#!/usr/bin/env python3
"""Run the board-level power / reset scenarios in ngspice and check them.

Usage: python3 simulation/run_power.py   (needs ngspice >= 40 on PATH, numpy)
Writes simulation/results/power_*.txt and prints a pass/fail summary.
"""
import pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SP = HERE / "spice"
RES = HERE / "results"
RES.mkdir(exist_ok=True)

SIGS = ["SYS_5V", "PMIC_ENABLE", "3V3_R", "1V2_R", "1V0_R", "1V8_R",
        "AOP_NRESET", "PMIC_PGOOD", "AOP_UART_RX_BALL", "USB_VBUS", "PWR_IN"]
# radar recommended operating minimums (SWRS237C 7.4)
RAIL_MIN = {"3V3_R": 3.13, "1V2_R": 1.14, "1V0_R": 0.95, "1V8_R": 1.71}
RAIL_NOM = {"3V3_R": 3.30, "1V2_R": 1.20, "1V0_R": 1.00, "1V8_R": 1.80}
NRESET_VIH, NRESET_VIL = 1.57, 0.65


def deck(name, tstop, sources, params):
    p = dict(r604="1e9", c605=0, rdis=1, rio="10k", tcp=0)
    p.update(params)
    ptxt = "\n".join(f".param {k}={v}" for k, v in p.items())
    c605 = "" if p["c605"] else "* C605 DNP\n"
    body = (SP / "board_power.inc").read_text()
    if not p["c605"]:
        body = body.replace("C605    AOP_NRESET 0 {c605}", "* C605 DNP")
    out = RES / f"power_{name}.txt"
    return f"""* scenario {name}
{ptxt}
{body}
{sources}
.options method=gear maxord=2 reltol=1e-3 abstol=1e-9 vntol=1e-6 itl4=500 rshunt=1e12
.tran 2u {tstop} 0 2u uic
.control
run
wrdata {out} {' '.join('v(' + s + ')' for s in SIGS)}
.endc
.end
""", out


def run(name, tstop, sources, **params):
    txt, out = deck(name, tstop, sources, params)
    cir = SP / f"_power_{name}.cir"
    cir.write_text(txt)
    r = subprocess.run(["ngspice", "-b", str(cir)], cwd=SP, capture_output=True, text=True)
    if r.returncode or not out.exists():
        print(r.stdout[-3000:], r.stderr[-3000:])
        sys.exit(f"ngspice failed for {name}")
    d = np.loadtxt(out)
    t = d[:, 0]
    return t, {s: d[:, 2 * i + 1] for i, s in enumerate(SIGS)}


def first(t, cond, after=0.0):
    idx = np.where(cond & (t >= after))[0]
    return t[idx[0]] if len(idx) else None


def ms(x):
    return "never" if x is None else f"{x*1e3:7.3f} ms"


USB_PLUG = """
VUSB_SRC usbsrc 0 PWL(0 0 1m 0 1.1m {vusb} {tun} {vusb} {tun2} 0)
RCAB usbsrc usbl 0.15
LCAB usbl USB_CONN_VBUS 0.5u
VPWR PWR_IN_SRC 0 0
RPWR PWR_IN_SRC PWR_IN 1e9
VDIS HOST_DISABLE 0 0
VHR HOST_NRESET_DRV 0 0
RHR HOST_NRESET_DRV HOST_NRESET 1e9
"""

CARRIER = """
VPWR PWR_IN_SRC 0 PWL(0 0 1m 0 1.1m 5.0)
RPWR PWR_IN_SRC PWR_IN 0.05
VUSB_SRC USB_CONN_VBUS 0 0
* HOST_DISABLE: 3.3 V push-pull from 40 ms to 55 ms
VDIS hd 0 PWL(0 0 40m 0 40.01m 3.3 55m 3.3 55.01m 0)
RDIS hd HOST_DISABLE 50
* carrier open-drain reset pulse on J400.8 at 100-101 ms
SHR HOST_NRESET 0 hrc 0 SWOD
VHRC hrc 0 PWL(0 0 100m 0 100.01m 1 101m 1 101.01m 0)
.model SWOD SW(VT=0.5 VH=0.1 RON=20 ROFF=1e9)
"""


