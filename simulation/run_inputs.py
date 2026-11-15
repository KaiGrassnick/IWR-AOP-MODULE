#!/usr/bin/env python3
"""5 V input transients: USB-C hot-plug overshoot and PMIC input-OVP margin.

1) Hot-plug: a live 5 V source is connected through a cable (series R, L) to
   J200 VBUS -> F200 -> USB_VBUS (C100 + C202 = 2 uF, no TVS, no bulk).
   Peak USB_VBUS vs LM66200 VIN abs max 6 V and CP2105 VBUS/REGIN ratings.
2) Load release: radar chirp current at 5 V steps 1.3 A -> 0.3 A in 1 us while the
   supply sits at its maximum allowed voltage; peak SYS_5V vs LP87524 VANA OVP
   (5.6 V min, 20 us debounce). OVP latches the PMIC off until 5 V is removed.
"""
import pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
SP, RES = HERE / "spice", HERE / "results"


def ng(txt):
    cir = SP / "_inputs.cir"; cir.write_text(txt)
    r = subprocess.run(["ngspice", "-b", str(cir)], cwd=SP, capture_output=True, text=True)
    f = RES / "in_tmp.txt"
    if r.returncode or not f.exists():
        print(r.stdout[-2000:], r.stderr[-2000:]); sys.exit("ngspice failed")
    d = np.loadtxt(f); f.unlink(); return d


def hotplug(lcab, rcab, vsrc=5.0, cbulk_src=100e-6, rev11=True):
    damper = """* C204 10 uF 50 V X7R (8 uF at 5 V) + R210 0.47 ohm damper, D202 SMF5.0A TVS
C204 USB_VBUS dmp 8u
R210 dmp 0 0.47
D202 0 USB_VBUS DTVS
.model DTVS D(BV=6.7 IBV=1m RS=0.35 CJO=600p)
""" if rev11 else ""
    d = ng(f"""* USB-C hot plug
* charger output: bulk {cbulk_src*1e6:.0f} uF already at {vsrc} V
Cs src 0 {cbulk_src} ic={vsrc}
Rs src s1 10m
* contact closes at 1 us
S1 s1 cab ctl 0 SWC
Vctl ctl 0 PWL(0 0 1u 0 1.001u 1)
.model SWC SW(VT=0.5 VH=0.1 RON=5m ROFF=1e9)
Lc cab cab2 {lcab}
Rc cab2 USB_CONN_VBUS {rcab}
RF200 USB_CONN_VBUS USB_VBUS 0.03
C100 USB_VBUS c1 1u
R100e c1 0 5m
C202 USB_VBUS c2 1u
R202e c2 0 5m
* CP2105 VBUS divider R200/R201
R200 USB_VBUS sense 24k
R201 sense 0 47k
{damper}.tran 2n 80u 0 2n uic
.control
run
wrdata {RES}/in_tmp.txt v(USB_VBUS)
.endc
.end
""")
    return d[:, 1].max()


def release(vsup, lsup, rsup):
    d = ng(f"""* load release at max supply
Vs src 0 {vsup}
Ls src a {lsup}
Rs a VIN {rsup}
CIN VIN 0 2u
* LM66200 on, 37 mohm
R117 VIN SYS_5V 0.037
* SYS_5V: 4 x 22 uF (derated) + 100 nF
C600 SYS_5V c6 70u
R600 c6 0 1m
C604 SYS_5V 0 100n
* PMIC input current: 1.3 A chirp peak -> 0.3 A at 100 us (1 us edge), back at 300 us
Iload SYS_5V 0 PWL(0 1.3 100u 1.3 101u 0.3 300u 0.3 301u 1.3)
.ic v(SYS_5V)={vsup - 1.3*(rsup+0.037)} v(VIN)={vsup - 1.3*rsup}
.tran 10n 500u 0 10n uic
.control
run
wrdata {RES}/in_tmp.txt v(SYS_5V)
.endc
.end
""")
    t, v = d[:, 0], d[:, 1]
    m = t > 100e-6
    pk = v[m].max()
    t_over = (v[m] > 5.6).sum() * (t[1] - t[0]) if pk > 5.6 else 0.0
    return pk, t_over


def main():
    L = ["USB-C hot plug into USB_VBUS; limit: LM66200 VIN abs max 6.0 V",
         "The board has the C204 + R210 damper and the D202 TVS; the second column is the same input with 2 uF of ceramic only",
         f"{'source, cable':40} {'peak':>13} {'undamped':>13}"]
    ok = True
    for name, lc, rc, vs in [("5.0 V, 0.3 m, 0.25 uH, 60 mohm", 0.25e-6, 0.06, 5.0),
                             ("5.0 V, 1 m, 0.7 uH, 120 mohm", 0.7e-6, 0.12, 5.0),
                             ("5.0 V, 2 m, 1.4 uH, 200 mohm", 1.4e-6, 0.20, 5.0),
                             ("5.25 V (USB max), 1 m", 0.7e-6, 0.12, 5.25),
                             ("5.5 V (charger high), 1 m", 0.7e-6, 0.12, 5.5)]:
        pk, pk0 = hotplug(lc, rc, vs), hotplug(lc, rc, vs, rev11=False)
        ok &= pk < 6.0
        L.append(f"{name:40} {pk:10.2f} V {pk0:10.2f} V  {'OVER 6 V abs max' if pk > 6 else 'ok'}")
    L.append(("PASS" if ok else "FAIL") + "  hot-plug peak under 6.0 V for every source and cable")
    L += ["", "Load release 1.3 A -> 0.3 A at the 5 V input; limit: LP87524 VANA OVP 5.6 V min (latches off)",
          f"{'supply':38} {'peak SYS_5V':>12}"]
    for name, vs, ls, rs in [("USB 5.00 V, 1 m cable", 5.00, 0.7e-6, 0.12),
                             ("USB 5.25 V (USB max), 1 m cable", 5.25, 0.7e-6, 0.12),
                             ("USB 5.50 V (charger high), 1 m cable", 5.50, 0.7e-6, 0.12),
                             ("carrier 5.50 V (spec max), 10 cm wiring", 5.50, 0.1e-6, 0.03),
                             ("carrier 5.50 V, 50 cm wiring", 5.50, 0.4e-6, 0.08)]:
        pk, tov = release(vs, ls, rs)
        L.append(f"{name:38} {pk:9.3f} V  " + (f"above 5.6 V for {tov*1e6:.1f} us (OVP needs > 20 us)" if tov else "below 5.6 V"))
    out = "\n".join(L)
    (RES / "inputs_summary.txt").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    main()
