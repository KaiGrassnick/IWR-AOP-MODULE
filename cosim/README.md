# Firmware and hardware co-simulation

Runs radar firmware, compiled for the IWR6843's Cortex-R4F, on a model of the Rev 1.1 board: power entry, LP87524J PMIC, TPS386000 supervisor, reset net, boot ROM and QSPI flash, CP2105 bridge and a carrier on J400/J401. The chirp load the firmware configures is then replayed through the ngspice model of the 1.0 V and 1.8 V rails from `simulation/`. The aim is to find mistakes in how firmware and board work together before the first boards are built, and to know what to look at on the bench (spec §9).

```
firmware/            Bare-metal test firmware for the MSS (C, arm-none-eabi-gcc)
sim/                 Board model in Python: CPU, peripherals, power tree, PMIC, radar stand-in
run_cosim.py         20 scenarios (power-up, flashing, resets, dropouts, trim, 3-TX stress, thermal, host limits)
replay/              ngspice replay of recorded frames on 1V0 and 1V8
results/             cosim_summary.txt, replay_summary.txt, plots, frame traces
```

## What is real and what is a model

| Part | How it is simulated | How close to the hardware |
|---|---|---|
| R4F core | Unicorn 2.1 (QEMU) Cortex-R5F, ARM/Thumb-2 + VFPv3-D16 | Same instruction set and exception model. Not cycle accurate: one instruction per 5 ns. Timing that matters comes from the peripheral models. |
| MSS peripherals | Register-level models of VIM, RTI, IOMUX, SCI-A/B, I2C and the BSS mailboxes, from TI SWRU522E | Only the features the firmware uses. Any access to an unmodelled address is counted and fails the scenario. |
| Pin mux | Each pad's function is checked against the board (spec §8.3/8.6): UART, I2C and FRAME_START only work on the right ball in the right mode. | Exact for the pins used. |
| I2C to the PMIC | Bit-level timing from the I2C prescaler/divider, LP87524J register file with J-variant OTP defaults (SNVU663A), W1C flags, RESET_REG, SW_RESET, auto-increment, current readback. | Register behaviour from the datasheet; rail changes follow the 3.8 mV/µs slew. |
| Power tree, supervisor, reset | Piecewise-linear rails with times taken from the ngspice power-up/unplug/disable runs (`simulation/results/power_summary.txt`). Supervisor thresholds and 20 ms delay; GPIO2, HOST_NRESET, RESET button. | Reset release at 29.64 ms vs 29.68 ms in ngspice. Fast transients are in the replay, not here. |
| Boot ROM and flash | SOP pins pick QSPI, UART flashing or debug mode. QSPI boot fails with QE clear or WIP busy (errata MSS#45); 5 ms ROM overhead [assumed]. | Behavioural. |
| Radar subsystem (BSS) | **Stand-in.** TI's BSS firmware and mmWaveLink are closed, so this answers the firmware over the real mailbox hardware with a simple message set (`firmware/src/bss_msg.h`, one message per mmWaveLink call the firmware would make). It enforces the board rules: LDO bypass before RF power-up, three TX at once only in bypass, all rails in range. It runs the frame timing and turns it into current per rail (SWRS237C §7.7), and reports a synthetic scene. | **No RF, no DSP, no calibration.** Real firmware will use mmWaveLink; the message layer here must be replaced by it. |
| Host side | CP2105 channels (115200 / 460800, 48 MHz baud generator [assumed]) and carrier UARTs, with a framing check for baud mismatch. | |
| Rails under chirp load | ngspice: the calibrated averaged current-mode model, Murata bead model and extracted copper of `simulation/run_loadstep.py`, driven by the recorded frame. | As good as `simulation/` (spec §10), plus the current split below. |

Assumptions that are not from a datasheet are marked `[assumption]` in the code. The ones that change results:

- Per-TX current share on 1V0 (0.5 A per TX on top of 1.0 A with RF on) and its edge time (1 µs and 10 µs run). Table 7-3 gives only totals.
- BSS boot (8 ms) and RF init (30 ms) times.
- PMIC loses its registers 0.3 ms after 5 V goes away (UVLO); shorter dropouts keep them.

## Running it

Needs `arm-none-eabi-gcc` (Ubuntu: `gcc-arm-none-eabi`), Python 3.10+ with `pip install unicorn==2.1.4 numpy matplotlib`, and ngspice for the replay. `simulation/results/` must be present (it is in the repository).

```
make -C cosim/firmware                 # build/fw.elf, and build/fw_debug.elf with raw PMIC writes
python3 cosim/run_cosim.py              # about 3 min, writes results/cosim_summary.txt and results/traces/
python3 cosim/replay/replay_rails.py    # about 1 min, writes results/replay_summary.txt and replay_*.png
python3 cosim/run_cosim.py usb_boot     # one scenario, printed only
```

## Results

All 20 scenarios pass (`results/cosim_summary.txt`):

- **Power-up and boot**, from USB or from the carrier: reset releases at 29.64 ms, the ROM starts the image 6 ms later, the firmware reads the PMIC at 400 kHz (OTP_REV 0x72) and the CLI answers on the right port. No rule violations, no unmodelled register accesses.
- **Streaming**: the default TDM-MIMO profile and a three-TX-at-once profile start (LDO bypass first), stream TI-style packets at 460800 Bd with no framing errors, and the PMIC current readback reports 1040 mA (one TX) and 1500 mA (three TX) on Buck2 during chirps.
- **Trim**: the firmware sets Buck2 to the trim (at most 1005 mV) only while the radar runs and goes back to 1000 mV on stop. After a HOST_NRESET pulse, a HOST_DISABLE pulse or a 0.1 ms USB dropout the PMIC keeps its registers. The firmware sees that (RESET_REG clear) at boot and restores 1000 mV. After a 1 ms dropout the PMIC reloads its OTP values.
- **Flashing**: SOP 101 programming then RESET boots. Without the QE bit the board does not boot (as in spec §4.2). RESET pressed within 40 ms of the QE write also fails to boot (MSS#45); a second reset boots.
- **Firmware limits**: trims outside 1000-1005 mV are refused. The raw PMIC write (`debugUnlock`, `pmicWrite`) exists only in `fw_debug.elf`.

Two findings for the firmware and the bench:

1. **A bad PMIC write locks the board until power is removed.** With Buck2 written to 855 mV, the supervisor holds the radar in reset, so the firmware cannot undo the write. HOST_DISABLE does not help, because EN1 does not reload the PMIC registers. Only removing 5 V for more than about 0.3 ms does. So the normal build only writes the PMIC through range-checked calls, and the raw write command is in the debug build only. A carrier that controls the module's 5 V can recover it.
2. **A 1025 mV trim pushes 1V0 over 1.05 V when the chirps stop** (`results/replay_summary.txt`, `replay_1V0.png`). With the remote sense, 1V0 at the radar already sits at 1.000 V and the dip and overshoot are about the same size. Raising the setpoint moves both up: at 1025 mV the first version of this replay reached 1.077 V for about 10 µs at frame end with the default profile and Table 7-3 worst-case current. The firmware's trim window is therefore now 1000-1005 mV; at 1005 mV the worst case is 1.057 V for 6 µs (1 µs edges, bead at zero bias) and the other three cases pass. Without the trim, the default profile stays inside 0.95-1.05 V in 3 of 4 cases (worst: 1.052 V for 3 µs). Each case lists the setpoint range that would fit, which is about 0.98-1.00 V. With three TX at once and 1 µs edges, no setpoint fits: there is 145 mV peak to peak, against a 100 mV window. Recommendation: leave Buck2 at 1000 mV until the first boards are measured (spec §9). Then set the trim from the measured dip and overshoot at the radar, not from the DC level. The excursions are far below the 1.4 V absolute maximum. 1V8 passes in every case.

The 921600 Bd data rate is marginal: the SCI has no fractional divider and makes 892857 Bd (-3.3 % against the CP2105). The firmware uses 460800 (+0.3 %).

## Safety layer (firmware/src/safety.c)

The firmware is safe by default: the limits below are compiled in (`SAFE_*` in `firmware/src/board.h`). A host can make them stricter but never looser. No command switches the governor off.

**Thermal governor.** The firmware reads the radar's temperature sensor on every frame and the PMIC status on every 10th frame.

| Condition | Action |
|---|---|
| radar ≥ 90 °C (`thermal.throttle_c`) | halve the frame rate (frame period ×2, then ×4); at most one step per hold time |
| radar ≤ 85 °C (`thermal.release_c`) | undo one step, again at most one per hold time |
| radar ≥ 100 °C (`thermal.stop_c`), or the LP87524 die-temperature warning | stop the radar, so TX are off. It restarts by itself at ×4 once under 90 °C (`thermal.resume_c`) and the PMIC warning has cleared |
| 3 temperature reads fail in a row | stop the radar (no reading, no RF); the host must send `sensorStart` again |
| `sensorStart` while already over 90 °C | wait, and start once cool |

The radar's limit is 105 °C junction (SWRS237C 7.3), so the defaults keep 5 K of margin. The hold time (`thermal.hold_ms`, default 20 s) lets the heat sink respond before the next step; the board's thermal time constant is about 150 s. Stopping the radar cannot take it below its idle temperature: about 21 K over ambient with HS300 and 28 K without it (thermal_summary.txt). Above roughly 80 °C ambient with the heat sink, only the host can help, by switching the module off.

**Host configuration.** The UART CLI is the host interface for now; a binary protocol can map onto the same table later. `cfg` lists every setting with its bounds, `cfg <key>` reads one and `cfg <key> <value>` sets one. `thermal` shows the governor's state. Settings outside the bounds are refused (`Error -51`), and so is a pair that would lose its hysteresis (`-52`, at least 3 K between throttle and release and between stop and resume). The telemetry TLV 0x4D01 now carries the governor state and throttle level in its 8th word.

| key | bounds | default |
|---|---|---|
| `thermal.throttle_c` | 60–90 °C | 90 |
| `thermal.release_c` | 50–87 °C | 85 |
| `thermal.stop_c` | 70–100 °C | 100 |
| `thermal.resume_c` | 50–97 °C | 90 |
| `thermal.hold_ms` | 100 ms–600 s | 20 s |
| `pmic.trim_mv` | 0–5 mV above 1000 mV | 0 |

Radar settings still use the TI-style commands (`profileCfg`, `chirpCfg`, `frameCfg`). `sensorStart` checks them against these limits:
- at most 2 TX per chirp (`-53`); three at once only in `fw_debug.elf`, until the 1V0 replay is confirmed on hardware
- chirps take at most 50 % of the frame period (`-54`)
- frame period at least 10 ms (`-55`)

**Simulation** (`results/thermal_governor.png`). These scenarios compress the thermal time constant to 2 s and the hold time to 300 ms, in the same ratio, so a run takes seconds instead of minutes. The load is a heavy profile of 2 TX per chirp, 129 chirps every 33 ms, which is 1.49 W unthrottled.
- **55 °C ambient with the heat sink:** unthrottled, the radar would settle at 96 °C. The governor throttles once and it settles at 86 °C while data keeps streaming.
- **66 °C ambient without the heat sink:** it throttles twice and stops at 100.0 °C. It stays stopped while idle heat alone keeps it at 94 °C. When the ambient drops to 55 °C it restarts at ×4 and holds at 90 °C.

Other scenarios cover a host that tightens the limits (throttle at 70 °C, stop at 74 °C), the PMIC thermal warning, failing temperature reads, a start while hot, and every out-of-bounds setting.

## Limits

- No RF, no BSS firmware, no calibration and no DSP. Anything that depends on them must be tested on hardware.
- CPU timing is not cycle accurate, so busy-wait loops run at a different speed than on the R4F. Firmware timing should use RTI, as this firmware does.
- The current model is from datasheet tables, not from a measured board. Measure 1V0 at the radar during a frame (spec §9) and put the measured edge times into `replay/replay_rails.py`.
- Renode was the first choice for the CPU side but could not be run in this environment; Unicorn runs the same code, and the peripheral models here are plain Python.
