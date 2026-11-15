#!/usr/bin/env python3
"""Electrical replay: the chirp load the firmware actually configured, on the board's 1.0 V and 1.8 V.

The co-simulation (cosim/run_cosim.py) records every frame the radar stand-in ran: chirp idle
and ramp time, TX start time, how many TX each chirp switches on, and the Buck2/Buck3 output
voltage the PMIC was set to at that moment (so a firmware trim of Buck2 is in the trace). This
script turns one frame of such a trace into a current waveform per rail and runs it through
the same ngspice model of buck controller, ferrites, copper and capacitors that
simulation/run_loadstep.py uses (calibrated controller, Murata bead model, extracted copper).

Current per rail uses the 'max' set of sim/bss.py (SWRS237C Table 7-3, worst case): RF on for
the whole active frame, plus a per-TX share while each TX is on. The edge time of these steps
is not published; 1 us and 10 us are run. The ferrite inductance is run at 100 % (zero bias) and
50 % (DC bias), as in loadstep_summary.txt.

usage: replay_rails.py [trace.json ...]     (default: every cosim/results/traces/*.json)
"""
import json
import pathlib
import subprocess
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
COSIM = HERE.parent
REPO = COSIM.parent
sys.path.insert(0, str(REPO / "simulation"))
sys.path.insert(0, str(COSIM))
import run_loadstep as ls  # noqa: E402  (needs simulation/results/pdn.json)
from sim.bss import LOAD  # noqa: E402

RES = COSIM / "results"
SP = REPO / "simulation" / "spice"
PRE, POST = 100e-6, 300e-6          # simulated time before the frame and after its last chirp
SVS_RAIL = {"1V0": "1V0", "1V8": "1V8"}
VSET_KEY = {"1V0": "1V0", "1V8": "1V8"}


def kp_calibrated():
    """kp from loadstep_summary.txt (the TI-datasheet calibration); recomputed if missing."""
    try:
        for line in open(REPO / "simulation/results/loadstep_summary.txt"):
            if "kp =" in line:
                return float(line.split("kp =")[1].split()[0])
    except FileNotFoundError:
        pass
    return 37.9


def load_points(f, rail, tr):
    """PWL (t, i) for one frame: idle, RF on over the active frame, per-TX share while TX are on."""
    idle, act, per_tx = LOAD["max"][rail]
    tc = f["idle"] + f["ramp"]
    t_end = PRE + len(f["seq"]) * tc
    pts = [(0.0, idle), (PRE, idle), (PRE + tr, act)]
    if per_tx:
        for k, n in enumerate(f["seq"]):
            on = PRE + k * tc + f["idle"] + f["txs"]
            off = PRE + k * tc + f["idle"] + f["ramp"]
            if n:
                pts += [(on, act), (on + tr, act + per_tx * n), (off, act + per_tx * n), (off + tr, act)]
    # RF off after the last TX has ramped down
    t_off = max(t_end, pts[-1][0])
    if t_off > pts[-1][0]:
        pts.append((t_off, act))
    pts += [(t_off + tr, idle), (t_off + tr + POST, idle)]
    t_end = t_off + tr
    # PWL needs strictly increasing times
    out = []
    for t, i in pts:
        if out and t <= out[-1][0]:
            t = out[-1][0] + 1e-9
        out.append((t, i))
    return out, t_end


def run(f, rail, k, tr, kp):
    pts, t_end = load_points(f, rail, tr)
    tend = t_end + POST
    vref = f["vset"][VSET_KEY[rail]]
    cir = ls.deck("tran", kp, rail=rail, k=k, tr=tr)
    lines = []
    for line in cir.splitlines():
        if line.startswith("Vref "):
            line = f"Vref ref 0 {vref:.4f}"
        elif line.startswith("Iload "):
            pwl = " ".join(f"{t:.9g} {i:.4g}" for t, i in pts)
            line = "Iload out 0 PWL(" + pwl + ")"
            # ngspice line length: wrap with continuation lines
            words, line, cur = line.split(" "), "", ""
            for w in words:
                if len(cur) + len(w) > 200:
                    line += cur + "\n+ "
                    cur = ""
                cur += w + " "
            line += cur
        elif line.startswith(".tran"):
            line = f".tran 20n {tend:.6g} 0 20n"
        elif line.startswith("wrdata"):
            line = f"wrdata {RES}/_replay_tmp.txt v(out) v(raw)"
        lines.append(line)
    path = SP / "_replay.cir"
    path.write_text("\n".join(lines) + "\n")
    r = subprocess.run(["ngspice", "-b", str(path)], cwd=SP, capture_output=True, text=True)
    out = RES / "_replay_tmp.txt"
    if r.returncode or not out.exists():
        print(r.stdout[-2000:], r.stderr[-2000:])
        sys.exit("ngspice failed")
    d = np.loadtxt(out)
    out.unlink()
    path.unlink()
    return d[:, 0], d[:, 1], d[:, 3], t_end


