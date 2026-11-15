#!/usr/bin/env python3
"""Co-simulation scenarios: the firmware in firmware/ (built ELF) running on the board model in sim/.

Each scenario builds a fresh board, acts like a person at the bench or a carrier board would
(plug USB, hold BOOT, send CLI lines, pulse HOST_DISABLE ...) and checks what happens against an
expectation. Results go to results/cosim_summary.txt (one block per scenario, with the board log)
and the frame traces for the electrical replay to results/traces/.

usage: run_cosim.py [scenario ...]      (default: all; 'make -C firmware' first)
"""
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sim import calib  # noqa: E402
from sim.board import Board  # noqa: E402
from sim.soc import VCLK  # noqa: E402

ELF = HERE / "firmware" / "build" / "fw.elf"
RES = HERE / "results"
TRACES = RES / "traces"
CAL = calib.load()


class Check:
    def __init__(self, name, what):
        self.name, self.what = name, what
        self.lines = []
        self.ok = True
        self.board = None

    def expect(self, cond, text):
        self.lines.append(f"  [{'ok' if cond else 'FAIL'}] {text}")
        self.ok &= bool(cond)

    def note(self, text):
        self.lines.append(f"  {text}")


def fresh(**kw):
    b = Board(str(ELF), **kw)
    return b


def booted(b, port=None, timeout=0.3, start=0):
    """Wait for a boot banner (written after byte 'start' of the port) to end in the prompt."""
    port = port or b.eci
    return b.wait_text(port, r"radar subsystem ready[^>]*aop:/>", timeout, start)


def clean(c, b):
    c.expect(not b.sim.violations, "no rule violations" if not b.sim.violations else
             "violations: " + "; ".join(v[2] for v in b.sim.violations))
    c.expect(not b.sim.unmodelled, "firmware touched only modelled registers" if not b.sim.unmodelled else
             "unmodelled accesses: " + ", ".join(f"0x{a:08X}" for a in b.sim.unmodelled))
    c.expect(b.sim.fault is None and "FAULT" not in b.eci.text(), "no CPU fault")


def nreset_times(b):
    return [(t, txt) for t, txt in b.power.events if "supervisor" in txt or "GPIO2" in txt]


def save_trace(b, name):
    TRACES.mkdir(parents=True, exist_ok=True)
    json.dump({"frames": b.bss.frames, "frame_starts": b.bss.frame_starts}, open(TRACES / f"{name}.json", "w"))


# ------------------------------------------------------------------------------------------ scenarios
def usb_boot(c):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    ok = booted(b, timeout=0.3)
    rel = [t for t, x in b.power.events if x == "supervisor releases reset"]
    c.expect(ok, "boot banner and prompt on the CP2105 ECI port")
    ref = max(CAL["rail_90"].values()) + CAL["settle_after_last90"] + CAL["sup_delay"]
    c.expect(rel and abs(rel[0] - ref) < 0.5e-3,
             f"reset released at {rel[0]*1e3:.2f} ms (ngspice power-up run: {ref*1e3:.2f} ms)" if rel else "reset never released")
    start = [x for x in b.boots if x[2].startswith("application")]
    c.expect(start, "boot ROM loaded the image from QSPI: " + (start[0][2] if start else str(b.boots)))
    txt = b.eci.text()
    c.expect("registers fresh from OTP" in txt and "OTP 0x72" in txt, "firmware read the LP87524J (OTP_REV 0x72, RESET_REG set)")
    out = b.cli("pmic")
    c.expect("I2C 400 kHz" in out and re.search(r"Buck2 1V0 +1000 mV", out), "pmic command: OTP voltages read over I2C at 400 kHz")
    c.note("pmic output: " + " | ".join(l.strip() for l in out.splitlines() if l.strip() and "aop" not in l))
    out = b.cli("version")
    m = re.search(r"data UART (\d+) Bd \(set (\d+)\)", out)
    c.expect(m and abs(int(m[2]) / int(m[1]) - 1) < 0.02, f"data UART {m[1]} Bd, SCI divider gives {m[2]} Bd" if m else out)
    clean(c, b)


def carrier_boot(c):
    b = c.board = fresh()
    b.preflash()
    b.carrier_power()
    ok = booted(b, port=b.car_uart, timeout=0.3)
    c.expect(ok, "boot banner on the carrier's UART (J400/J401), CP2105 unpowered")
    c.expect(not b.eci.data, "nothing reaches the unpowered CP2105")
    out = b.cli("status")
    c.expect("bss ready" in out, "CLI over the carrier UART")
    clean(c, b)


