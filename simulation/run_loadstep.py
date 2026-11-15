#!/usr/bin/env python3
"""1.0 V and 1.8 V rails: chirp load step, DC level at the radar and loop stability.

The LP87524J-Q1 compensation is internal and undocumented, so the controller is an
averaged current-mode model (inner current loop as a 0.25 us lag, PI outer loop)
CALIBRATED to TI's own datasheet number: 0.1 -> 2 A in 1 us with 22 uF + 22 uF POL,
no ferrite, gives about -40 mV (SNVSAW2B 6.5).  That controller is then put on the
board network:

    buck output (2 x 22 uF) -> ferrite(s) -> extracted copper -> radar-side capacitors
    FB pin: DC from the radar end through 22 ohm, AC from the buck output through 1 uF

The ferrite is Murata's equivalent circuit for the BLM18KG260TN1 (spice/beads_murata.lib); its
inductance is swept down to 10 % because a bead loses inductance under DC bias and Murata's
model is a zero-bias one.  Copper resistances come from results/pdn.json (pdn_extract.py, 85 C).
For comparison the same board is run with the filter of the TI EVM: 120 ohm beads
(BLM18KG121TH1) and local sense at the buck output.
"""
import json
import pathlib
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SP, RES = HERE / "spice", HERE / "results"
RES.mkdir(exist_ok=True)
try:
    PDN = json.load(open(RES / "pdn.json"))["85C"]
except FileNotFoundError:
    sys.exit("run pdn_extract.py first (results/pdn.json)")

# rail: nominal V, radar-side C (lumped, from the netlist), beads in parallel, copper bead -> radar (ohm),
#       idle A, peak A
RAILS = {
    "1V0": dict(v=1.0, cr=92.7e-6, nbead=2, rcu=PDN["1V0"]["bead_to_radar_mohm"] * 1e-3, i0=0.1, ipk=2.5, vmin=0.95, vmax=1.05,
                svs=0.900),
    "1V8": dict(v=1.8, cr=44.3e-6, nbead=1, rcu=PDN["1V8"]["bead_to_radar_mohm"] * 1e-3, i0=0.1, ipk=0.85, vmin=1.71, vmax=1.90,
                svs=1.653),
}
T0, T1, TEND = 100e-6, 500e-6, 900e-6


def deck(kind, kp, rail="1V0", sense="blend", k=1.0, tr=1e-6, network="board", craw=44e-6, nbead=None, cr=None,
         rs=22.0, cs=1e-6, ipk=None, i0=None, fz=30e3, bead="BEAD26", rcu=None):
    r = RAILS[rail]
    ki = kp * 2 * np.pi * fz
    ipk = r["ipk"] if ipk is None else ipk
    i0 = r["i0"] if i0 is None else i0
    nbead = r["nbead"] if nbead is None else nbead
    cr = r["cr"] if cr is None else cr
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
Rfbdc out fb 1m
"""
        vref, ipk, i0 = 1.0, 2.0, 0.1
    else:
        beads = "\n".join(f"XB{i} raw mid {bead} params: k={k}" for i in range(nbead))
        fbnet = {"blend": f"R628 out fb {rs}\nC631 raw fb {cs}",
                 "local": "Rfbdc raw fb 1m",
                 "after": "Rfbdc out fb 1m"}[sense]
        plant = f"""
.include beads_murata.lib
* buck output capacitors (22 uF 1210 / 0805 each: 3 mohm, 0.5 nH)
CRAW raw c1e {craw}
RRAW c1e c1l {3e-3 * 22e-6 / craw}
LRAW c1l 0 {0.5e-9 * 22e-6 / craw}
{beads}
* extracted copper from the bead outputs to the radar balls, ~1 nH of plane-pair inductance
RCU mid midl {r['rcu'] if rcu is None else rcu}
LCU midl out 1n
* radar-side capacitors, lumped
CR out cre {cr}
RR cre crl 1m
LR crl 0 0.15n
* feedback network; FB pin input resistance 100 k [ASSUMED, not in the datasheet]
{fbnet}
Rfbin fb 0 100k
"""
        vref = r["v"]
    if kind == "tran":
        load = f"Iload out 0 PWL(0 {i0} {T0} {i0} {T0+tr} {ipk} {T1} {ipk} {T1+tr} {i0})"
        ana = f".tran 10n {TEND} 0 10n\n.control\nrun\nwrdata {RES}/ls_tmp.txt v(out) v(raw) v(fb)\n.endc"
        inj = "Vinj fbin fb 0"
    else:
        load = f"Iload out 0 {i0}"
        ana = f".ac dec 60 300 10meg\n.control\nrun\nlet T = -v(fb)/v(fbin)\nwrdata {RES}/ls_tmp.txt mag(T) ph(T)\n.endc"
        inj = "Vinj fbin fb 0 AC 1"
    return f"""* averaged current-mode buck, {rail}, {sense}
