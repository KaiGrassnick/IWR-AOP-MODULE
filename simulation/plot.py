#!/usr/bin/env python3
"""Plot the key waveforms from results/ into results/*.png (needs matplotlib)."""
import pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = pathlib.Path(__file__).resolve().parent / "results"
SIGS = ["SYS_5V", "PMIC_ENABLE", "3V3_R", "1V2_R", "1V0_R", "1V8_R",
        "AOP_NRESET", "PMIC_PGOOD", "AOP_UART_RX_BALL", "USB_VBUS", "PWR_IN"]
COL = {"3V3_R": "#d62728", "1V2_R": "#2ca02c", "1V0_R": "#1f77b4", "1V8_R": "#9467bd",
       "AOP_NRESET": "black", "PMIC_ENABLE": "#ff7f0e", "SYS_5V": "#7f7f7f"}


def load(name):
    d = np.loadtxt(RES / f"power_{name}.txt")
    return d[:, 0], {s: d[:, 2 * i + 1] for i, s in enumerate(SIGS)}


def panel(ax, t, v, t0, t1, title):
    m = (t * 1e3 >= t0) & (t * 1e3 <= t1)
    for s in ["SYS_5V", "PMIC_ENABLE", "3V3_R", "1V8_R", "1V2_R", "1V0_R", "AOP_NRESET"]:
        ax.plot(t[m] * 1e3, v[s][m], label=s, color=COL[s], lw=2.2 if s == "AOP_NRESET" else 1.3,
                ls="--" if s in ("SYS_5V", "PMIC_ENABLE") else "-")
    ax.axhline(1.57, color="black", lw=0.6, ls=":")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("ms"); ax.set_ylabel("V"); ax.grid(alpha=0.3)


t, v = load("carrier")
tu, vu = load("usb")
td, vd = load("dip")
fig, axs = plt.subplots(2, 2, figsize=(13, 8))
panel(axs[0, 0], t, v, 0, 35, "Carrier power-up: 1V8 > 1V0 > 1V2/3V3, U601 releases reset 20 ms after the last rail")
panel(axs[0, 1], t, v, 39.5, 42.5, "HOST_DISABLE at 40 ms: GPIO2 pulls reset in ~12 us, before rails fall")
panel(axs[1, 0], tu, vu, 79.8, 81.0, "USB unplug at 80 ms: U601 pulls reset as 3V3_R passes 3.07 V, core rails still up")
panel(axs[1, 1], td, vd, 59, 95, "1 ms 5 V dropout at 60 ms: reset held until 20 ms after all rails are back")
axs[0, 0].legend(fontsize=8, ncol=2)
fig.tight_layout()
fig.savefig(RES / "power_sequences.png", dpi=110)

fig, ax = plt.subplots(1, 2, figsize=(13, 4.2))
for a_, rail, vnom, lo, hi in ((ax[0], "1V0", 1.0, 0.95, 1.05), (ax[1], "1V8", 1.8, 1.71, 1.9)):
    for f, lab in (("k100_tr1us", "bead L 100 %, 1 us edge"), ("k25_tr10us", "bead L 25 %, 10 us edge")):
        d = np.loadtxt(RES / f"loadstep_{rail}_{f}.txt")
        a_.plot(d[:, 0] * 1e6, d[:, 1], label=f"radar, {lab}")
        a_.plot(d[:, 0] * 1e6, d[:, 2], ls="--", lw=0.9, label=f"buck output, {lab}")
    a_.axhline(lo, color="k", ls=":", lw=0.8); a_.axhline(hi, color="k", ls=":", lw=0.8)
    a_.set_xlabel("us"); a_.set_ylabel("V"); a_.grid(alpha=0.3); a_.legend(fontsize=7)
    a_.set_title(f"{rail}: load step at 100 us, release at 500 us (dotted: radar DC limits)", fontsize=10)
fig.tight_layout()
fig.savefig(RES / "loadstep.png", dpi=110)
print("wrote", RES / "power_sequences.png", RES / "loadstep.png")