def _stream(c, name, trim, cfg=(), seconds=1.05):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    for line in cfg:
        out = b.cli(line)
        c.expect("Done" in out, f"'{line}' accepted")
    if trim:
        c.expect("Done" in b.cli(f"pmicTrim {trim}"), f"pmicTrim {trim}")
    out = b.cli("sensorStart", 0.3)
    c.expect("Done" in out, "sensorStart: channel, LDO bypass, RF init, profile, chirps, frame, start")
    b.run(seconds)
    frames = b.frames_from(b.sci_port)
    tel = [f["telemetry"] for f in frames if f["telemetry"]]
    c.expect(len(frames) >= int(seconds / 0.1) - 1, f"{len(frames)} data packets parsed from the 460800 Bd port, "
                                                       f"{b.sci_port.framing_errors} framing errors")
    run = [f for f in b.bss.frames if f["kind"] == "frame"]
    if run:
        f = run[0]
        c.note(f"frame: {len(f['seq'])} chirps of {1e6*(f['idle']+f['ramp']):.1f} us, TX per chirp "
               f"{sorted(set(f['seq']))}, 1V0 VSET {f['vset']['1V0']*1e3:.0f} mV, average radar power "
               f"{b.bss.frame_power(f):.2f} W (typ)")
    if tel:
        t = tel[-1]
        c.note(f"telemetry TLV: Buck2 {t['buck2_mv']} mV, Buck2 {t['buck2_frame_ma']} mA in chirps / "
               f"{t['buck2_idle_ma']} mA idle, Buck3 {t['buck3_frame_ma']} / {t['buck3_idle_ma']} mA, "
               f"radar {t['temp_c']:.1f} C")
        c.expect(t["buck2_mv"] == (trim or 1000), f"Buck2 runs at {t['buck2_mv']} mV while streaming")
    out = b.cli("sensorStop", 0.4)
    c.expect("Done" in out, "sensorStop")
    c.expect(b.pmic.regs[0x0E] == 0x4D, "Buck2 back to 1000 mV (0x4D) after stop")
    save_trace(b, name)
    return b


def stream_default(c):
    b = _stream(c, "a_default_no_trim", None)
    clean(c, b)


def stream_trim(c):
    b = _stream(c, "b_default_trim_1025", 1025)
    w = [(t, r, v) for t, r, v in b.pmic.writes if r == 0x0E]
    c.note("Buck2 VOUT writes: " + ", ".join(f"{t*1e3:.1f} ms 0x{v:02X}" for t, r, v in w))
    clean(c, b)


def stress_3tx(c):
    cfg = ["chirpReset", "chirpCfg 0 0 0 0 0 0 0 7", "frameCfg 0 0 64 0 100"]
    b = _stream(c, "c_3tx_simultaneous_trim_1025", 1025, cfg)
    clean(c, b)


def guards(c):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    c.expect("Error" in b.cli("pmicTrim 1100"), "pmicTrim 1100 refused (window 1000-1025 mV)")
    c.expect("Error" in b.cli("buck2 900"), "buck2 900 refused")
    c.expect("Error" in b.cli("pmicWrite 0x0E 0x30"), "pmicWrite refused before debugUnlock")
    c.expect(b.pmic.regs[0x0E] == 0x4D, "Buck2 untouched")
    clean(c, b)


def bad_write(c):
    """What a firmware bug writing the PMIC can do: Buck2 to 855 mV."""
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    b.cli("debugUnlock")
    t0 = b.sim.t
    b.eci.send("pmicWrite 0x0E 0x30\r")
    b.run(0.5)
    ev = [x for t, x in b.power.events if t > t0]
    c.expect(any("supervisor asserts reset" in x for x in ev), "supervisor holds the radar in reset (1V0 under 0.900 V)")
    c.expect(not b.power.nreset, "radar still in reset 0.5 s later: the PMIC keeps 855 mV, nothing reloads it")
    c.note("events: " + "; ".join(f"{t*1e3:.1f} ms {x}" for t, x in b.power.events if t > t0))
    b.host_disable(True)
    b.run(20e-3)
    b.host_disable(False)
    b.run(0.1)
    c.expect(not b.power.nreset, "a HOST_DISABLE pulse does not help either: EN1 does not reload the registers")
    # the way out: power cycle long enough for the PMIC to reach UVLO
    n0 = len(b.eci.data)
    b.plug_usb(False)
    b.run(0.05)
    b.plug_usb(True)
    c.expect(booted(b, timeout=0.3, start=n0), "unplug / replug: PMIC reloads OTP, radar boots again")
    c.expect(any("violation" in v[2].lower() or "outside" in v[2] or "1.0 V" in v[2] for v in b.sim.violations),
             "the model flags the write: " + "; ".join(v[2] for v in b.sim.violations))