def metrics(t, vo, vr, t_end, rail):
    r = ls.RAILS[rail]
    frame = (t >= PRE) & (t <= t_end + POST)
    dt = np.diff(t, append=t[-1])
    return dict(vmin=vo[frame].min(), vmax=vo[frame].max(), buck_min=vr[frame].min(),
                below=float((dt * (vo < r["vmin"]) * frame).sum()), above=float((dt * (vo > r["vmax"]) * frame).sum()),
                svs=r["svs"], lo=r["vmin"], hi=r["vmax"])


def plot(name, series, rail, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    r = ls.RAILS[rail]
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    for (label, t, v), color in zip(series, ("#2a78d6", "#eb6834")):
        ax.plot(t * 1e6, v, color=color, lw=1.2, label=label.split("_", 1)[-1].replace("_", " "))
    for y, txt in ((r["vmin"], f"radar min {r['vmin']} V"), (r["vmax"], f"radar max {r['vmax']} V")):
        ax.axhline(y, color="#6b6b6b", lw=0.9, ls="--")
        ax.annotate(txt, (0.995, y), xycoords=("axes fraction", "data"), ha="right", va="bottom", fontsize=8, color="#4a4a4a")
    ax.set_xlabel("time from frame start (us)")
    ax.set_ylabel(f"{rail} at the radar balls (V)")
    ax.set_title(f"{name}: one firmware frame on {rail} (Table 7-3 max current, 1 us edges, bead at 100 %)",
                 fontsize=10, loc="left")
    ax.grid(color="#e4e4e4", lw=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    lo = min(min(v.min() for _, _, v in series), r["vmin"])
    hi = max(max(v.max() for _, _, v in series), r["vmax"])
    pad = 0.15 * (hi - lo)
    ax.set_ylim(lo - pad, hi + 2.2 * pad)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=9, loc="upper left", ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(paths):
    kp = kp_calibrated()
    L = [f"Electrical replay of firmware-configured frames (controller kp = {kp:.1f} A/V, simulation/run_loadstep.py model)",
         "Current: SWRS237C Table 7-3 worst case ('max' set in sim/bss.py). Voltages at the radar balls; 'buck min' at the",
         "buck output, where the supervisor senses. Edge = current step rise time [not published, swept].", ""]
    L.append("'VSET that fits': the setpoints for which this frame stays inside the radar's range (the loop is linear")
    L.append("in its reference, so another setpoint shifts the waveform). Supervisor margin: buck min vs 0.900 / 1.653 V.")
    L.append("")
    head = (f"{'trace':28} {'rail':4} {'VSET':>6} {'bead L':>6} {'edge':>5} {'min':>7} {'max':>7} {'<min':>8} {'>max':>8} "
            f"{'buck min':>8}  {'VSET that fits':>15}  verdict")
    L.append(head)
    worst = {}
    for p in paths:
        tr_data = json.load(open(p))
        name = pathlib.Path(p).stem
        frames = [f for f in tr_data["frames"] if f["kind"] == "frame"]
        if not frames:
            L.append(f"{name:28} no frames in trace")
            continue
        f = frames[0]
        f["t"] = 0.0
        for rail in ("1V0", "1V8"):
            for k in (1.0, 0.5):
                for edge in (1e-6, 10e-6):
                    t, vo, vr, t_end = run(f, rail, k, edge, kp)
                    m = metrics(t, vo, vr, t_end, rail)
                    ok = m["below"] == 0 and m["above"] == 0 and m["buck_min"] > m["svs"] + 0.009
                    verdict = "PASS" if ok else ("FAIL: supervisor" if m["buck_min"] <= m["svs"] + 0.009 else
                                                 "FAIL: outside radar range")
                    # the loop model is linear in VREF: the same frame at another setpoint is this waveform shifted
                    v = f["vset"][rail]
                    lo_ok, hi_ok = v + m["lo"] - m["vmin"], v + m["hi"] - m["vmax"]
                    fits = f"{lo_ok:.3f}-{hi_ok:.3f}" if lo_ok <= hi_ok else f"none ({(m['vmax']-m['vmin'])*1e3:.0f} mV p-p)"
                    L.append(f"{name:28} {rail:4} {v:6.3f} {k*100:5.0f}% {edge*1e6:3.0f}us {m['vmin']:7.3f} "
                             f"{m['vmax']:7.3f} {m['below']*1e6:6.1f}us {m['above']*1e6:6.1f}us {m['buck_min']:8.3f}  "
                             f"{fits:>15}  {verdict}")
                    if k == 1.0 and edge == 1e-6:
                        worst[(name, rail)] = (t - PRE, vo)
        L.append("")
    # plots: per rail, the first two traces (for example, no trim vs trim) on one axis
    names = list(dict.fromkeys(n for n, _ in worst))
    groups = [("default profile", names[:2], ""), ("3 TX at once", names[2:3], "_3tx")]
    for title, group, suffix in groups:
        for rail in ("1V0", "1V8"):
            ser = [(n, *worst[(n, rail)]) for n in group if (n, rail) in worst]
            if ser:
                plot(title, ser, rail, RES / f"replay_{rail}{suffix}.png")
    txt = "\n".join(L) + "\n"
    (RES / "replay_summary.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    args = sys.argv[1:] or sorted(str(p) for p in (RES / "traces").glob("*.json"))
    if not args:
        sys.exit("no traces: run cosim/run_cosim.py first")
    main(args)
