/*
 * IWR-AOP-MODULE firmware (co-simulation build).
 *
 * Boot: pin mux, interrupts, 1 ms tick, UARTA CLI at 115200 (CP2105 ECI / J400), UARTB data
 * at 460800 (CP2105 SCI / J401.9), PMIC check over I2C, wait for the radar subsystem.
 * CLI: TI mmWave demo style (channelCfg, profileCfg, chirpCfg, frameCfg, sensorStart,
 * sensorStop) plus board commands (status, pmic, pmicTrim, buck2, load, temp) and the host
 * configuration (cfg, thermal; limits in board.h, governor in safety.c).
 * Data: one TI-style packet per frame on UARTB (magic 0102 0304 0506 0708), with the
 * points TLV and a board telemetry TLV.
 */
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "bss_msg.h"
#include "pmic.h"
#include "radar.h"
#include "safety.h"
#include "soc.h"

#define PROMPT "aop:/>"

#ifdef FW_DEBUG_PMIC
static bool debug_unlocked;
#endif
static uint32_t telemetry_buck2_ma, telemetry_buck3_ma;        /* between frames */
static uint32_t telemetry_buck2_frame_ma, telemetry_buck3_frame_ma; /* while chirping */
static volatile uint32_t frame_start_pending, frame_starts;

/* RADARSS frame start (VIM 98): used to time the PMIC current measurement into the chirps */
static void frame_start_isr(void)
{
    frame_start_pending = 1;
    frame_starts++;
}

void fault(uint32_t kind, uint32_t lr)
{
    static const char *const names[] = {"?", "undefined instruction", "svc", "prefetch abort", "data abort"};
    uprintf(&sci_cli, "\nFAULT: %s at 0x%08x\n", names[kind < 5 ? kind : 0], lr);
    for (;;)
        cpu_wfi();
}

/* ------------------------------------------------------------------ helpers */
static int split(char *line, char **argv, int max)
{
    int n = 0;
    for (char *p = strtok(line, " \t"); p && n < max; p = strtok(0, " \t"))
        argv[n++] = p;
    return n;
}

/* "57.14" -> 5714 (hundredths) */
static uint32_t centi(const char *s)
{
    uint32_t ip = 0, fp = 0, fd = 0;
    while (*s >= '0' && *s <= '9')
        ip = ip * 10u + (uint32_t)(*s++ - '0');
    if (*s == '.') {
        s++;
        while (*s >= '0' && *s <= '9' && fd < 2) {
            fp = fp * 10u + (uint32_t)(*s++ - '0');
            fd++;
        }
    }
    while (fd++ < 2)
        fp *= 10u;
    return ip * 100u + fp;
}

static void print_pmic(void)
{
    if (!pmic.present) {
        uprintf(&sci_cli, "PMIC: not answering at 0x%02x\n", PMIC_I2C_ADDR);
        return;
    }
    static const char *const rail[4] = {"3V3_R ", "1V2_R ", "1V0   ", "1V8   "};
    uprintf(&sci_cli, "PMIC: LP87524J OTP_REV 0x%02x, I2C %u kHz\n", pmic.otp_rev, i2c_scl_hz() / 1000u);
    for (unsigned b = 0; b < 4; b++)
        uprintf(&sci_cli, "  Buck%u %s %4u mV  CTRL1 0x%02x\n", b, rail[b], pmic_vset_to_mv(pmic.vout[b]), pmic.ctrl1[b]);
    uprintf(&sci_cli, "  TOP_STAT 0x%02x  BUCK_0_1_STAT 0x%02x  BUCK_2_3_STAT 0x%02x\n", pmic.top_stat, pmic.stat01,
            pmic.stat23);
    uprintf(&sci_cli, "  flags cleared: INT_TOP1 0x%02x INT_TOP2 0x%02x INT_BUCK 0x%02x 0x%02x\n", pmic.int_top1,
            pmic.int_top2, pmic.int_b01, pmic.int_b23);
}

