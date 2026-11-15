# Firmware and hardware co-simulation

Runs radar firmware, compiled for the IWR6843's Cortex-R4F, on a model of the Rev 1.1 board: power entry, LP87524J PMIC, TPS386000 supervisor, reset net, boot ROM and QSPI flash, CP2105 bridge and a carrier on J400/J401. The chirp load the firmware configures is then replayed through the ngspice model of the 1.0 V and 1.8 V rails from `simulation/`. The aim is to find mistakes in how firmware and board work together before the first boards are built, and to know what to look at on the bench (spec §9).

```
firmware/            Bare-metal test firmware for the MSS (C, arm-none-eabi-gcc)
sim/                 Board model in Python: CPU, peripherals, power tree, PMIC, radar stand-in
run_cosim.py         14 scenarios (power-up, flashing, resets, dropouts, trim, 3-TX stress)
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
python3 cosim/run_cosim.py              # about 40 s, writes results/cosim_summary.txt and results/traces/
python3 cosim/replay/replay_rails.py    # about 1 min, writes results/replay_summary.txt and replay_*.png
python3 cosim/run_cosim.py usb_boot     # one scenario, printed only
```

## Results

All 14 scenarios pass (`results/cosim_summary.txt`):

- **Power-up and boot**, from USB or from the carrier: reset releases at 29.64 ms, the ROM starts the image 6 ms later, the firmware reads the PMIC at 400 kHz (OTP_REV 0x72) and the CLI answers on the right port. No rule violations, no unmodelled register accesses.
- **Streaming**: the default TDM-MIMO profile and a three-TX-at-once profile start (LDO bypass first), stream TI-style packets at 460800 Bd with no framing errors, and the PMIC current readback reports 1040 mA (one TX) and 1500 mA (three TX) on Buck2 during chirps.
- **Trim**: the firmware sets Buck2 to the trim (at most 1005 mV) only while the radar runs and goes back to 1000 mV on stop. After a HOST_NRESET pulse, a HOST_DISABLE pulse or a 0.1 ms USB dropout the PMIC keeps its registers. The firmware sees that (RESET_REG clear) at boot and restores 1000 mV. After a 1 ms dropout the PMIC reloads its OTP values.
- **Flashing**: SOP 101 programming then RESET boots. Without the QE bit the board does not boot (as in spec §4.2). RESET pressed within 40 ms of the QE write also fails to boot (MSS#45); a second reset boots.
- **Firmware limits**: trims outside 1000-1005 mV are refused. The raw PMIC write (`debugUnlock`, `pmicWrite`) exists only in `fw_debug.elf`.

Two findings for the firmware and the bench:

1. **A bad PMIC write locks the board until power is removed.** With Buck2 written to 855 mV, the supervisor holds the radar in reset, so the firmware cannot undo the write. HOST_DISABLE does not help, because EN1 does not reload the PMIC registers. Only removing 5 V for more than about 0.3 ms does. So the normal build only writes the PMIC through range-checked calls, and the raw write command is in the debug build only. A carrier that controls the module's 5 V can recover it.
2. **A 1025 mV trim pushes 1V0 over 1.05 V when the chirps stop** (`results/replay_summary.txt`, `replay_1V0.png`). With the remote sense, 1V0 at the radar already sits at 1.000 V and the dip and overshoot are about the same size. Raising the setpoint moves both up: at 1025 mV the first version of this replay reached 1.077 V for about 10 µs at frame end with the default profile and Table 7-3 worst-case current. The firmware's trim window is therefore now 1000-1005 mV; at 1005 mV the worst case is 1.057 V for 6 µs (1 µs edges, bead at zero bias) and the other three cases pass. Without the trim, the default profile stays inside 0.95-1.05 V in 3 of 4 cases (worst: 1.052 V for 3 µs). Each case lists the setpoint range that would fit, which is about 0.98-1.00 V. With three TX at once and 1 µs edges, no setpoint fits: there is 145 mV peak to peak, against a 100 mV window. Recommendation: leave Buck2 at 1000 mV until the first boards are measured (spec §9). Then set the trim from the measured dip and overshoot at the radar, not from the DC level. The excursions are far below the 1.4 V absolute maximum. 1V8 passes in every case.

The 921600 Bd data rate is marginal: the SCI has no fractional divider and makes 892857 Bd (-3.3 % against the CP2105). The firmware uses 460800 (+0.3 %).

## Limits

- No RF, no BSS firmware, no calibration and no DSP. Anything that depends on them must be tested on hardware.
- CPU timing is not cycle accurate, so busy-wait loops run at a different speed than on the R4F. Firmware timing should use RTI, as this firmware does.
- The current model is from datasheet tables, not from a measured board. Measure 1V0 at the radar during a frame (spec §9) and put the measured edge times into `replay/replay_rails.py`.
- Renode was the first choice for the CPU side but could not be run in this environment; Unicorn runs the same code, and the peripheral models here are plain Python.
