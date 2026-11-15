/* Radar control: the MSS side of the MSS<->BSS mailbox (SWRU522E chapter 18) and the start-up
 * order the board needs (spec 8.1: LDO bypass before any RF power). See bss_msg.h for what is
 * real and what stands in for TI's mmWaveLink. */
#include <string.h>

#include "board.h"
#include "bss_msg.h"
#include "pmic.h"
#include "radar.h"
#include "safety.h"
#include "soc.h"

radar_cfg_t radar_cfg;
radar_state_t radar;

#define MB_MSS_REG(r) REG32(MSS_MBOX4BSS_REG + (r))
#define MB_BSS_REG(r) REG32(BSS_MBOX4MSS_REG + (r))

#define EVQ 8
static volatile uint32_t evq[EVQ][BSS_MSG_WORDS];
static volatile unsigned evq_head, evq_tail;
static volatile uint32_t resp_buf[BSS_MSG_WORDS];
static volatile bool resp_valid, read_done = true;
static uint32_t seq;

static void mbox_isr(void)
{
    /* new message from the BSS: acknowledge, copy, tell the BSS it has been read */
    MB_MSS_REG(MBOX_INT_ACK) = 1u;
    const volatile uint32_t *m = (const volatile uint32_t *)MSS_MBOX4BSS_MEM;
    if (m[0] == BSS_MSG_MAGIC) {
        if (m[1] & BSS_RESPONSE) {
            for (unsigned i = 0; i < BSS_MSG_WORDS; i++)
                resp_buf[i] = m[i];
            resp_valid = true;
        } else {
            unsigned next = (evq_head + 1u) % EVQ;
            if (next != evq_tail) {
                for (unsigned i = 0; i < BSS_MSG_WORDS; i++)
                    evq[evq_head][i] = m[i];
                evq_head = next;
            }
        }
    }
    MB_BSS_REG(MBOX_INT_TRIG) = 2u; /* read complete, bit 1 */
}

static void mbox_read_isr(void)
{
    MB_MSS_REG(MBOX_INT_ACK) = 2u;
    read_done = true;
}

void radar_link_init(void)
{
    memset(&radar, 0, sizeof radar);
    evq_head = evq_tail = 0;
    read_done = true;
    vim_attach(VIM_CH_BSS_MBOX, mbox_isr);
    vim_attach(VIM_CH_BSS_MBOX_RD, mbox_read_isr);
}

static bool wait_until(volatile bool *flag, uint32_t timeout_us)
{
    uint32_t t0 = time_us();
    while (!*flag) {
        if (time_us() - t0 > timeout_us)
            return false;
    }
    return true;
}

int radar_send(uint32_t id, const uint32_t *payload, unsigned n, uint32_t *resp, unsigned resp_n)
{
    if (!wait_until(&read_done, 10000))
        return -1;
    volatile uint32_t *m = (volatile uint32_t *)BSS_MBOX4MSS_MEM;
    m[0] = BSS_MSG_MAGIC;
    m[1] = id;
    m[2] = ++seq;
    for (unsigned i = 0; i < n && i + 3 < BSS_MSG_WORDS; i++)
        m[3 + i] = payload[i];
    resp_valid = false;
    read_done = false;
    MB_BSS_REG(MBOX_INT_TRIG) = 1u;
    if (!wait_until(&resp_valid, 20000))
        return -2;
    if (resp_buf[1] != (id | BSS_RESPONSE) || resp_buf[2] != seq)
        return -3;
    for (unsigned i = 0; i < resp_n; i++)
        resp[i] = resp_buf[4 + i];
    if (resp_buf[3] != BSS_OK) {
        radar.last_error = resp_buf[3];
        radar.errors++;
    }
    return (int)resp_buf[3];
}

bool evq_empty(void) { return evq_head == evq_tail; }

int radar_poll_event(uint32_t *msg)
{
    uint32_t key = irq_disable();
    int got = 0;
    if (evq_tail != evq_head) {
        for (unsigned i = 0; i < BSS_MSG_WORDS; i++)
            msg[i] = evq[evq_tail][i];
        evq_tail = (evq_tail + 1u) % EVQ;
        got = 1;
    }
    irq_restore(key);
    return got;
}

void radar_defaults(void)
{
    /* TI's IWR6843AOP 3-D people-counting style profile: TDM-MIMO, one TX per chirp */
    memset(&radar_cfg, 0, sizeof radar_cfg);
    radar_cfg.rx_mask = 0xF;
    radar_cfg.tx_mask = 0x7;
    radar_cfg.idle_10ns = 4700;      /* 47 us */
    radar_cfg.adc_start_10ns = 700;  /* 7 us */
    radar_cfg.ramp_end_10ns = 5714;  /* 57.14 us */
    radar_cfg.tx_start_10ns = 100;   /* 1 us */
    radar_cfg.samples = 256;
    radar_cfg.ksps = 5209;
    radar_cfg.slope_khz_us = 70000;
    radar_cfg.chirp_start = 0;
    radar_cfg.chirp_end = 2;
    radar_cfg.chirp_tx_mask[0] = 1;
    radar_cfg.chirp_tx_mask[1] = 4;
    radar_cfg.chirp_tx_mask[2] = 2;
    radar_cfg.frame_chirp_start = 0;
    radar_cfg.frame_chirp_end = 2;
    radar_cfg.loops = 16;
    radar_cfg.frames = 0;
    radar_cfg.period_us = 100000;
    radar_cfg.buck2_mv_running = 0;
}

