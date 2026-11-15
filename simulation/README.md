# Design simulation

Checks of the Rev 1.1 board, run from the routed layout and the datasheets with **KiCad 10** (pcbnew Python), **ngspice** and Python. Revision 1.1 has not been built, so this is the evidence behind the design. It goes as far as the available models allow; the last section lists what only a real board can show.

## What is modelled and how

| Script | Tool | What it checks |
|---|---|---|
| `extract_netlist.py` | Python | Reads pad → net from `IWR-AOP-MODULE.kicad_pcb` into `netlist.json`. DRC schematic parity is 0, so these nets match the schematic. |
| `check_netlist.py` | Python | Every pin of U117, U600, U601, U301 and U200 and every power and strap ball of U300 against its datasheet pin table. Also strap defaults, divider and contention levels, supervisor thresholds with tolerance, the sense network, and output capacitance per rail. |
| `pdn_extract.py` | pcbnew, scipy | Post-layout DC extraction. Rasterises the real copper of each rail on every layer at 25 µm, adds via barrels and the ferrites, and solves the resistor mesh for the voltage at every radar ball at peak current. Also the ground return, current density and a voltage budget per rail. |
| `run_power.py` | ngspice | USB and carrier power-up, the HOST_DISABLE cycle, carrier reset, USB unplug, a 1 ms 5 V dropout, the `R604` option, and CP2105 back-power through R208, with the TPS386000 supervisor. |
| `run_loadstep.py` | ngspice | Chirp load steps on 1.0 V and 1.8 V and loop stability, with the remote-sense network, Murata's ferrite circuit and the extracted copper. Sweeps ferrite inductance, edge rate and component spread, and compares with the TI EVM and Rev 1.0 arrangements. |
| `run_ripple.py` | ngspice | 4 MHz switching ripple at the radar on 1V0_R and 1V8_R, against SWRS237C Table 7-2. |
| `run_inputs.py` | ngspice | USB-C hot-plug overshoot on USB_VBUS with the damper and TVS, and SYS_5V load-release overshoot against the PMIC input OVP. |
| `thermal_board.py` | pcbnew, scipy | Steady-state board temperature from the real copper of all six layers and every via, for four radar power levels, bare board in still air. |
| `plot.py` | matplotlib | Draws `results/power_sequences.png` and `results/loadstep.png`. |

Models (`spice/`, datasheets in `references/datasheets/`):

- **LP87524J-Q1** timing (`lp87524j_behav.lib`): start delays, ramp, shutdown delay, discharge resistors, GPIO2, PGOOD window, UVLO and OVP, from the J OTP tables in SNVU663A. Loop dynamics are a separate averaged current-mode model in `run_loadstep.py`, calibrated so that TI's datasheet test (0.1 → 2 A in 1 µs with 22 µF + 22 µF) gives −40 mV.
- **TPS386000**: four comparators at 0.4 V with hysteresis, 20 ms delay, open-drain outputs, `MR`.
- **BLM18KG121TH1D** (`bead_blm18kg121.lib`): Murata's published equivalent circuit, with the inductance scaled for DC bias.
- **LM66200**, **Q402**, **CP2105** and the passives as before; ceramic capacitance is derated for DC bias.
- **Copper**: resistances from `pdn_extract.py` at 85 °C.

## Assumptions that change the answers

1. **Radar current edge rate.** TI publishes peak currents, not how fast the 1.0 V current rises at the start of a chirp. Steps are run with 1, 10 and 100 µs edges.
2. **Ferrite inductance under DC bias.** Swept from 100 % down to 10 % of the small-signal value.
3. **LP87524 control loop.** Internal and undocumented; calibrated to the datasheet test as above. FB pin input resistance is assumed to be at least 100 kΩ.
4. **Current split between radar balls.** Assumed equal across the balls of a rail.
5. **Thermal boundary.** One combined convection and radiation coefficient of 10 W/m²K on both faces, no carrier, housing or airflow. Radar power from SWRS237C Table 7-4; the three-transmitter figure is an estimate.
6. **LP87524 pin states below UVLO.** Not specified; modelled as released, which is the worse case for reset.

## Running

