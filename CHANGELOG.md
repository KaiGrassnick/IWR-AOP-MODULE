# Changelog

All notable changes are documented here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1] - 2026-11-15

Design revision after a review backed by post-layout extraction and simulation (`simulation/`).

### Added

- TPS386000 supervisor (`U601`) on all four rails drives the radar reset: thresholds just under the radar minimums, 20 ms release delay.
- Remote sense for the 1.0 V and 1.8 V rails: DC from the radar end (`R628`, `R629`), AC from the buck output (`C631`, `C632`).
- Second 1.0 V ferrite in parallel (`FB602`), 22 µF bulk at the buck outputs (`C626`, `C628`) and behind the 1.0 V ferrites (`C630`, `C633`).
- Heat sink `HS300` (14 × 14 × 6 mm) on the back under the radar; in the thermal model it raises the ambient limit from 41 to 58 °C at 1.75 W.
- Radar I²C (balls G3/G1) to the PMIC through `R605`/`R606`, so firmware can trim rails and read PMIC status.
- USB hot-plug damper (`C204` + `R210`) and TVS (`D202`) on `USB_VBUS`.
- Test pads `TP613` (PGOOD), `TP614`/`TP615` (PMIC I²C).
- `simulation/pdn_extract.py` (DC extraction of the routed copper), `simulation/thermal_board.py` (board temperature map), Murata ferrite models, `tools/rev11/` (scripts that generate the schematic and layout changes) and `tools/bom_cost.py`.

### Changed

- `1V0_R` and `1V8_R` run on copper areas in place of narrow inner-layer tracks: 12 mV and 10 mV from the sense point to the worst ball at peak current.
- Ferrites are BLM18KG260TN1D (26 Ω, 6 A, 7 mΩ) in place of the 120 Ω part: the dip from a fast chirp current edge is roughly halved and the ripple limit is still met.
- PMIC PGOOD is no longer a reset source (`R604` not fitted); its window is the same size as a chirp load step.
- PMIC I²C pull-ups go to `3V3_R`.
- All simulations run on the extracted copper of the routed board; specification rewritten, with operating conditions and the model basis in §10.
- Renders, 3D model and BOM cost table regenerated; `manufacturing/` and `tools/render/` scripts run on Linux as well as macOS.

## [1.0] - 2026-10-05

### Added

- First public release of the IWR6843AOP 60 GHz mmWave radar module: 85.6 × 33.7 mm, six layers, antenna-on-package and on-board QSPI boot flash.
- USB-C power and CP2105 dual UART, plus 5 V carrier operation with UART, SPI, control and synchronization signals.
- Ideal-diode input OR, four-rail LP87524J-Q1 PMIC and reset sequencing; BOOT/RESET buttons, Cortex-10 JTAG, debug-boot jumper and rail/control test pads.
- KiCad 10 project, project symbol/footprint/3D libraries, JLCPCB manufacturing package, hardware specification and BOM cost reference.
- Product renders, looping GIF and orbit video, pinout and dimension drawings, and reproducible drawing/render tools.