/* ------------------------------------------------------------------ data packets */
static void send_frame_packet(const uint32_t *ev)
{
    /* TI mmWave demo output: header of 40 bytes, then TLVs (type, length, payload) */
    uint32_t npts = ev[5] > 8 ? 8 : ev[5];
    uint32_t tlv1 = 8 + 16 * npts, tlv2 = 8 + 32;
    uint32_t total = 40 + tlv1 + tlv2;
    total = (total + 31u) & ~31u; /* padded to 32 bytes like the TI demo */
    static const uint16_t magic[4] = {0x0102, 0x0304, 0x0506, 0x0708};
    uint32_t hdr[8] = {0x03050004u, total, 0xA6843u, ev[3], time_us(), npts, 2u, 0u};
    sci_write(&sci_data, magic, 8);
    sci_write(&sci_data, hdr, 32);
    uint32_t t1[2] = {1u, 16 * npts};
    sci_write(&sci_data, t1, 8);
    for (uint32_t i = 0; i < npts; i++) {
        /* range (mm), speed (mm/s), azimuth (mdeg) -> x, y, z, v in metres */
        float r = (float)ev[6 + 3 * i] * 1e-3f, v = (float)(int32_t)ev[7 + 3 * i] * 1e-3f;
        float az = (float)(int32_t)ev[8 + 3 * i] * 1.745329e-5f;
        float x = r * az, y = r * (1.0f - 0.5f * az * az), z = 0.0f;
        float p[4] = {x, y, z, v};
        sci_write(&sci_data, p, 16);
    }
    uint32_t t2[2] = {0x4D01u, 32u};
    uint32_t tel[8] = {pmic_vset_to_mv(pmic.vout[2]), telemetry_buck2_frame_ma, telemetry_buck2_ma,
                       telemetry_buck3_frame_ma, telemetry_buck3_ma, (uint32_t)radar.temp_mc, safety.pmic_flags,
                       ((uint32_t)safety.state << 8) | safety.level};
    sci_write(&sci_data, t2, 8);
    sci_write(&sci_data, tel, 32);
    for (uint32_t n = 40 + tlv1 + tlv2; n < total; n++)
        sci_putc(&sci_data, 0);
}