def rails_settled(t, v, after):
    """time at which every rail is inside the PMIC regulation band (+-2 %, at least +-20 mV)"""
    ok = np.ones_like(t, bool)
    for r, n in RAIL_NOM.items():
        ok &= np.abs(v[r] - n) < max(0.02, 0.02 * n)
    return first(t, ok, after)


def report_powerup(tag, t, v, lines, t0=0.0):
    st = {r: first(t, v[r] > 0.9 * RAIL_NOM[r], t0) for r in RAIL_NOM}
    order = sorted(st, key=lambda r: st[r] if st[r] is not None else 9)
    t_en = first(t, v["PMIC_ENABLE"] > 1.2, t0)
    t_rel = first(t, v["AOP_NRESET"] > NRESET_VIH, t0)
    t_set = rails_settled(t, v, t0)
    lines.append(f"[{tag}] EN1 high at {ms(t_en)}")
    for r in order:
        lines.append(f"[{tag}]   {r:6} reaches 90 % at {ms(st[r])}")
    lines.append(f"[{tag}] all rails inside window at {ms(t_set)}")
    lines.append(f"[{tag}] AOP_NRESET released (>1.57 V) at {ms(t_rel)}"
                 + (f"  = {(t_rel-t_set)*1e3:.2f} ms after rails settled, {(t_rel-t_en)*1e3:.2f} ms after EN1"
                    if t_rel and t_set and t_en else ""))
    checks = []
    checks.append(("1V8_R up before 3V3_R (errata MSS#38A)",
                   st["1V8_R"] is not None and st["3V3_R"] is not None and st["1V8_R"] < st["3V3_R"]))
    checks.append(("reset released only after all rails settled",
                   t_rel is not None and t_set is not None and t_rel > t_set))
    # NRESET low while 3V3_R rising
    t33 = first(t, v["3V3_R"] > 1.0, t0)
    checks.append(("AOP_NRESET held low while 3V3_R ramps",
                   t33 is not None and np.all(v["AOP_NRESET"][(t >= t33) & (t <= (t_set or t33))] < NRESET_VIL)))
    return checks


def report_powerdown(tag, t, v, t_ev, lines):
    t_rst = first(t, v["AOP_NRESET"] < NRESET_VIL, t_ev)
    lines.append(f"[{tag}] AOP_NRESET below 0.65 V at {ms(t_rst)} ({(t_rst-t_ev)*1e6:.1f} us after event)" if t_rst else f"[{tag}] AOP_NRESET never asserted")
    t_out = {}
    for r, mn in RAIL_MIN.items():
        t_out[r] = first(t, v[r] < mn, t_ev)
        lines.append(f"[{tag}]   {r:6} below datasheet min ({mn} V) at {ms(t_out[r])}")
    core = min([t_out[r] for r in ("1V2_R", "1V0_R", "1V8_R") if t_out[r] is not None], default=None)
    ok = t_rst is not None and (core is None or t_rst <= core)
    out = [(f"{tag}: reset asserted before any core rail (1.2/1.0/1.8 V) leaves its operating range", ok)]
    if t_out["3V3_R"] is not None and t_rst is not None:
        lag = t_rst - t_out["3V3_R"]
        lines.append(f"[{tag}]   reset comes {lag*1e6:.0f} us after 3V3_R passes 3.13 V (supervisor threshold 3.07 V sits "
                     f"under the radar minimum so that tolerance cannot cause nuisance resets)" if lag > 0 else
                     f"[{tag}]   reset comes {-lag*1e6:.0f} us before 3V3_R passes 3.13 V")
        out.append((f"{tag}: reset within 1 ms of 3V3_R leaving its range", lag < 1e-3))
    return out


