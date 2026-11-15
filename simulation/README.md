# Design simulation

Board-level checks of the Rev 1.0 power, enable, reset and interface logic, run with **ngspice** and Python. They don't replace the bring-up measurements in spec §9. They show which of those measurements matter most and what to look for.

## What is modelled and how

| Script | Tool | What it checks |
|---|---|---|
| `extract_netlist.py` | Python | Reads pad → net from `IWR-AOP-MODULE.kicad_pcb` into `netlist.json`. KiCad 10 isn't needed. DRC schematic parity is 0, so these nets match the schematic. |
| `check_netlist.py` | Python | Every pin of U117, U600, U301 and U200 and every power and strap ball of U300 against its datasheet pin table. Also strap defaults, divider and contention levels, and output capacitance per rail. |
| `run_power.py` | ngspice | USB and carrier power-up, the HOST_DISABLE cycle, carrier reset, USB unplug, the §3.3 fallback (R604 out, C605 in), a 1 ms 5 V dropout, and CP2105 back-power through R208. |
| `run_loadstep.py` | ngspice | A 0.1 → 2.5 A chirp step on the 1.0 V rail, plus loop stability with FB600 inside the FB_B2 sense loop. |
| `run_ripple.py` | ngspice | 4 MHz switching ripple at the radar on 1V0_R and 1V8_R, against SWRS237C Table 7-2. |
| `run_inputs.py` | ngspice | USB-C hot-plug overshoot on USB_VBUS, and SYS_5V load-release overshoot against the PMIC input OVP. |
| `plot.py` | matplotlib | Draws `results/power_sequences.png` and `results/loadstep_1v0.png`. |

The parts that matter for these checks are modelled from their datasheets (`references/datasheets/`):

- **LP87524J-Q1** (`spice/lp87524j_behav.lib`) is a behavioral timing model built from the J OTP tables in SNVU663A. It covers the start delays (B3 0 ms, B2 2 ms, B0/B1 5 ms) and the 3.8 mV/µs ramp. It also models the 1 ms shutdown delay on Buck3 and the 230 Ω discharge resistors. Supervision is modelled as follows:
  - GPIO2 follows EN1 with a 5 ms release delay.
  - PGOOD uses a −40/+50 mV window, a 7 µs filter and an 11 ms set delay.
  - UVLO is 2.63/2.60 V. OVP latches.
  - EN1 has a 500 kΩ pulldown.

  This model has no switching. Loop dynamics are in `run_loadstep.py` instead.
- **LM66200** is modelled with a 1 ms delay, a 1.7 ms soft-start, 37 mΩ on-resistance and reverse blocking.
- **Q402** uses the standard 2N3904 SPICE model.
- **CP2105** is modelled as a 3.45 V VDD/VIO, with TXD idling high into R208 and the radar's V16 ESD diode into 3V3_R.
- **Passives** use the values from the netlist. Ceramic capacitance is derated where DC bias matters. ESR and ESL are assumed and marked `[ASSUMED]` in the decks.

## Assumptions that change the answers

1. **FB600/FB601 inductance.** The BLM18KG121TH1D low-frequency inductance under 1–2.5 A DC bias isn't in the repo, so it is swept from 5 to 300 nH. The load-step, stability and ripple results depend strongly on it. Murata's SimSurfing model at the real DC bias would settle it.
2. **LP87524 control loop.** The compensation is internal and undocumented. The controller is calibrated so that TI's own datasheet test (0.1 → 2 A in 1 µs with 22 µF + 22 µF) gives −40 mV. The ferrite network is then added to that calibrated controller.
3. **LP87524 pin states below UVLO and during READ OTP.** The datasheet doesn't specify them, so GPIO2 and PGOOD are modelled as released while VANA < UVLO and until OTP is loaded (1 ms, also assumed).
4. **Radar load currents.** These are estimates: 3V3 40 mA, 1V2 0.5 A, 1V0 0.8 A and 1V8 0.6 A running, and less while held in reset.

## Running

```
apt-get install ngspice            # ngspice 42 used
pip install numpy matplotlib
cd simulation
python3 extract_netlist.py && python3 check_netlist.py
python3 run_power.py && python3 run_loadstep.py && python3 run_ripple.py && python3 run_inputs.py
python3 plot.py
```

Summaries are written to `results/*_summary.txt` and `results/netlist_checks.txt`.

## Results (Rev 1.0)

**Checks out**

- The netlist checks all pass (113 of 113). Every IC pin and every radar power and strap ball is on the net its datasheet function needs. The default boot straps are SOP 001, BOOT gives 101 and JP300 gives 011. VOUT_PA is fed with 1 V for 3-TX mode, and the per-rail capacitance is under 100 µF.
- On power-up (USB and carrier), 1V8 comes up at 4.3 ms, 1V0 at 6.1 ms, and 1V2 and 3V3 at 9.1–9.6 ms. AOP_NRESET releases 11.0 ms after the last rail settles, 18.3 ms after EN1. 1V8 rises before 3V3, as MSS#38A requires.
- With HOST_DISABLE, AOP_NRESET falls 12 µs after the pin goes high, before any rail leaves its range (1V2 is first at 32 µs). Re-enabling repeats the clean start-up.
- A carrier reset on J400.8 reaches the radar at 0.31 V (VIL is 0.65 V). If USB and a carrier contend on UART RX, the carrier wins both ways (0.31 V and 3.0 V).
- On a USB unplug, PGOOD catches the 3V3 sag. Reset asserts 147 µs after the unplug, at the same moment 3V3_R reaches 3.13 V and about 350 µs before the core rails drop.
- CP2105 back-power through R208 is harmless at normal start-up: 3V3_R reaches only 0.2 V before Buck0 starts. If the rails stay off with USB attached, Buck0's discharge resistor holds 3V3_R at 0.5 V. Without that resistor, it would reach 2.6 V.
- A load release at a 5.5 V input reaches 5.48 V, under the 5.6 V minimum PMIC OVP threshold.

**Needs attention** (details in `results/`)

1. **The 1.0 V chirp step against the PGOOD window.** A 2.5 A step (3-TX peak) dips 52 mV even with a trace-like 5 nH between the PMIC and the radar. That is past the −29 mV minimum and −40 mV typical PGOOD thresholds, so frame-time resets are likely unless the real chirp current edge is much slower.
2. **FB600/FB601 inside the FB_B2/FB_B3 sense loop.** With about 50 nH or more of ferrite inductance, the calibrated loop has almost no phase margin and oscillates in the model. Sensing before the ferrite is stable, but then the radar sees a 62 mV DC drop and 100–200 mV dips.
3. **Ripple against the ferrite.** Switching ripple meets Table 7-2 only if the ferrite stays above roughly 150 nH (1V8) or 300 nH (1V0) at operating current. That conflicts with item 2, so the real bead data decides it.
4. **The §3.3 fallback (R604 out, C605 in) loses power-fail reset.** After a USB unplug, the core rails collapse within 0.2 ms, but AOP_NRESET stays high for another 19 ms.
5. **A short 5 V dropout can release reset with the core rails down.** This happens if the PMIC releases GPIO2 and PGOOD below UVLO or during OTP load, which the datasheet doesn't specify. Fitting C605 1 µF while keeping R604 removes this window in the model and delays reset release by about 6.5 ms.
6. **USB-C hot plug.** USB_VBUS has only 2 µF of ceramic and no TVS. A live source with a 0.3–2 m cable rings to about 8 V, above the LM66200's 6 V absolute maximum on VIN1 and the CP2105's ratings. Compliant USB-C sources switch VBUS on softly; legacy and cheap sources don't.
