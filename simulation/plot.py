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
tf, vf = load("fallback")
tu, vu = load("usb")
fig, axs = plt.subplots(2, 2, figsize=(13, 8))
panel(axs[0, 0], t, v, 0, 25, "Carrier power-up: 1V8 > 1V0 > 1V2/3V3, reset released 11 ms after settle")
panel(axs[0, 1], t, v, 39.5, 42.5, "HOST_DISABLE at 40 ms: GPIO2 pulls reset in ~12 us, before rails fall")
panel(axs[1, 0], tu, vu, 59.8, 61.0, "USB unplug at 60 ms (R604 fitted): PGOOD catches 3V3 sag")
panel(axs[1, 1], tf, vf, 59.8, 80.0, "USB unplug with fallback (R604 out, C605 in): reset ~19 ms late")
axs[0, 0].legend(fontsize=8, ncol=2)
fig.tight_layout()
fig.savefig(RES / "power_sequences.png", dpi=110)

fig, ax = plt.subplots(1, 2, figsize=(13, 4.2))
for f, lab in [("L5n_after", "5 nH (trace-like), sense after"), ("L50n_after", "50 nH, sense after"),
               ("L150n_before", "150 nH, sense before ferrite")]:
    d = np.loadtxt(RES / f"loadstep_{f}.txt")
    ax[0].plot(d[:, 0] * 1e6, d[:, 1], label=lab)
ax[0].axhline(1 - 0.029, color="r", ls=":", lw=0.8, label="PGOOD UV min (-29 mV)")
ax[0].axhline(0.95, color="k", ls=":", lw=0.8, label="radar min 0.95 V")
ax[0].set_xlim(30, 400); ax[0].set_ylim(0.8, 1.15); ax[0].set_xlabel("us"); ax[0].set_ylabel("1V0_R (V)")
ax[0].set_title("1.0 V at the radar, 0.1 -> 2.5 A step at 50 us, back at 200 us", fontsize=10)
ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)
d = np.loadtxt(RES / "loadstep_L150n_after.txt")
ax[1].plot(d[:, 0] * 1e6, d[:, 1], color="#d62728")
ax[1].set_title("150 nH ferrite, FB_B2 after ferrite (as built): loop oscillates in the model", fontsize=10)
ax[1].set_xlabel("us"); ax[1].grid(alpha=0.3)
fig.tight_layout()
fig.savefig(RES / "loadstep_1v0.png", dpi=110)
print("wrote", RES / "power_sequences.png", RES / "loadstep_1v0.png")