/* ------------------------------------------------------------------ CLI */
static void cli_exec(char *line)
{
    char *argv[16];
    int argc = split(line, argv, 16);
    if (!argc)
        return;
    const char *cmd = argv[0];
    int st = 0;
    bool done = true;

    if (!strcmp(cmd, "version")) {
        uprintf(&sci_cli, "IWR-AOP-MODULE fw %s, data UART %u Bd (set %u)\n", FW_VERSION, DATA_BAUD, sci_data.baud_set);
    } else if (!strcmp(cmd, "status")) {
        uprintf(&sci_cli, "radar: bss %s, rf %s, %s, frames %u, errors %u (last %u), temp %d mC\n",
                radar.bss_ready ? "ready" : "NOT READY", radar.rf_init ? "init" : "off",
                radar.running ? "running" : "stopped", radar.frames_done, radar.errors, radar.last_error,
                (int)radar.temp_mc);
        uprintf(&sci_cli, "uart rx errors %u, uptime %u ms\n", sci_cli.rx_errors, time_ms());
    } else if (!strcmp(cmd, "pmic")) {
        st = pmic_probe();
        if (!st)
            st = pmic_poll();
        print_pmic();
    } else if (!strcmp(cmd, "cfg")) {
        if (argc == 1) {
            for (unsigned i = 0; i < cfg_count; i++) {
                const cfg_item_t *c = &cfg_items[i];
                uprintf(&sci_cli, "%-20s %6d %-2s  [%d..%d, default %d]  %s\n", c->key, (int)*c->val, c->unit,
                        (int)c->min, (int)c->max, (int)c->def, c->help);
            }
        } else if (argc == 2) {
            const cfg_item_t *c = cfg_find(argv[1]);
            if (c)
                uprintf(&sci_cli, "%s %d\n", c->key, (int)*c->val);
            else
                st = SAFE_ERR_KEY;
        } else {
            st = cfg_set(argv[1], (int32_t)atoi(argv[2]));
        }
    } else if (!strcmp(cmd, "thermal")) {
        uprintf(&sci_cli, "thermal: %s, level %u (frame period x%u), radar %d mC, PMIC warning %s\n",
                safety_state_name(), safety.level, 1u << safety.level, (int)safety.temp_mc,
                safety.pmic_hot ? "yes" : "no");
        uprintf(&sci_cli, "throttle %d C, release %d C, stop %d C, resume %d C, hold %d ms; throttled %u, stopped %u, restarted %u\n",
                (int)cfg_throttle_c, (int)cfg_release_c, (int)cfg_stop_c, (int)cfg_resume_c, (int)cfg_hold_ms,
                safety.throttles, safety.stops, safety.restarts);
    } else if (!strcmp(cmd, "pmicTrim") && argc >= 2) {
        uint32_t mv = (uint32_t)atoi(argv[1]);
        if (mv && (mv < BUCK2_MV_MIN || mv > BUCK2_MV_MAX))
            st = SAFE_ERR_RANGE;
        else
            st = cfg_set("pmic.trim_mv", mv ? (int32_t)(mv - BUCK2_MV_MIN) : 0);
    } else if (!strcmp(cmd, "buck2") && argc >= 2) {
        st = pmic_buck2_set_mv((uint32_t)atoi(argv[1]));
    } else if (!strcmp(cmd, "load")) {
        st = pmic_load_ma(2, &telemetry_buck2_ma);
        if (!st)
            st = pmic_load_ma(3, &telemetry_buck3_ma);
        uprintf(&sci_cli, "now: Buck2 (1V0) %u mA, Buck3 (1V8) %u mA\n", telemetry_buck2_ma, telemetry_buck3_ma);
        uprintf(&sci_cli, "last frame: Buck2 %u mA, Buck3 %u mA\n", telemetry_buck2_frame_ma, telemetry_buck3_frame_ma);
    } else if (!strcmp(cmd, "temp")) {
        st = radar_get_temp(&radar.temp_mc);
        uprintf(&sci_cli, "radar temperature %d mC\n", (int)radar.temp_mc);
#ifdef FW_DEBUG_PMIC
    /* Raw PMIC writes bypass every range check. A bad Buck write makes the supervisor hold the
     * radar in reset and only removing 5 V recovers the board, so this is a debug build only. */
    } else if (!strcmp(cmd, "debugUnlock")) {
        debug_unlocked = true;
    } else if (!strcmp(cmd, "pmicWrite") && argc >= 3) {
        if (!debug_unlocked)
            st = -30;
        else
            st = pmic_raw_write((uint8_t)strtoul(argv[1], 0, 0), (uint8_t)strtoul(argv[2], 0, 0));
#endif
    } else if (!strcmp(cmd, "channelCfg") && argc >= 3) {
        radar_cfg.rx_mask = (uint32_t)atoi(argv[1]);
        radar_cfg.tx_mask = (uint32_t)atoi(argv[2]);
    } else if (!strcmp(cmd, "profileCfg") && argc >= 12) {
        /* id startFreq idleTime adcStartTime rampEndTime txPower txPhase slope txStartTime samples ksps ... */
        radar_cfg.idle_10ns = centi(argv[3]);
        radar_cfg.adc_start_10ns = centi(argv[4]);
        radar_cfg.ramp_end_10ns = centi(argv[5]);
        radar_cfg.slope_khz_us = centi(argv[8]) * 10u;
        radar_cfg.tx_start_10ns = centi(argv[9]);
        radar_cfg.samples = (uint32_t)atoi(argv[10]);
        radar_cfg.ksps = (uint32_t)atoi(argv[11]);
    } else if (!strcmp(cmd, "chirpCfg") && argc >= 9) {
        uint32_t a = (uint32_t)atoi(argv[1]), b = (uint32_t)atoi(argv[2]);
        for (uint32_t i = a; i <= b && i < 8; i++)
            radar_cfg.chirp_tx_mask[i] = (uint32_t)atoi(argv[8]);
        if (a < radar_cfg.chirp_start || radar_cfg.chirp_start > radar_cfg.chirp_end)
            radar_cfg.chirp_start = a;
        if (b > radar_cfg.chirp_end)
            radar_cfg.chirp_end = b;
    } else if (!strcmp(cmd, "chirpReset")) {
        radar_cfg.chirp_start = 1;
        radar_cfg.chirp_end = 0;
        memset(radar_cfg.chirp_tx_mask, 0, sizeof radar_cfg.chirp_tx_mask);
    } else if (!strcmp(cmd, "frameCfg") && argc >= 6) {
        radar_cfg.frame_chirp_start = (uint32_t)atoi(argv[1]);
        radar_cfg.frame_chirp_end = (uint32_t)atoi(argv[2]);
        radar_cfg.loops = (uint32_t)atoi(argv[3]);
        radar_cfg.frames = (uint32_t)atoi(argv[4]);
        radar_cfg.period_us = centi(argv[5]) * 10u;
    } else if (!strcmp(cmd, "sensorStart")) {
        st = safety_start();
    } else if (!strcmp(cmd, "sensorStop")) {
        st = safety_stop();
    } else {
        done = false;
        uprintf(&sci_cli, "'%s' ignored\n", cmd);
    }
    if (done)
        uprintf(&sci_cli, st ? "Error %d\n" : "Done\n", st);
}