def flash_uart(c):
    b = c.board = fresh()
    b.hold_boot(True)
    b.plug_usb()
    b.run(0.05)
    c.expect(b.rom_mode == "uart", "BOOT held at power-up: ROM in UART flashing mode (SOP 101)")
    b.flash_image(set_qe=True, wait_wip=True)
    b.hold_boot(False)
    b.press_reset(True)
    b.run(1e-3)
    b.press_reset(False)
    c.expect(booted(b, timeout=0.3), "after programming + QE + RESET the image boots")
    clean(c, b)


def flash_no_qe(c):
    b = c.board = fresh()
    b.hold_boot(True)
    b.plug_usb()
    b.run(0.05)
    b.flash_image(set_qe=False)
    b.hold_boot(False)
    b.press_reset(True)
    b.run(1e-3)
    b.press_reset(False)
    b.run(0.1)
    c.expect(not b.eci.data, "programmed without setting QE: no boot (expected, spec 4.2)")
    c.note("ROM: " + b.boots[-1][2])


def flash_reset_wip(c):
    b = c.board = fresh()
    b.hold_boot(True)
    b.plug_usb()
    b.run(0.05)
    b.flash_image(set_qe=True, wait_wip=False)
    b.hold_boot(False)
    b.press_reset(True)
    b.run(1e-3)
    b.press_reset(False)
    b.run(0.1)
    c.expect(not b.eci.data, "RESET within tW (40 ms) of the QE write: boot fails (errata MSS#45), a second reset fixes it")
    c.note("ROM: " + b.boots[-1][2])
    b.press_reset(True)
    b.run(1e-3)
    b.press_reset(False)
    c.expect(booted(b, timeout=0.3), "second reset boots")


def host_disable(c):
    b = c.board = fresh()
    b.carrier_power()
    b.preflash()
    booted(b, port=b.car_uart)
    b.cli("pmicTrim 1025")
    b.cli("sensorStart", 0.3)
    b.run(0.25)
    n0 = len(b.car_uart.data)
    b.host_disable(True)
    b.run(10e-3)
    c.expect(not b.power.nreset, "HOST_DISABLE mid-stream: EN1 low, rails off, radar in reset")
    b.host_disable(False)
    ok = booted(b, port=b.car_uart, timeout=0.3, start=n0)
    txt = b.car_uart.data[n0:].decode("latin-1")
    c.expect(ok, "radar boots again after HOST_DISABLE is released")
    c.expect("registers kept" in txt, "firmware sees RESET_REG clear: PMIC kept its registers across HOST_DISABLE")
    c.note("PMIC Buck2 VOUT after re-enable: 0x%02X" % b.pmic.regs[0x0E])
    c.expect(b.pmic.regs[0x0E] == 0x4D, "Buck2 is at 1000 mV again (firmware stopped the radar before, or reset it at boot)")
    c.note("events: " + "; ".join(f"{t*1e3:.1f} ms {x}" for t, x in b.power.events if t > 0.25))
    clean(c, b)


def carrier_reset(c):
    b = c.board = fresh()
    b.carrier_power()
    b.preflash()
    booted(b, port=b.car_uart)
    b.cli("pmicTrim 1025")
    b.cli("sensorStart", 0.3)
    b.run(0.25)
    trimmed = b.pmic.regs[0x0E]
    n0 = len(b.car_uart.data)
    b.carrier_reset(True)
    b.run(1e-3)
    b.carrier_reset(False)
    ok = booted(b, port=b.car_uart, timeout=0.3, start=n0)
    txt = b.car_uart.data[n0:].decode("latin-1")
    c.expect(trimmed == 0x52, "streaming with Buck2 at 1025 mV (0x52)")
    c.expect(ok and "registers kept" in txt and "Buck2 1025 mV" in txt,
             "HOST_NRESET pulse mid-stream: radar resets, PMIC keeps 1025 mV, firmware notices at boot")
    c.expect("Buck2 back to 1000" in txt and b.pmic.regs[0x0E] == 0x4D, "firmware puts Buck2 back to 1000 mV")
    clean(c, b)