static int wait_event(uint32_t id, uint32_t timeout_us, uint32_t *msg)
{
    uint32_t t0 = time_us();
    while (time_us() - t0 < timeout_us) {
        if (radar_poll_event(msg)) {
            if (msg[1] == id)
                return 0;
            if (msg[1] == BSS_EV_ERROR)
                return (int)msg[3];
        }
    }
    return -2;
}

int radar_start(void)
{
    static uint32_t ev[BSS_MSG_WORDS];
    int st;
    if (!radar.bss_ready)
        return BSS_ERR_STATE;
    if (radar.running)
        return BSS_OK;
    /* hard limits (board.h): TX per chirp, frame duty, period */
    uint32_t chirp_us10 = radar_cfg.idle_10ns + radar_cfg.ramp_end_10ns, nchirps = 0;
    for (uint32_t i = radar_cfg.frame_chirp_start; i <= radar_cfg.frame_chirp_end && i < 8; i++) {
        uint32_t m = radar_cfg.chirp_tx_mask[i] & radar_cfg.tx_mask, n = 0;
        for (; m; m &= m - 1u)
            n++;
        if (n > (cfg_tx3 ? 3u : SAFE_MAX_TX_PER_CHIRP))
            return SAFE_ERR_TX;
        nchirps++;
    }
    if (radar_cfg.period_us < SAFE_MIN_PERIOD_US)
        return SAFE_ERR_PERIOD;
    uint64_t active_10ns = (uint64_t)chirp_us10 * nchirps * radar_cfg.loops;
    if (active_10ns * 100u > (uint64_t)radar_cfg.period_us * 100u * SAFE_MAX_DUTY_PCT)
        return SAFE_ERR_DUTY;
    if (!radar.period_scale)
        radar.period_scale = 1;
    uint32_t ch[] = {radar_cfg.rx_mask, radar_cfg.tx_mask};
    if ((st = radar_send(BSS_CHANNEL_CFG, ch, 2, 0, 0)))
        return st;
    /* spec 8.1: external 1.0 V on VOUT_PA. ldoBypassEnable = 3 (RF LDO bypassed, PA LDO
     * disabled), supplyMonIrDrop = 1 (remote sense keeps the drop to the balls near 1 %),
     * ioSupplyIndicator = 0 (3.3 V I/O). Must precede rlRfInit. */
    uint32_t ldo[] = {3, 1, 0};
    if ((st = radar_send(BSS_LDO_BYPASS_CFG, ldo, 3, 0, 0)))
        return st;
    if (!radar.rf_init) {
        if ((st = radar_send(BSS_RF_INIT, 0, 0, 0, 0)))
            return st;
        if ((st = wait_event(BSS_EV_RF_INIT_DONE, 500000, ev)))
            return st;
        radar.rf_init = true;
    }
    uint32_t prof[] = {radar_cfg.idle_10ns, radar_cfg.adc_start_10ns, radar_cfg.ramp_end_10ns, radar_cfg.tx_start_10ns,
                       radar_cfg.samples, radar_cfg.ksps, radar_cfg.slope_khz_us};
    if ((st = radar_send(BSS_PROFILE_CFG, prof, 7, 0, 0)))
        return st;
    for (uint32_t i = radar_cfg.chirp_start; i <= radar_cfg.chirp_end && i < 8; i++) {
        uint32_t c[] = {i, i, radar_cfg.chirp_tx_mask[i]};
        if ((st = radar_send(BSS_CHIRP_CFG, c, 3, 0, 0)))
            return st;
    }
    uint32_t fr[] = {radar_cfg.frame_chirp_start, radar_cfg.frame_chirp_end, radar_cfg.loops, radar_cfg.frames,
                     radar_cfg.period_us * radar.period_scale};
    if ((st = radar_send(BSS_FRAME_CFG, fr, 5, 0, 0)))
        return st;
    if (radar_cfg.buck2_mv_running) {
        if ((st = pmic_buck2_set_mv(radar_cfg.buck2_mv_running)))
            return 100 - st;
        delay_us(200); /* 25 mV at 3.8 mV/us takes 7 us; leave the loop time to settle */
    }
    if ((st = radar_send(BSS_SENSOR_START, 0, 0, 0, 0)))
        return st;
    radar.running = true;
    return BSS_OK;
}

int radar_stop(void)
{
    static uint32_t ev[BSS_MSG_WORDS];
    if (!radar.running)
        return BSS_OK;
    int st = radar_send(BSS_SENSOR_STOP, 0, 0, 0, 0);
    if (!st)
        st = wait_event(BSS_EV_STOPPED, 300000, ev);
    radar.running = false;
    if (radar_cfg.buck2_mv_running)
        pmic_buck2_set_mv(BUCK2_MV_MIN);
    return st;
}

int radar_get_temp(int32_t *mc)
{
    uint32_t r[1];
    int st = radar_send(BSS_GET_TEMP, 0, 0, r, 1);
    if (!st)
        *mc = (int32_t)r[0];
    return st;
}
