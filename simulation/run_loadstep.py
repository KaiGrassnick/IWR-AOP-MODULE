#!/usr/bin/env python3
"""1.0 V rail (Buck2 -> FB600 -> radar) chirp load step and loop stability.

The LP87524J-Q1 compensation is internal and undocumented, so the controller is an
averaged current-mode model (inner current loop as a 0.25 us lag, PI outer loop)
CALIBRATED to TI's own datasheet number: 0.1 -> 2 A in 1 us with 22 uF + 22 uF POL,
no ferrite, gives about -40 mV (SNVSAW2B 6.5, TLDSR). The same controller is then put
on this board's 1.0 V network: 22 uF at the PMIC, FB600 ferrite, ~49 uF at the radar,
with FB_B2 sensing after the ferrite. Results are indicative, not a sign-off.
"""
import pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SP, RES = HERE / "spice", HERE / "results"
RES.mkdir(exist_ok=True)


def deck(kind, kp, fz, lfb, sense, ipk, tr=1e-6, ibase=0.1, network="board", tstop=400e-6):
    ki = kp * 2 * np.pi * fz
    if network == "ti":
        plant = """
* TI datasheet test: COUT 22 uF + CPOL 22 uF, no ferrite
C1 raw c1e 22u
R1 c1e c1l 3m
L1 c1l 0 0.4n
Rsh raw out 1m
C2 out c2e 22u
R2 c2e c2l 3m
L2 c2l 0 0.4n
"""
    else:
        plant = f"""
* board: C612 22u (1210 X7R) at the PMIC
C612 raw c1e 22u
R612 c1e c1l 3m
L612 c1l 0 0.5n
* FB600 BLM18KG121TH1D: DCR + low-frequency inductance with loss resistor
RFB raw fbm 0.025
LFB fbm out {lfb}
RFBP raw out 120
* radar end: C624 22u, C620 10u, C312 10u, C341 4.7u, C313 1u, 10x100n (lumped, ~1 mohm, ~0.15 nH)
CR out cre 48.7u
RR cre crl 1m
LR crl 0 0.15n
"""
    fbnode = "out" if sense == "after" else "raw"
    if kind == "tran":
        load = f"Iload out 0 PWL(0 {ibase} 50u {ibase} {50e-6+tr} {ipk} 200u {ipk} {200e-6+tr} {ibase})"
        ana = f".tran 5n {tstop} 0 5n\n.control\nrun\nwrdata {RES}/ls_tmp.txt v(out) v(raw) i(Vsl)\n.endc"
        inj = f"Vinj fbin {fbnode} 0"
    else:
        load = f"Iload out 0 {ibase}"
        ana = (f".ac dec 50 1k 10meg\n.control\nrun\nlet T = -v({fbnode})/v(fbin)\n"
               f"wrdata {RES}/ls_tmp.txt mag(T) ph(T)\n.endc")
        inj = f"Vinj fbin {fbnode} 0 AC 1"
    return f"""* Buck2 averaged current-mode model
.param kp={kp} ki={ki}
Vref ref 0 1.0
{inj}
* PI outer loop: Ic = kp*e + ki*integral(e), clamped to the 5 A peak current limit
Ci integ 0 1
Ri integ 0 1e9
Bi 0 integ I = {{ki}}*(V(ref)-V(fbin))
Bc ic 0 V = max(-5, min(5, {{kp}}*(V(ref)-V(fbin)) + V(integ)))
* inner current loop as first-order lag (0.25 us), plus 2 MHz roll-off already inside it
Rlag ic icl 1
Clag icl 0 0.25u
Bil 0 raw I = V(icl)
Vsl raw_s raw 0
{plant}
{load}
.options reltol=1e-4
{ana}
.end
"""


def sim(**kw):
    kind = kw.get("kind", "tran")
    cir = SP / "_loadstep.cir"
    cir.write_text(deck(**kw))
    r = subprocess.run(["ngspice", "-b", str(cir)], cwd=SP, capture_output=True, text=True)
    f = RES / "ls_tmp.txt"
    if r.returncode or not f.exists():
        print(r.stdout[-2000:], r.stderr[-2000:]); sys.exit("ngspice failed")
    d = np.loadtxt(f); f.unlink()
    return d