static void cli_poll(void)
{
    static char line[160];
    static unsigned n;
    int c;
    while ((c = sci_getc(&sci_cli)) >= 0) {
        if (c == '\r' || c == '\n') {
            if (n) {
                line[n] = 0;
                uprintf(&sci_cli, "\n");
                cli_exec(line);
                n = 0;
                uprintf(&sci_cli, PROMPT);
            }
        } else if (n < sizeof line - 1) {
            line[n++] = (char)c;
            sci_putc(&sci_cli, (char)c); /* echo, as the TI demo does */
        }
    }
}

/* ------------------------------------------------------------------ main */
static void handle_event(uint32_t *ev)
{
    switch (ev[1]) {
    case BSS_EV_READY:
        radar.bss_ready = true;
        break;
    case BSS_EV_FRAME_DONE:
        radar.frames_done++;
        radar.last_frame = ev[3];
        if (radar.frames_done % 10u == 1u) {
            /* every 10th frame: rail current between frames */
            pmic_load_ma(2, &telemetry_buck2_ma);
            pmic_load_ma(3, &telemetry_buck3_ma);
        }
        send_frame_packet(ev);
        safety_on_frame(); /* temperature every frame, PMIC status every 10th; may throttle or stop */
        break;
    case BSS_EV_ERROR:
        radar.errors++;
        radar.last_error = ev[3];
        radar.running = false;
        safety_on_radar_error(ev[3]);
        uprintf(&sci_cli, "\nradar error %u\n", ev[3]);
        break;
    case BSS_EV_STOPPED:
        radar.running = false;
        break;
    }
}

int main(void)
{
    pinmux_apply();
    vim_init();
    rti_init();
    sci_init(&sci_cli, SCIA_BASE, CLI_BAUD, true);
    sci_init(&sci_data, SCIB_BASE, DATA_BAUD, false);
    vim_attach(VIM_CH_SCIA_LVL0, sci_isr_cli);
    radar_link_init();
    radar_defaults();
    cfg_defaults();
    vim_attach(VIM_CH_FRAME_START, frame_start_isr);
    i2c_init(I2C_SCL_HZ);
    irq_enable();

    uprintf(&sci_cli, "\nIWR-AOP-MODULE fw %s\n", FW_VERSION);
    int st = pmic_probe();
    if (!st)
        st = pmic_poll();
    if (st) {
        uprintf(&sci_cli, "PMIC: error %d\n", st);
    } else {
        /* RESET_REG (INT_TOP2 bit 0) is set when the PMIC has (re)loaded its registers from
         * OTP. Clear means the PMIC stayed powered across this radar reset and kept any value
         * firmware wrote before (a Buck2 trim, for example). */
        uprintf(&sci_cli, "PMIC: LP87524J OTP 0x%02x, Buck2 %u mV, %s\n", pmic.otp_rev, pmic_vset_to_mv(pmic.vout[2]),
                (pmic.int_top2 & LP_INT_TOP2_RESET_REG) ? "registers fresh from OTP"
                                                         : "registers kept from before this reset");
        if (pmic_vset_to_mv(pmic.vout[2]) != BUCK2_MV_MIN && !radar.running) {
            pmic_buck2_set_mv(BUCK2_MV_MIN);
            uprintf(&sci_cli, "PMIC: Buck2 back to %u mV\n", BUCK2_MV_MIN);
        }
    }

    uint32_t ev[BSS_MSG_WORDS];
    uint32_t t0 = time_ms();
    while (!radar.bss_ready && time_ms() - t0 < 200u) {
        if (radar_poll_event(ev))
            handle_event(ev);
    }
    uprintf(&sci_cli, "radar subsystem %s after %u ms\n", radar.bss_ready ? "ready" : "NOT READY", time_ms() - t0);
    uprintf(&sci_cli, PROMPT);

    for (;;) {
        if (frame_start_pending) {
            frame_start_pending = 0;
            /* every 10th frame, measure the rail currents while the chirps run (50 us each) */
            if (radar.running && frame_starts % 10u == 1u) {
                pmic_load_ma(2, &telemetry_buck2_frame_ma);
                pmic_load_ma(3, &telemetry_buck3_frame_ma);
            }
        }
        cli_poll();
        safety_poll();
        while (radar_poll_event(ev))
            handle_event(ev);
        uint32_t key = irq_disable();
        if (sci_cli.rx_head == sci_cli.rx_tail && evq_empty() && !frame_start_pending)
            cpu_wfi(); /* an interrupt wakes the core even while masked */
        irq_restore(key);
    }
}