```
apt-get install kicad ngspice      # KiCad 10.0.8 and ngspice used
pip install numpy scipy pillow matplotlib
cd simulation
python3 extract_netlist.py && python3 check_netlist.py
python3 pdn_extract.py
python3 run_power.py && python3 run_loadstep.py && python3 run_ripple.py && python3 run_inputs.py
python3 thermal_board.py && python3 plot.py
```

Summaries are written to `results/*_summary.txt` and `results/netlist_checks.txt`.

## Results (Rev 1.1)

**Checks out**

- **Netlist.** 150 of 150 checks pass.
- **DC drop.** Ferrite outputs to radar balls: 13.4 mΩ on 1.0 V, 62 mΩ on 1.8 V. With remote sense, the worst ball is 11.8 mV under the regulation point on 1.0 V at 2.5 A and 50.9 mV on 1.8 V at 0.85 A. Worst-case voltage at the balls, with PMIC accuracy and ground return: 0.964 V (minimum 0.95), 1.712 V (1.71), 1.155 V (1.14) and 3.229 V (3.135). Ground return is 1.6 mΩ.
- **Sequencing and reset.** 1V8 is up at 4.3 ms, 1V0 at 6.1 ms, 1V2 and 3V3 at 9.1–9.6 ms. Reset releases 20 ms after the last rail settles. With HOST_DISABLE it asserts in 12 µs, before any rail leaves its range. On a USB unplug it asserts 17 µs after 3V3_R passes 3.13 V and about 230 µs before the first core rail leaves its range. A 1 ms dropout never releases reset with a core rail down. A carrier reset reaches the radar as 0.31 V.
- **Stability.** Phase margin is 70° or more for every ferrite inductance and component spread tried. DC returns to target at the radar after a step.
- **Load steps with 100 µs edges.** −44 mV on both rails, inside the radar limits. The buck output stays above the supervisor threshold in every case, including 1 µs edges, so load steps cannot cause resets.
- **Ripple.** At most 5.8 µV on 1V0_R (limit 13 µV) and 4.2 µV on 1V8_R (limit 19 µV), down to 10 % ferrite inductance.
- **Hot plug.** 5.0–5.5 V peak on USB_VBUS for every source and cable tried (6 V limit). Rev 1.0 reached 7.9–8.9 V in the same test.
- **Load release.** At most 5.48 V on SYS_5V, under the 5.6 V PMIC OVP minimum.

**Needs attention**

1. **Fast chirp edges.** For a full 0.1 → 2.5 A step with a 1–10 µs edge, 1V0_R dips to 0.83–0.92 V for tens of microseconds, depending on ferrite inductance; 1V8_R dips to 1.69–1.74 V. This is the ferrite filter, not the regulator, and the TI EVM arrangement dips the same way in this model (0.874 V against 0.879 V at 10 µs and 50 % inductance). A two-transmitter step (0.5 → 2.0 A) dips to 0.924 V. Measure the real edge on the first board (spec §9); the fallback is 0 Ω in place of the ferrites.
2. **Thermal.** Bare board in still air: radar junction rise 24 K at 0.6 W, 47 K at 1.25 W, 64 K at 1.75 W and 84 K at 2.3 W. Maximum ambient is about 81, 58, 41 and 21 °C. The board is nearly isothermal, so its surface area is the limit. High-duty use needs a heat path into a carrier or housing.
3. **1.8 V margin.** +2 mV at the worst ball (U2). The firmware raises Buck3 by one 20 mV step (spec §8).
4. **Buck2 output capacitance.** 137 µF nominal against the datasheet's 100 µF figure; the TI EVM has 174 µF. The loop model is stable with it.

## What this cannot show

These need the assembled board or tools and models that aren't available here:

- **RF.** Antenna pattern and the effect of nearby parts, the board edge and a housing. This needs a 3D field solver and TI's package model.
- **The radar's real supply current waveform**, which decides item 1 above.
- **The real LP87524 loop.** The model matches one datasheet point.
- **Switching-edge effects**: EMI, ground bounce and coupling at the buck switch nodes. The extraction here is DC and lumped.
- **Thermal in the real mounting**: convection in an enclosure and contact resistance to a carrier.
- **Assembly**: BGA and QFN solder joints, filled-via planarity under paste.
