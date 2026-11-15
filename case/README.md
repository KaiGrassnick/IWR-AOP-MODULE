# Case design notes

Input for the enclosure of the IWR-AOP-MODULE (Rev 1.1). No case is designed yet; this file collects what the board requires from one. Board facts come from the [hardware specification](../docs/STG-HW-SPEC-004_IWR-AOP-MODULE.md); thermal figures come from the board's thermal model and are estimates until a built unit is measured.

## Requirements

| Item | Value | Source |
|---|---|---|
| Room temperature | 30 °C maximum in normal use | Project requirement |
| Radar junction temperature | 105 °C maximum | TI SWRS237C |
| Field of view to keep clear | ±60° in azimuth and elevation, in front of the radar | TI SWRS237C |
| Operating band | 60–64 GHz (free-space wavelength 4.68–5.00 mm, 4.84 mm at 62 GHz) | TI SWRS237C |

## The three rules

1. **Give the back of the radar a heat path.** Either the heat sink stands in open or moving air, or a thermal pad bridges from the back of the board to a metal wall or plate. A small sealed plastic box with neither is the one arrangement to avoid.
2. **Put only a plain, flat plastic window in front of the radar**, of the right thickness and at the right distance (see [Antenna window](#antenna-window)).
3. **Keep metal, cables and case walls out of the ±60° cone in front of the radar.**

## Heat

The radar is the heat source: 0.6 W idle, 1.25 W and 1.75 W with two transmitters at 24 % and 48 % duty, and about 2.3 W with three transmitters at high duty. The board already spreads the heat well; what limits the temperature is getting it from the surface into the air. Board changes (more copper, more vias) gain nothing in the model, so the case decides.

Maximum air temperature around the board that keeps the radar at or under 105 °C:

| Arrangement | 1.25 W | 1.75 W | 2.3 W | Margin at 30 °C room, 1.75 W |
|---|---|---|---|---|
| Heat sink `HS300` in open still air (as designed) | 71 °C | 58 °C | 43 °C | 28 K |
| Fischer heat sink in place of the Same Sky part (29 instead of 39.7 K/W) | 73 °C | 61 °C | 47 °C | 31 K |
| Taller heat sink, 10–15 mm (about 20 K/W) | 76 °C | 64 °C | 52 °C | 34 K |
| Thermal pad from the back to a metal case wall or plate (5–10 K/W to air) | 80–83 °C | 70–74 °C | 60–65 °C | 40–44 K |
| Gentle airflow over board and heat sink, about 1 m/s | 85 °C | 77 °C | 69 °C | 47 K |
| Small sealed plastic box, no heat path | 52 °C | 32 °C | 9 °C | 2 K |

How to read it:

- The temperature in the table is the air next to the board. In a closed case that air is warmer than the room, so a vented or metal case keeps the margin and a closed plastic one spends it.
- At 30 °C room temperature every arrangement with a heat path has at least 13 K of margin even at 2.3 W. The sealed plastic box is acceptable only up to about 1.25 W (22 K margin); at 1.75 W the margin is gone and 2.3 W is not possible.
- The rows other than the first are what-if runs of `simulation/thermal_board.py` with assumed values: the heat sink resistance shown, a combined surface coefficient of 25 W/m²K for airflow and 5 W/m²K for the sealed box (10 W/m²K in open still air), and 60 K/W for the heat sink inside the sealed box. They rank the options; they do not replace a measurement.

Design guidance:

- **Preferred for a plastic case:** vent slots above and below the heat sink so air can rise past it, with the heat sink fins vertical in the mounted position where possible.
- **Preferred for a metal case or a metal mounting plate:** leave out the heat sink and bridge from the back of the board under the radar to the metal with a thermal gap pad. The pad area is the 14 × 14 mm heat sink outline; the parts under it are 0.55 mm tall 0402 capacitors, so use a soft pad of 1 mm or more and little clamping force. The pad must be electrically insulating.
- **Heat sink part:** the board names both parts, and both fit the same outline. Prefer the Fischer Elektronik ICK BGA 14 × 14 × 6 (29 K/W in natural convection) over the Same Sky HSB03-141406 (39.7 K/W). The board's thermal figures (spec §10) assume the Same Sky part, the worse of the two.
- **Safety net in firmware, in every case:** the test firmware's thermal governor reads the radar's on-chip temperature sensor once per second, halves the frame rate from 90 °C and stops transmitting at 100 °C ([`cosim/`](../cosim/README.md#safety-layer-firmwaresrcsafetyc), spec §8). Radar power follows the transmit duty cycle, so this works whatever the mounting. It cannot take the radar below its idle temperature, so above roughly 80 °C around the board only switching the module off helps.
- Check the finished unit with the on-chip sensor at the intended duty cycle (spec §9, item 8).

## Antenna window

The antennas are inside the radar package and radiate from its top face, on the front of the board. Whatever covers them is part of the antenna. The rules below follow TI's radome guidance for mmWave sensors and the usual half-wave window design; material data at 60 GHz varies by grade, so confirm the permittivity with the supplier or by test.

- **Material.** Unfilled, low-loss plastic: polycarbonate, ABS, PMMA, polypropylene, HDPE or PTFE. No glass or carbon fibre fill, no metallic or carbon-black paint, no metallised film, no label or logo in the window.
- **Thickness.** Half a wavelength inside the material, so reflections from the two faces cancel: t = λ₀ / (2 √εr) with λ₀ = 4.84 mm at 62 GHz.

  | Material | εr (typical) | Window thickness |
  |---|---|---|
  | Polycarbonate | 2.8–2.9 | 1.42–1.45 mm |
  | ABS | 2.7–3.0 | 1.40–1.47 mm |
  | PMMA | 2.6 | 1.50 mm |
  | Polypropylene, HDPE | 2.2–2.3 | 1.60–1.63 mm |
  | PTFE | 2.05 | 1.69 mm |

  Hold the thickness to about ±0.1 mm. A film or wall much thinner than a tenth of the wavelength in the material (under about 0.3 mm) also works. Thicknesses in between are the poor choices.
- **Distance.** Place the inner face of the window a multiple of half a free-space wavelength from the top of the radar package: 2.4 mm or 4.8 mm. This keeps the remaining reflection in phase with the antenna.
- **Shape.** Flat and parallel to the board, with constant thickness over the whole window. No ribs, bosses, steps, screws or text inside it.
- **Size.** The window must cover the ±60° cone from the 15 × 15 mm package. Its half-width is 7.5 mm + 1.73 × distance: at least 32 × 32 mm for a 4.8 mm distance and 24 × 24 mm for 2.4 mm, centred on the radar, plus tolerance.
- **Around the window.** Keep metal parts, cables, screws and thick case walls out of the same cone. The front-side components of the board already stay below it (spec §6). The radar sits on the bottom edge of the board with the notch above it; keep case metal a few millimetres clear of the package on those two sides as well.
- A window narrows the usable field of view a little at the largest angles, because the wave crosses it obliquely there. Check range and angle coverage with the real cover before freezing the tool.

## Board geometry

Use the STEP model for the CAD work: [`docs/3d/IWR-AOP-MODULE.step`](../docs/3d/IWR-AOP-MODULE.step). It includes all parts and the heat sink. Its origin is the top-left corner of the board outline seen from the front, x to the right and y down (negative in the file). The dimension drawing is [`docs/images/dimensions.svg`](../docs/images/dimensions.svg).

Positions from that corner, front view, in mm:

| Item | Side | x | y | Note |
|---|---|---|---|---|
| Board outline | | 0–85.6 | 0–33.7 | 1.6 mm thick, 3.18 mm corner radii, trapezoid notch in the top edge above the radar (spec §6) |
| Radar `U300` | front | 42.83 | 26.03 | 15 × 15 mm, its lower side on the bottom board edge |
| Heat sink `HS300` | back | 42.83 | 26.03 | 14 × 14 × 6 mm, about 6.6 mm above the board with the bond line |
| Mounting holes `H1`, `H4`, `H3` | | 5.00 / 5.00 / 80.65 | 5.00 / 28.75 / 28.75 | 3.2 mm for M3, not plated. There is no hole at the top right |
| Carrier sockets `J401`, `J400` | back | 5.00 / 80.65 | 16.87 | 2.54 mm pitch, 8.5 mm tall, 75.65 mm apart |
| USB-C `J200` | back | 14.98 | 30.84 | Opening at the bottom edge |
| RESET `SW400`, BOOT `SW401` | front | 80.37 / 72.12 | 5.2 | Push buttons |
| JTAG `J301` | back | 75.98 | 4.48 | 2×5, 1.27 mm, development only |

## Mounting and access

- **Fixing.** Three M3 screws. Use plastic or metal standoffs outside the antenna cone; the holes are not connected to ground, so a metal case is not grounded through them.
- **On a carrier.** The module plugs onto the carrier with the two sockets on the back. The socket height of 8.5 mm leaves at least 1.5 mm between the heat sink and the carrier. Confirm the mated height with the purchased parts before fixing standoff lengths (spec §6).
- **USB-C.** The connector opens at the bottom edge on the back side, next to the radar's edge of the board. A cable there must leave downward or backward, not across the front.
- **Buttons.** RESET and BOOT are on the front at the top right. A case for development needs access to them; a product case does not, because the carrier can reset and boot-select the radar.
- **JTAG and test pads.** Needed only on the bench. No opening is required in a product case.
- **Orientation.** The front of the board faces the scene. Azimuth and elevation resolution are similar, so the board can be mounted in either orientation; choose the one that lets warm air rise past the heat sink.

## Open points for the case design

- Target radar duty cycle, which fixes the power row to design for.
- Plastic or metal case, and with that the choice between a vented heat sink and a thermal pad to metal.
- Window material and its permittivity at 60 GHz.
- Ingress protection: a sealed case needs the metal heat path.