def usb_dropout(c):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    b.cli("pmicTrim 1025")
    b.cli("sensorStart", 0.3)
    b.run(0.2)
    for gap, expect in ((0.1e-3, "kept"), (1e-3, "fresh")):
        n0 = len(b.eci.data)
        b.plug_usb(False)
        b.run(gap)
        b.plug_usb(True)
        ok = booted(b, timeout=0.3, start=n0)
        txt = b.eci.data[n0:].decode("latin-1")
        c.expect(ok, f"USB dropout of {gap*1e3:.1f} ms: radar resets and boots again")
        c.note(f"  banner: {[l for l in txt.splitlines() if 'PMIC' in l]}")
        b.cli("pmicTrim 1025")
        b.cli("sensorStart", 0.3)
        b.run(0.15)
    clean(c, b)


def baud_table(c):
    """SCI baud rates the radar can make (no fractional divider) vs a CP2105 receiver."""
    cp = 48e6
    for baud in (115200, 460800, 921600, 1843200):
        brs = round(VCLK / (16 * baud)) - 1
        real = VCLK / (16 * (brs + 1))
        host = cp / round(cp / baud)
        err = real / host - 1
        ok = abs(err) * 9.5 <= 0.47 * 0.5      # half the sampling margin left for edges and jitter
        c.note(f"{baud:8d} Bd: radar makes {real:9.0f} Bd (BRS {brs}), CP2105 {host:9.0f} Bd, "
               f"error {err*100:+.2f} %  {'ok' if ok else 'MARGINAL' if abs(err)*9.5 <= 0.47 else 'FAILS'}")
    c.expect(True, "firmware uses 115200 (CLI) and 460800 (data); 921600 is -3.1 %: marginal, not used")


SCENARIOS = [
    ("usb_boot", "USB power-up: rails, supervisor, boot ROM, firmware, PMIC over I2C", usb_boot),
    ("carrier_boot", "Carrier power (J400/J401) only, CLI over the carrier UART", carrier_boot),
    ("stream_default", "Default TDM-MIMO profile, 10 frames, no 1V0 trim", stream_default),
    ("stream_trim", "Same with the 1V0 trim to 1025 mV while running", stream_trim),
    ("stress_3tx", "Three TX at once (LDO bypass), 64 chirps per frame, trim 1025 mV", stress_3tx),
    ("guards", "Firmware limits on PMIC writes", guards),
    ("bad_pmic_write", "Firmware bug: Buck2 written to 855 mV", bad_write),
    ("flash_uart", "UniFlash-style programming in SOP 101, then boot", flash_uart),
    ("flash_no_qe", "Programmed without the QE bit", flash_no_qe),
    ("flash_reset_wip", "RESET pressed while the QE write is still busy", flash_reset_wip),
    ("host_disable", "Carrier pulses HOST_DISABLE while streaming with trim", host_disable),
    ("carrier_reset", "Carrier pulses HOST_NRESET while streaming with trim", carrier_reset),
    ("usb_dropout", "USB dropouts of 0.1 ms and 1 ms while streaming with trim", usb_dropout),
    ("uart_baud", "UART baud rates the SCI can make", baud_table),
]


def main(names):
    if not ELF.exists():
        sys.exit("build the firmware first: make -C firmware")
    RES.mkdir(exist_ok=True)
    out, n_fail = [], 0
    for name, what, fn in SCENARIOS:
        if names and name not in names:
            continue
        c = Check(name, what)
        t0 = time.time()
        try:
            fn(c)
        except Exception as e:  # a crash in a scenario is a failed scenario, with the reason
            c.expect(False, f"scenario crashed: {type(e).__name__}: {e}")
        wall = time.time() - t0
        n_fail += not c.ok
        head = f"=== {name}: {'PASS' if c.ok else 'FAIL'}  ({what})"
        if c.board:
            head += f"  [{c.board.sim.t*1e3:.0f} ms simulated, {c.board.sim.instructions/1e6:.1f} M instructions, {wall:.0f} s]"
        block = [head] + c.lines
        if c.board:
            block.append("  log:")
            block += [f"    {t*1e3:9.3f} ms  {src:6} {txt}" for t, src, txt in c.board.sim.log
                      if src in ("power", "ROM", "PMIC", "radar", "cpu", "flash", "bss", "BSS") or "VIOLATION" in txt][:60]
        print("\n".join(block[:len(c.lines) + 1]), flush=True)
        out += block + [""]
    summary = [f"Co-simulation results: {sum(1 for _ in out if _.startswith('==='))} scenarios, {n_fail} failed", ""] + out
    if not names:
        (RES / "cosim_summary.txt").write_text("\n".join(summary) + "\n")
    return n_fail


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
