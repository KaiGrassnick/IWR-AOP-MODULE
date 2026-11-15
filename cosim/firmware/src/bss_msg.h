/*
 * Messages between the MSS and the radar subsystem (BSS) in the co-simulation.
 *
 * The transport is the real one: message memory and INT_TRIG/INT_ACK registers of the
 * MSS<->BSS mailboxes (SWRU522E chapter 18). The message CONTENT is not TI's: on silicon
 * the BSS runs TI's closed firmware and speaks the mmWaveLink protocol, which is not
 * published at register level. Each message below stands for the mmWaveLink call named
 * next to it; on hardware, radar_link.c is replaced by calls into TI's mmWaveLink library.
 * cosim/sim/board/bss.py decodes the same layout.
 */
#ifndef BSS_MSG_H
#define BSS_MSG_H

#include <stdint.h>

#define BSS_MSG_MAGIC 0x4C4D4D43u /* "CMML" */
#define BSS_MSG_WORDS 64u

enum bss_msg_id {
    BSS_CHANNEL_CFG = 0x01,   /* rlSetChannelConfig: rxMask, txMask */
    BSS_LDO_BYPASS_CFG = 0x02,/* rlRfSetLdoBypassConfig: ldoBypassEnable, supplyMonIrDrop, ioSupplyIndicator */
    BSS_RF_INIT = 0x03,       /* rlRfInit: RF calibrations */
    BSS_PROFILE_CFG = 0x04,   /* rlSetProfileConfig: idle, adcStart, rampEnd, txStart (10 ns), samples, ksps, slope kHz/us */
    BSS_CHIRP_CFG = 0x05,     /* rlSetChirpConfig: startIdx, endIdx, txMask */
    BSS_FRAME_CFG = 0x06,     /* rlSetFrameConfig: chirpStart, chirpEnd, loops, frames (0 = infinite), period us */
    BSS_SENSOR_START = 0x07,  /* rlSensorStart */
    BSS_SENSOR_STOP = 0x08,   /* rlSensorStop */
    BSS_GET_TEMP = 0x09,      /* rlRfGetTemperatureReport: returns mdegC */

    BSS_RESPONSE = 0x8000,    /* OR-ed into the id of a response: word3 = status, then payload */

    BSS_EV_READY = 0x0100,    /* BSS booted (rlDevicePowerOn async event) */
    BSS_EV_RF_INIT_DONE = 0x0101,
    BSS_EV_FRAME_DONE = 0x0102,/* frame number, chirps, then up to 8 points (range mm, speed mm/s, azimuth mdeg) */
    BSS_EV_ERROR = 0x0103,    /* error code */
    BSS_EV_STOPPED = 0x0104,
};

enum bss_status {
    BSS_OK = 0,
    BSS_ERR_LDO_NOT_SET = 1,      /* RF powered before the LDO bypass was configured */
    BSS_ERR_LDO_MISMATCH = 2,     /* LDO setting does not match the board's external 1.0 V on VOUT_PA */
    BSS_ERR_3TX_NEEDS_BYPASS = 3, /* SWRS237C Table 7-1 note 1 */
    BSS_ERR_PROFILE = 4,
    BSS_ERR_FRAME = 5,
    BSS_ERR_STATE = 6,
    BSS_ERR_SUPPLY = 7,           /* a rail outside the radar's operating range */
    BSS_ERR_UNKNOWN = 8,
};

/* word layout: [0] magic, [1] id, [2] seq, [3..] payload */

#endif
