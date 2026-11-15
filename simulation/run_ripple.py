#!/usr/bin/env python3
"""Switching ripple at the radar on 1V0_R and 1V8_R vs SWRS237C Table 7-2 (Rev 1.1).

Ideal synchronous buck at 4 MHz (fixed duty, open loop, steady state from initial
conditions) driving L60x 0.47 uH -> 2 x 22 uF -> ferrite(s) -> radar-end capacitors.
The ferrite is Murata's equivalent circuit (spice/bead_blm18kg121.lib) with its inductance
swept down for DC bias; capacitor ESR/ESL are assumptions (marked below).
"""
import pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SP, RES = HERE / "spice", HERE / "results"

# rail: (Vout, load A, radar-end C, spec uVrms at 4.4 MHz from Table 7-2)
# plus the number of ferrites in parallel
RAILS = {"1V0_R": (1.0, 0.8, 92.7e-6, 13, 2), "1V8_R": (1.8, 0.6, 44.3e-6, 19, 1)}


def run(rail, k):
    vo, il, cr, spec, nb = RAILS[rail]
    beads = "\n".join(f"XB{i} raw2 out BEAD120 params: k={k}" for i in range(nb))
    vin, fs = 5.0, 4e6
    d = vo / vin + 0.004  # small loss allowance
    cir = SP / "_ripple.cir"
    cir.write_text(f"""* ripple {rail}
Vin vin 0 {vin}
* ideal synchronous switch node: PULSE between 0 and VIN
Vsw sw 0 PULSE(0 {vin} 0 2n 2n {d/fs - 2e-9} {1/fs})
L60x sw raw 0.47u ic={il}
R60xdcr raw raw2 0.02
.include bead_blm18kg121.lib
* buck output: 2 x 22 uF, each ESR 3 mohm, ESL 0.5 nH [ASSUMED]
C61x raw2 c1e 44u ic={vo}
R61x c1e c1l 1.5m
L61x c1l 0 0.25n
{beads}
* radar-end MLCCs, ~1 mohm, 0.15 nH, plus via/plane 0.2 nH [ASSUMED]
CR out cre {cr} ic={vo}
RR cre crl 1m
LR crl 0 0.35n
Iload out 0 {il}
.options reltol=1e-4
.tran 1n 400u 0 1n uic
.control
run
linearize v(out) v(raw2)
wrdata {RES}/rp_tmp.txt v(out) v(raw2)
.endc
.end
""")
    r = subprocess.run(["ngspice", "-b", str(cir)], cwd=SP, capture_output=True, text=True)
    f = RES / "rp_tmp.txt"
    if r.returncode or not f.exists():
        print(r.stdout[-2000:], r.stderr[-2000:]); sys.exit("ngspice failed")
    dd = np.loadtxt(f); f.unlink()
    t = dd[:, 0]
    m = t > 360e-6
    out, raw = dd[m, 1], dd[m, 3]
    n = len(out); dt = t[1] - t[0]
    # rms of the fundamental at fs (sinusoid amplitude / sqrt2) via single-bin DFT on whole periods
    nper = int(round(4e6 * n * dt)); n2 = int(round(nper / 4e6 / dt))
    def fund(x):
        x = x[:n2] - x[:n2].mean()
        tt = np.arange(n2) * dt
        a = 2 / n2 * np.abs(np.sum(x * np.exp(-2j * np.pi * 4e6 * tt)))
        return a / np.sqrt(2)
    return fund(out) * 1e6, fund(raw) * 1e6, (out.max() - out.min()) * 1e6, spec


def main():
    lines = ["Switching ripple, fundamental at 4 MHz (Table 7-2 gives the limit at 4.4 MHz)",
             f"{'rail':6} {'bead L':>9} {'at PMIC (uVrms)':>16} {'at radar (uVrms)':>17} {'limit':>6}"]
    for rail in RAILS:
        for k in [1.0, 0.5, 0.25, 0.1]:
            o, rw, pp, spec = run(rail, k)
            lines.append(f"{rail:6} {k*100:7.0f} % {rw:16.0f} {o:17.1f} {spec:4d} uV  {'PASS' if o < spec else 'FAIL'}")
    out = "\n".join(lines)
    (RES / "ripple_summary.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