def step_metrics(d, ipk):
    t, v = d[:, 0], d[:, 1]
    pre = v[(t > 40e-6) & (t < 50e-6)].mean()
    win = (t > 50e-6) & (t < 200e-6)
    vmin = v[win].min()
    # ringing: count zero crossings of (v - final) after the dip
    fin = v[(t > 180e-6) & (t < 200e-6)].mean()
    after = v[(t > 50e-6) & (t < 200e-6)] - fin
    sgn = np.sign(after[np.abs(after) > 2e-3])
    rings = int(np.sum(sgn[1:] != sgn[:-1]))
    vmax = v[(t > 200e-6)].max()
    return pre, vmin - pre, vmax - fin, rings, fin


def margins(d):
    f, mag, ph = d[:, 0], d[:, 1], d[:, 3] if d.shape[1] > 3 else d[:, 2]
    # wrdata writes (f, re, im) per vector for complex; for mag/ph they are real -> col 1 and col 3
    mag, ph = d[:, 1], d[:, 3]
    idx = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    if not len(idx):
        return None, None
    i = idx[0]
    fc = f[i]
    pm = 180 + np.degrees(ph[i])
    # additional crossings (gain back above 1 => resonance in the loop)
    return fc, pm


def main():
    lines = []
    # 1) calibrate kp so the TI datasheet case gives -40 mV
    lo, hi = 5.0, 200.0
    for _ in range(25):
        kp = (lo * hi) ** 0.5
        d = sim(kind="tran", kp=kp, fz=30e3, lfb=0, sense="after", ipk=2.0, network="ti")
        _, dip, _, _, _ = step_metrics(d, 2.0)
        if -dip > 0.040: lo = kp
        else: hi = kp
    kp = (lo * hi) ** 0.5
    d = sim(kind="ac", kp=kp, fz=30e3, lfb=0, sense="after", ipk=2.0, network="ti")
    fc, pm = margins(d)
    lines.append(f"Calibration (TI test, 22u+22u, 0.1->2 A/1 us): kp = {kp:.1f} A/V gives -40 mV; "
                 f"crossover {fc/1e3:.0f} kHz, phase margin {pm:.0f} deg")

    # 2) board network, sweep ferrite inductance and sense point, 0.1 -> 2.5 A (3-TX peak)
    lines.append("")
    lines.append("Board 1.0 V rail, 0.1 -> 2.5 A in 1 us at the radar (3-TX peak, SWRS237C Table 7-3 note 2)")
    lines.append(f"{'ferrite L':>10} {'sense':>7} {'dip at radar':>13} {'overshoot':>10} {'rings':>6} {'fc':>8} {'PM':>6}")
    rows = []
    for lfb in [5e-9, 20e-9, 50e-9, 150e-9, 300e-9]:
        for sense in ["after", "before"]:
            d = sim(kind="tran", kp=kp, fz=30e3, lfb=lfb, sense=sense, ipk=2.5)
            pre, dip, ov, rings, fin = step_metrics(d, 2.5)
            np.savetxt(RES / f"loadstep_L{int(lfb*1e9)}n_{sense}.txt", d[::20], fmt="%.6e",
                       header="t v(1V0_R) v(1V0_RAW) i")
            a = sim(kind="ac", kp=kp, fz=30e3, lfb=lfb, sense=sense, ipk=2.5)
            fc, pm = margins(a)
            t_, v_ = d[:, 0], d[:, 1]
            late = (t_ > 350e-6)
            osc = (v_[late].max() - v_[late].min()) * 1e3
            stable = osc < 1.0
            rows.append((lfb, sense, dip, ov, rings, fc, pm, fin))
            lines.append(f"{lfb*1e9:8.0f}nH {sense:>7} {dip*1e3:10.1f} mV {ov*1e3:7.1f} mV {rings:6d} "
                         f"{(fc or 0)/1e3:6.0f}k {((pm + 180) % 360 - 180) if pm is not None else float('nan'):5.0f}°  "
                         f"{'stable' if stable else f'OSCILLATES ({osc:.0f} mVpp at 350-400 us)'}  DC at radar {fin:.4f} V")
    lines.append("")
    lines.append("Limits: PGOOD undervoltage trips at -29 mV (min) / -40 mV (typ) / -53 mV (max) for > 4-10 us;")
    lines.append("        radar VIN_13RF in LDO-bypass mode needs >= 0.95 V (SWRS237C 7.4).")
    out = "\n".join(lines)
    (RES / "loadstep_summary.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
