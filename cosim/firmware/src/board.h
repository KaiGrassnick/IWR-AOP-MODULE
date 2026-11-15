/*
 * IWR-AOP-MODULE Rev 1.1: what the firmware needs to know about the board.
 * Sources: STG-HW-SPEC-004 sections 3, 4 and 8, simulation/netlist.json (U300 balls), SWRS237C pin tables.
 */
#ifndef BOARD_H
#define BOARD_H

#include "hw.h"

#define FW_VERSION "0.4.0-cosim"

/* Pin mux, spec 8.3 and 8.6, modes from SWRS237C Table 6-1. */
struct pinmux_entry {
    uint32_t pad;
    uint8_t mode;
    const char *what;
};

#define BOARD_PINMUX                                                   \
    {PAD_V16_RS232_RX, 2, "V16 MSS_UARTA_RX (CP2105 ECI / J400.2)"},   \
    {PAD_U16_RS232_TX, 5, "U16 MSS_UARTA_TX (CP2105 ECI / J400.1)"},   \
    {PAD_E2_SPIB_CLK, 6, "E2 MSS_UARTB_TX (CP2105 SCI / J401.9)"},     \
    {PAD_G3_SPIB_MISO, 2, "G3 I2C_SCL (LP87524)"},                     \
    {PAD_G1_SPIB_MOSI, 2, "G1 I2C_SDA (LP87524)"},                     \
    {PAD_D2_SPIA_CLK, 1, "D2 SPIA_CLK (J401.1)"},                      \
    {PAD_C2_SPIA_CS_N, 1, "C2 SPIA_CS_N (J401.5)"},                    \
    {PAD_F2_SPIA_MOSI, 1, "F2 SPIA_MOSI (J401.3)"},                    \
    {PAD_D1_SPIA_MISO, 1, "D1 SPIA_MISO (J401.4)"},                    \
    {PAD_K3_GPIO_2, 13, "K3 FRAME_START (J401.8)"},                    \
    {PAD_U12_SYNC_IN, 1, "U12 SYNC_IN (J400.3)"},

/* UARTs. 115200 on control. The data port runs 460800: at VCLK 200 MHz the SCI's integer
 * divider gives 921600 only as 892857 Bd (-3.1 %), see cosim/README.md. */
#define CLI_BAUD 115200u
#define DATA_BAUD 460800u

/* LP87524J-Q1 (SNVSAW2B 7.6, SNVU663A Table 3-8) */
#define PMIC_I2C_ADDR 0x60u
#define PMIC_OTP_REV_J 0x72u
#define I2C_SCL_HZ 400000u

/* Buck2 (1.0 V rail) trim window. The radar allows 0.95-1.05 V on 1V0 (SWRS237C 7.3);
 * the PMIC is 2 % accurate (spec 3.2), so the setpoint must stay <= 1.025 V to keep the
 * worst-case DC level under 1.05 V. SNVSAW2B: with VIN > 4 V do not set an output under 1.0 V. */
#define BUCK2_MV_MIN 1000u
#define BUCK2_MV_MAX 1005u   /* cosim/results/replay_summary.txt: higher pushes 1V0 over 1.05 V at frame end */


/* Safety limits (safety.c). The host can tighten these, never loosen them.
 * Radar junction limit 105 C (SWRS237C 7.3); thermal analysis: spec section 10 and
 * simulation/results/thermal_summary.txt (27 K/W with HS300, tau about 150 s). */
#define SAFE_THROTTLE_C_MIN 60
#define SAFE_THROTTLE_C_MAX 90     /* halve the frame rate from here */
#define SAFE_STOP_C_MIN 70
#define SAFE_STOP_C_MAX 100        /* stop transmitting from here: 5 K under the junction limit */
#define SAFE_HYST_C_MIN 3          /* release/resume at least this far under throttle/stop */
#define SAFE_HOLD_MS_MIN 100
#define SAFE_HOLD_MS_MAX 600000
#define SAFE_HOLD_MS_DEFAULT 20000 /* about tau/8 of the board with the heat sink */
#define SAFE_MAX_LEVEL 2u          /* deepest throttle: frame period x4 */
#define SAFE_TEMP_FAILS_MAX 3u
/* RF limits checked at sensorStart. Three TX at once does not fit the 1V0 window in the replay
 * (cosim/results/replay_summary.txt) until it is measured on hardware, so only the debug build
 * allows it. */
#ifdef FW_DEBUG_PMIC
#define SAFE_MAX_TX_PER_CHIRP 3u
#else
#define SAFE_MAX_TX_PER_CHIRP 2u
#endif
#define SAFE_MAX_DUTY_PCT 50u      /* chirping time per frame period */
#define SAFE_MIN_PERIOD_US 10000u

#endif
