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
ELF_DEBUG = HERE / "firmware" / "build" / "fw_debug.elf"
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


def fresh(debug=False, **kw):
    b = Board(str(ELF_DEBUG if debug else ELF), **kw)
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


def _stream(c, name, trim, cfg=(), seconds=1.05, debug=False):
    b = c.board = fresh(debug=debug)
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
    b = _stream(c, "b_default_trim_1005", 1005)
    w = [(t, r, v) for t, r, v in b.pmic.writes if r == 0x0E]
    c.note("Buck2 VOUT writes: " + ", ".join(f"{t*1e3:.1f} ms 0x{v:02X}" for t, r, v in w))
    clean(c, b)


def stress_3tx(c):
    cfg = ["chirpReset", "chirpCfg 0 0 0 0 0 0 0 7", "frameCfg 0 0 64 0 100"]
    n = fresh()
    n.preflash()
    n.plug_usb()
    booted(n)
    for line in cfg:
        n.cli(line)
    out = n.cli("sensorStart", 0.3)
    c.expect("Error -53" in out, "three TX at once refused by default (radar.tx3_enable 0)")
    b = _stream(c, "c_3tx_simultaneous_trim_1005", 1005, ["cfg radar.tx3_enable 1"] + cfg)
    clean(c, b)


def guards(c):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    c.expect("Error" in b.cli("pmicTrim 1025"), "pmicTrim 1025 refused (window 1000-1005 mV)")
    c.expect("Error" in b.cli("buck2 900"), "buck2 900 refused")
    c.expect("ignored" in b.cli("debugUnlock") and "ignored" in b.cli("pmicWrite 0x0E 0x30"),
             "normal build has no raw PMIC write (debugUnlock, pmicWrite ignored)")
    c.expect(b.pmic.regs[0x0E] == 0x4D, "Buck2 untouched")
    clean(c, b)


def bad_write(c):
    """What a raw PMIC write can do (debug build): Buck2 to 855 mV."""
    b = c.board = fresh(debug=True)
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
    b.cli("pmicTrim 1005")
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
    b.cli("pmicTrim 1005")
    b.cli("sensorStart", 0.3)
    b.run(0.25)
    trimmed = b.pmic.regs[0x0E]
    n0 = len(b.car_uart.data)
    b.carrier_reset(True)
    b.run(1e-3)
    b.carrier_reset(False)
    ok = booted(b, port=b.car_uart, timeout=0.3, start=n0)
    txt = b.car_uart.data[n0:].decode("latin-1")
    c.expect(trimmed == 0x4E, "streaming with Buck2 at 1005 mV (0x4E)")
    c.expect(ok and "registers kept" in txt and "Buck2 1005 mV" in txt,
             "HOST_NRESET pulse mid-stream: radar resets, PMIC keeps 1005 mV, firmware notices at boot")
    c.expect("Buck2 back to 1000" in txt and b.pmic.regs[0x0E] == 0x4D, "firmware puts Buck2 back to 1000 mV")
    clean(c, b)


def usb_dropout(c):
    b = c.board = fresh()
    b.preflash()
    b.plug_usb()
    booted(b)
    b.cli("pmicTrim 1005")
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
        b.cli("pmicTrim 1005")
        b.cli("sensorStart", 0.3)
        b.run(0.15)
    clean(c, b)


# ------------------------------------------------------------------------------ thermal and host limits
# The board's thermal time constant is about 150 s (sim/bss.py). To keep runs short the thermal
# scenarios compress it to 2 s and the firmware's hold time from its 20 s default to 300 ms, the
# same ratio, so the governor sees the same dynamics 75 times faster. The temperature read interval
# goes to its minimum of 100 ms (7.5 s at real speed, coarser than the 1 s default).
TAU_FAST = 2.0
RTH_BARE = 37.4          # K/W, no heat sink (thermal_summary.txt: 46.7 K at 1.25 W)
HEAVY = ["chirpReset", "chirpCfg 0 0 0 0 0 0 0 3", "chirpCfg 1 1 0 0 0 0 0 6", "chirpCfg 2 2 0 0 0 0 0 5",
         "frameCfg 0 2 43 0 33", "cfg thermal.hold_ms 300", "cfg thermal.read_ms 100"]
# 2 TX per chirp, 129 chirps every 33 ms: 1.49 W


THERMAL_RUNS = {}