def main():
    lines, checks = [], []

    # A: USB power-up, then unplug at 60 ms
    t, v = run("usb", "100m", USB_PLUG.format(vusb=5.0, tun="80m", tun2="80.01m"))
    checks += [("A " + c, ok) for c, ok in report_powerup("A usb", t, v, lines)]
    pre = v["3V3_R"][(t > 1e-3) & (t < 5.5e-3)].max()
    lines.append(f"[A usb] max 3V3_R before Buck0 starts (CP2105 back-power through R208): {pre:.3f} V")
    checks += report_powerdown("A usb unplug", t, v, 80e-3, lines)

    # B: carrier power-up, HOST_DISABLE 40-55 ms, carrier reset pulse 75-76 ms
    t, v = run("carrier", "130m", CARRIER)
    checks += [("B " + c, ok) for c, ok in report_powerup("B carrier", t, v, lines)]
    checks += report_powerdown("B HOST_DISABLE", t, v, 40e-3, lines)
    checks += [("B " + c, ok) for c, ok in report_powerup("B re-enable", t, v, lines, t0=55.02e-3)]
    m = (t > 100.2e-3) & (t < 100.9e-3)
    vlow = v["AOP_NRESET"][m].max()
    lines.append(f"[B carrier reset] AOP_NRESET during J400.8 low: {vlow:.3f} V (needs < 0.65 V)")
    checks.append(("B carrier reset through R417 reaches radar VIL", vlow < NRESET_VIL))
    t_back = first(t, v["AOP_NRESET"] > NRESET_VIH, 101.01e-3)
    lines.append(f"[B carrier reset] released {ms(t_back)}")

    # C: option R604 fitted (PGOOD also resets, Rev 1.0 behaviour), USB plug + unplug
    t, v = run("r604", "100m", USB_PLUG.format(vusb=5.0, tun="80m", tun2="80.01m"), r604="0.001")
    checks += [("C R604 option " + c, ok) for c, ok in report_powerup("C R604 fitted", t, v, lines)]
    checks += report_powerdown("C R604 fitted unplug", t, v, 80e-3, lines)

    # D: back-power sensitivity: discharge resistor off / weak radar IO load / CP2105 TX timing
    for rd, rio in [(1, "1e6"), (0, "10k"), (0, "1e6")]:
        t, v = run(f"bp_rdis{rd}_rio{rio}", "8m", USB_PLUG.format(vusb=5.0, tun="1", tun2="1.01"), rdis=rd, rio=rio)
        pre = v["3V3_R"][(t > 1e-3) & (t < 5.5e-3)].max()
        t18 = first(t, v["1V8_R"] > 1.62)
        lines.append(f"[D back-power rdis={rd} rio={rio}] 3V3_R peak before Buck0 = {pre:.3f} V; 1V8_R up at {ms(t18)}")
    # E: short USB interruption (connector bump): VBUS gone for 1 ms at 60 ms, then back
    src = USB_PLUG.format(vusb=5.0, tun="60m", tun2="60.01m").replace(
        "60.01m 0)", "60.01m 0 61m 0 61.01m 5.0)")
    t, v = run("dip", "110m", src)
    bad = (v["AOP_NRESET"] > NRESET_VIH) & ((v["1V2_R"] < RAIL_MIN["1V2_R"]) | (v["1V8_R"] < RAIL_MIN["1V8_R"])
                                           | (v["1V0_R"] < RAIL_MIN["1V0_R"])) & (t > 60e-3)
    dur = bad.sum() * 2e-6
    tb = first(t, bad)
    lines.append(f"[E 1 ms USB dropout] AOP_NRESET above 1.57 V while core rails are below minimum: "
                 + (f"{dur*1e3:.2f} ms in total, first at {ms(tb)}" if dur else "never"))
    lines.append("[E] NOTE: depends on the LP87524 GPIO2/PGOOD pin state below UVLO and during READ OTP, "
                 "which the datasheet does not specify (modelled as released / high-impedance).")
    checks.append(("E reset stays asserted through a 1 ms 5 V dropout and re-start", dur == 0))
    report_powerup("E restart", t, v, lines, t0=61.02e-3)

    # D2: USB attached but rails held off (PMIC latched by OVP/thermal, or EN1 low) for 300 ms
    for rd, rio in [(1, "1e6"), (0, "1e6"), (0, "10k")]:
        src = USB_PLUG.format(vusb=5.0, tun="1", tun2="1.01").replace("VDIS HOST_DISABLE 0 0", "VDIS HOST_DISABLE 0 3.3")
        t, v = run(f"bpoff_rdis{rd}_rio{rio}", "300m", src, rdis=rd, rio=rio)
        lines.append(f"[D2 rails off, USB on, rdis={rd} rio={rio}] 3V3_R after 300 ms = {v['3V3_R'][-1]:.3f} V, "
                     f"radar RX ball = {v['AOP_UART_RX_BALL'][-1]:.3f} V, 1V8_R = {v['1V8_R'][-1]:.3f} V")

    out = "\n".join(lines) + "\n\nCHECKS\n" + "\n".join(f"  {'PASS' if ok else 'FAIL'}  {c}" for c, ok in checks)
    (RES / "power_summary.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