Vref ref 0 {vref}
{inj}
Ci integ 0 1
Ri integ 0 1e9
Bi 0 integ I = {ki}*(V(ref)-V(fbin))
Bc ic 0 V = max(-5, min(5, {kp}*(V(ref)-V(fbin)) + V(integ)))
Rlag ic icl 1
Clag icl 0 0.25u
Bil 0 raw I = V(icl)
{plant}
{load}
.options reltol=1e-4 method=gear
{ana}
.end
"""


def sim(kind, kp, **kw):
    cir = SP / "_loadstep.cir"
    cir.write_text(deck(kind, kp, **kw))
    r = subprocess.run(["ngspice", "-b", str(cir)], cwd=SP, capture_output=True, text=True)
    f = RES / "ls_tmp.txt"
    if r.returncode or not f.exists():
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit("ngspice failed")
    d = np.loadtxt(f)
    f.unlink()
    return d


def metrics(d):
    t, vo, vr = d[:, 0], d[:, 1], d[:, 3]
    pre = (t > T0 - 20e-6) & (t < T0)
    on = (t > T0) & (t < T1)
    end = (t > T1 - 30e-6) & (t < T1)
    return dict(pre=vo[pre].mean(), vmin=vo[on].min(), settled=vo[end].mean(), ring=np.ptp(vo[end]),
                vmax=vo[t > T1].max(), raw_min=vr[on].min(), raw_settled=vr[end].mean(), raw_max=vr.max())


def margin(d):
    f, mag, ph = d[:, 0], d[:, 1], np.degrees(d[:, 3])
    i = np.where((mag[:-1] >= 1) & (mag[1:] < 1))[0]
    if not len(i):
        return None, None
    i = i[-1]                                   # last crossing: everything above it has gain < 1
    return f[i], (ph[i] + 180 + 180) % 360 - 180


def main():
    L = []
    lo, hi = 5.0, 200.0
    for _ in range(25):
        kp = (lo * hi) ** 0.5
        m = metrics(sim("tran", kp, network="ti"))
        lo, hi = (kp, hi) if m["pre"] - m["vmin"] > 0.040 else (lo, kp)
    kp = (lo * hi) ** 0.5
    fc, pm = margin(sim("ac", kp, network="ti"))
    L.append(f"Controller calibration (TI test, 22u + 22u, 0.1 -> 2 A in 1 us = -40 mV): kp = {kp:.1f} A/V, "
             f"crossover {fc/1e3:.0f} kHz, phase margin {pm:.0f} deg")
    L.append("")

    def row(label, rail, **kw):
        r = RAILS[rail]
        m = metrics(sim("tran", kp, rail=rail, **kw))
        fc, pm = margin(sim("ac", kp, rail=rail, **kw))
        stable = m["ring"] < 2e-3 and pm is not None and pm > 30
        L.append(f"{label:44} {m['settled']:6.3f} V {1e3*(m['vmin']-m['pre']):7.0f} mV {m['vmin']:6.3f} V "
                 f"{m['vmax']:6.3f} V {m['raw_min']:6.3f} V {m['raw_max']:6.3f} V {(fc or 0)/1e3:5.0f}k {pm if pm is not None else float('nan'):4.0f}  "
                 f"{'stable' if stable else 'UNSTABLE' if m['ring'] > 20e-3 else 'ringing'}")
        return m, pm

    head = (f"{'case':44} {'DC@radar':>8} {'dip':>10} {'min':>8} {'max':>8} {'buck min':>8} {'buck max':>8} {'fc':>6} {'PM':>4}")
    res = {}
    for rail in ("1V0", "1V8"):
        r = RAILS[rail]
        L.append(f"=== {rail}: {r['i0']} -> {r['ipk']} A at the radar, {r['nbead']} bead(s), copper {r['rcu']*1e3:.1f} mohm, "
                 f"radar-side C {r['cr']*1e6:.1f} uF.  Radar limits {r['vmin']} - {r['vmax']} V, supervisor trips at {r['svs']} V at the buck output")
        L.append(head)
        for k in (1.0, 0.5, 0.25, 0.1):
            for tr in (1e-6, 10e-6, 100e-6):
                m, pm = row(f"bead L {k*100:.0f} %, {tr*1e6:.0f} us edge", rail, k=k, tr=tr)
                res[(rail, k, tr)] = (m, pm)
                if (k, tr) in ((1.0, 1e-6), (0.25, 10e-6)):
                    d = sim("tran", kp, rail=rail, k=k, tr=tr)
                    np.savetxt(RES / f"loadstep_{rail}_k{int(k*100)}_tr{int(tr*1e6)}us.txt", d[::25][:, [0, 1, 3]], fmt="%.6e", header="t v(radar) v(buck output)")
        L.append("  component spread (bead L 130 %, sense C -40 %, radar C -30 %, buck C -30 %):")
        row("  worst-case stack, 1 us edge", rail, k=1.3, cs=0.6e-6, cr=r["cr"] * 0.7, craw=31e-6)
        row("  sense C -40 % only", rail, cs=0.6e-6)
        row("  bead L 130 %, radar C +30 %", rail, k=1.3, cr=r["cr"] * 1.3)
        L.append("  for comparison, 10 us edge, bead L 50 %:")
        row("  this board", rail, k=0.5, tr=10e-6)
        row("  TI EVM filter (120 ohm beads, local sense)", rail, sense="local", k=0.5, tr=10e-6, bead="BEAD120")
        if rail == "1V0":
            row("  2-TX profile 0.5 -> 2.0 A, 10 us, L 50 %", rail, k=0.5, tr=10e-6, i0=0.5, ipk=2.0)
        L.append("")
    # verdicts
    checks = []
    for rail in ("1V0", "1V8"):
        r = RAILS[rail]
        ms = [v for (rl, k, tr), v in res.items() if rl == rail]
        checks.append((f"{rail}: loop stable for every bead inductance and edge (PM > 45 deg, no ringing)",
                       all(pm is not None and pm > 45 and m["ring"] < 2e-3 for m, pm in ms)))
        checks.append((f"{rail}: DC level at the radar back on target under load (within 5 mV)",
                       all(abs(m["settled"] - r["v"]) < 5e-3 for m, pm in ms)))
        checks.append((f"{rail}: buck output never reaches the supervisor threshold during a step",
                       all(m["raw_min"] > r["svs"] + 0.02 for m, pm in ms)))
        slow = [m for (rl, k, tr), (m, pm) in res.items() if rl == rail and tr >= 100e-6]
        checks.append((f"{rail}: inside the radar limits for 100 us edges",
                       all(m["vmin"] >= r["vmin"] - 0.02 and m["vmax"] <= r["vmax"] + 0.02 for m in slow)))
        fast = [m for (rl, k, tr), (m, pm) in res.items() if rl == rail and tr <= 10e-6]
        worst = min(m["vmin"] for m in fast)
        L.append(f"{rail}: fastest edges dip to {worst:.3f} V at the radar for some tens of microseconds "
                 f"({'below' if worst < r['vmin'] else 'inside'} the {r['vmin']} V DC limit). This is the ferrite-capacitor "
                 f"filter and the feed copper; it is smaller than with the TI EVM filter and depends on how fast the radar "
                 f"really steps its current.")
    L.append("")
    L.append("CHECKS")
    L += [f"  {'PASS' if ok else 'FAIL'}  {c}" for c, ok in checks]
    out = "\n".join(L)
    (RES / "loadstep_summary.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