def plot_thermal():
    """Radar temperature in the two governor scenarios, with the thresholds."""
    if not THERMAL_RUNS:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    for (label, tr), color in zip(THERMAL_RUNS.items(), ("#2a78d6", "#eb6834")):
        ax.plot([t for t, _ in tr], [x for _, x in tr], color=color, lw=1.4, label=label)
    for y, txt in ((90, "throttle 90 C"), (100, "stop 100 C"), (105, "junction limit 105 C")):
        ax.axhline(y, color="#6b6b6b", lw=0.9, ls="--")
        ax.annotate(txt, (0.995, y), xycoords=("axes fraction", "data"), ha="right", va="bottom", fontsize=8,
                    color="#4a4a4a")
    ax.set_xlabel(f"simulated time (s); thermal time constant compressed to {TAU_FAST:.0f} s from about 150 s")
    ax.set_ylabel("radar temperature (C)")
    ax.set_title("Thermal governor: 2 TX per chirp, 129 chirps every 33 ms (1.49 W unthrottled)", fontsize=10,
                 loc="left")
    ax.grid(color="#e4e4e4", lw=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    fig.tight_layout()
    fig.savefig(RES / "thermal_governor.png")
    plt.close(fig)


def hot_board(c, ambient, rth=None, cfg=HEAVY, start=True):
    b = c.board = fresh(ambient=ambient, rth=rth, thermal_tau=TAU_FAST)
    b.preflash()
    b.plug_usb()
    booted(b)
    for line in cfg:
        c.expect("Done" in b.cli(line), f"'{line}'")
    if start:
        c.expect("Done" in b.cli("sensorStart", 0.3), "sensorStart")
    return b


def thermal_lines(b, start=0):
    return [l.strip() for l in b.eci.data[start:].decode("latin-1").splitlines() if l.startswith("thermal:")]


def tj_max(b, t_from=0.0):
    return max((x for t, x in b.bss.temp_trace if t >= t_from), default=b.bss.ambient)


def thermal_throttle(c):
    b = hot_board(c, 55.0)
    c.note(f"ambient 55 C, heat sink fitted: unthrottled the radar would settle at "
           f"{55 + b.bss.rth * 1.485:.0f} C")
    b.run(10.0)
    c.note("firmware: " + " | ".join(thermal_lines(b)))
    fr = b.frames_from(b.sci_port)
    tel = fr[-1]["telemetry"]
    c.expect(tel["throttle_level"] >= 1 and tel["safe_state"] == 1,
             f"still streaming, throttled to level {tel['throttle_level']} (frame period x{1 << tel['throttle_level']})")
    c.expect(tj_max(b) < 91.0, f"radar peaks at {tj_max(b):.1f} C, settles at {b.bss.tj:.1f} C (throttle at 90 C)")
    c.expect(not any("stop" in l for l in thermal_lines(b)), "never had to stop")
    c.expect(len(fr) > 150, f"{len(fr)} packets in 10 s: the host keeps getting data, at a lower rate")
    THERMAL_RUNS["55 C ambient, heat sink: throttles once"] = b.bss.temp_trace
    clean(c, b)


def thermal_stop(c):
    b = hot_board(c, 66.0, rth=RTH_BARE)
    c.note(f"ambient 66 C, no heat sink: even idle the radar sits at {66 + RTH_BARE * b.bss.idle_power():.0f} C")
    b.run(8.0)
    lines = thermal_lines(b)
    c.note("firmware: " + " | ".join(lines))
    c.expect(any("stop temperature" in l for l in lines) and not b.bss.running,
             "radar stopped at 100 C after throttling to level 2")
    c.expect(tj_max(b) < 100.5, f"peak {tj_max(b):.2f} C (stop at 100, junction limit 105)")
    c.expect("stopped (hot)" in b.cli("thermal"), "stays stopped while the radar is above the resume temperature (90 C)")
    # the room cools down
    b.bss._thermal_update()
    b.bss.ambient = 55.0
    n0 = len(b.eci.data)
    t0 = b.sim.t
    b.run(8.0)
    lines = thermal_lines(b, n0)
    c.note("after ambient drops to 55 C: " + " | ".join(lines))
    c.expect(any("restarted" in l for l in lines) and b.bss.running, "restarts by itself once under 90 C, at level 2")
    c.expect(tj_max(b, t0) < 100.5, f"peak after restart {tj_max(b, t0):.1f} C")
    THERMAL_RUNS["66 C, no heat sink (55 C from 8 s): stops, restarts"] = b.bss.temp_trace
    clean(c, b)


def thermal_hot_start(c):
    b = hot_board(c, 66.0, rth=RTH_BARE, start=False)
    b.bss.tj = 95.0
    out = b.cli("sensorStart", 0.3)
    c.expect("too warm to start" in out and not b.bss.running, "sensorStart at 95 C: waits instead of transmitting")
    b.run(2.0)
    c.expect(not b.bss.running and not b.bss.frames, "no frames while the radar stays over 90 C")
    c.expect("Done" in b.cli("sensorStop") and "idle" in b.cli("thermal"), "sensorStop cancels the wait")
    clean(c, b)


def host_limits(c):
    b = hot_board(c, 50.0, cfg=[], start=False)
    out = b.cli("cfg")
    c.note("cfg: " + " | ".join(l.strip() for l in out.splitlines()[1:-2]))
    for line, code, what in (("cfg thermal.stop_c 105", -51, "stop temperature above 100 C"),
                             ("cfg thermal.throttle_c 95", -51, "throttle temperature above 90 C"),
                             ("cfg thermal.hold_ms 50", -51, "hold time under 100 ms"),
                             ("cfg pmic.trim_mv 25", -51, "1V0 trim over 5 mV"),
                             ("cfg thermal.read_ms 10000", -51, "temperature read interval over 5 s"),
                             ("cfg radar.tx3_enable 2", -51, "tx3_enable other than 0 or 1"),
                             ("cfg thermal.throttle_c 86", -52, "throttle closer than 3 K to release (85 C)"),
                             ("cfg thermal.governor 0", -50, "unknown key (no switch to turn the governor off)")):
        c.expect(f"Error {code}" in b.cli(line), f"refused: {what}")
    for line, code, what in (("frameCfg 0 2 43 0 20", -54, "chirps over 50 % of the frame"),
                             ("frameCfg 0 2 1 0 5", -55, "frame period under 10 ms")):
        b.cli(line)
        c.expect(f"Error {code}" in b.cli("sensorStart", 0.3), f"sensorStart refused: {what}")
    # a host may tighten the limits, and the governor then uses them
    c.note(f"ambient 50 C, heat sink: radar idle settles at {50 + b.bss.rth * b.bss.idle_power():.1f} C")
    for line in ("cfg thermal.release_c 65", "cfg thermal.throttle_c 70", "cfg thermal.resume_c 71",
                 "cfg thermal.stop_c 74", "cfg thermal.hold_ms 300"):
        c.expect("Done" in b.cli(line), f"accepted: '{line}'")
    for line in HEAVY[:-2] + HEAVY[-1:]:
        b.cli(line)
    b.cli("sensorStart", 0.3)
    b.run(6.0)
    lines = thermal_lines(b)
    c.note("firmware: " + " | ".join(lines))
    c.expect(any("level 1" in l for l in lines) and any("stop temperature" in l for l in lines),
             "with the host's limits it throttles at 70 C and stops at 74 C")
    c.expect(tj_max(b) < 74.5, f"peak {tj_max(b):.1f} C")
    clean(c, b)


def pmic_thermal(c):
    b = hot_board(c, 25.0)
    b.run(0.5)
    b.bss.pmic_extra = 120.0      # stand-in for a hot carrier next to the PMIC
    b.run(1.5)
    lines = thermal_lines(b)
    c.expect(any("PMIC thermal warning" in l for l in lines) and not b.bss.running,
             "LP87524 TDIE_WARN (die over 137 C) stops the radar within 10 frames")
    b.bss.pmic_extra = 0.0
    b.run(1.0)
    c.expect(b.bss.running and any("restarted" in l for l in thermal_lines(b)), "restarts once the warning clears")
    c.note("firmware: " + " | ".join(thermal_lines(b)))
    clean(c, b)


def temp_fail(c):
    b = hot_board(c, 25.0)
    b.run(0.3)
    b.bss.fail_temp = True
    b.run(0.5)
    lines = thermal_lines(b)
    c.expect(any("unreadable" in l for l in lines) and not b.bss.running,
             "three failed temperature reads stop the radar: no reading, no RF")
    c.expect("stopped (fault)" in b.cli("thermal"), "state 'stopped (fault)' until the host starts it again")
    b.bss.fail_temp = False
    c.expect("Done" in b.cli("sensorStart", 0.3) and b.bss.running, "sensorStart after the fault runs again")
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
    ("stream_trim", "Same with the 1V0 trim to 1005 mV while running", stream_trim),
    ("stress_3tx", "Three TX at once: refused by default, runs once the host enables it", stress_3tx),
    ("guards", "Firmware limits on PMIC writes", guards),
    ("bad_pmic_write", "Debug build: raw write puts Buck2 at 855 mV", bad_write),
    ("flash_uart", "UniFlash-style programming in SOP 101, then boot", flash_uart),
    ("flash_no_qe", "Programmed without the QE bit", flash_no_qe),
    ("flash_reset_wip", "RESET pressed while the QE write is still busy", flash_reset_wip),
    ("host_disable", "Carrier pulses HOST_DISABLE while streaming with trim", host_disable),
    ("carrier_reset", "Carrier pulses HOST_NRESET while streaming with trim", carrier_reset),
    ("usb_dropout", "USB dropouts of 0.1 ms and 1 ms while streaming with trim", usb_dropout),
    ("thermal_throttle", "55 C ambient, heavy profile: governor halves the frame rate", thermal_throttle),
    ("thermal_stop", "66 C ambient, no heat sink: stop at 100 C, restart when cooler", thermal_stop),
    ("thermal_hot_start", "sensorStart while the radar is already at 95 C", thermal_hot_start),
    ("host_limits", "Host config: what it can change, what it cannot", host_limits),
    ("pmic_thermal", "PMIC die temperature warning while streaming", pmic_thermal),
    ("temp_fail", "Radar temperature reads fail while streaming", temp_fail),
    ("uart_baud", "UART baud rates the SCI can make", baud_table),
]


def main(names):
    if not (ELF.exists() and ELF_DEBUG.exists()):
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
        plot_thermal()
        (RES / "cosim_summary.txt").write_text("\n".join(summary) + "\n")
    return n_fail


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:]) else 0)
