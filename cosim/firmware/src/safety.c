/*
 * Thermal governor and host configuration. See safety.h.
 *
 * Governor, evaluated after each frame once thermal.read_ms has passed (radar temperature over the
 * mailbox, LP87524 status over I2C):
 *   T >= stop                      -> stop the radar (TX off), state HOT
 *   T >= throttle, level < 2       -> level + 1: frame period x2, x4 (fewer chirps per second)
 *   T <= release,  level > 0       -> level - 1
 *   level changes at most once per hold time, so the heat sink can respond (tau ~150 s).
 * While HOT, every read_ms: T <= resume and no PMIC thermal warning -> restart at level 2.
 * Three failed temperature reads in a row stop the radar (state FAULT): no reading, no RF.
 * The radar's own limit is 105 C junction (SWRS237C 7.3); the defaults keep 5 K of margin.
 */
#include <string.h>

#include "board.h"
#include "bss_msg.h"
#include "pmic.h"
#include "radar.h"
#include "safety.h"
#include "soc.h"

safety_t safety;

int32_t cfg_throttle_c, cfg_release_c, cfg_stop_c, cfg_resume_c, cfg_hold_ms, cfg_read_ms, cfg_trim_mv, cfg_tx3;

const cfg_item_t cfg_items[] = {
    {"thermal.throttle_c", "C", "radar temperature that halves the frame rate", &cfg_throttle_c,
     SAFE_THROTTLE_C_MIN, SAFE_THROTTLE_C_MAX, SAFE_THROTTLE_C_MAX},
    {"thermal.release_c", "C", "temperature that undoes one throttle step", &cfg_release_c,
     SAFE_THROTTLE_C_MIN - 10, SAFE_THROTTLE_C_MAX - SAFE_HYST_C_MIN, SAFE_THROTTLE_C_MAX - 5},
    {"thermal.stop_c", "C", "temperature that stops the radar", &cfg_stop_c,
     SAFE_STOP_C_MIN, SAFE_STOP_C_MAX, SAFE_STOP_C_MAX},
    {"thermal.resume_c", "C", "temperature under which a stopped radar restarts", &cfg_resume_c,
     SAFE_STOP_C_MIN - 20, SAFE_STOP_C_MAX - SAFE_HYST_C_MIN, SAFE_STOP_C_MAX - 10},
    {"thermal.hold_ms", "ms", "minimum time between throttle steps", &cfg_hold_ms,
     SAFE_HOLD_MS_MIN, SAFE_HOLD_MS_MAX, SAFE_HOLD_MS_DEFAULT},
    {"thermal.read_ms", "ms", "time between temperature (and PMIC status) reads", &cfg_read_ms,
     SAFE_READ_MS_MIN, SAFE_READ_MS_MAX, SAFE_READ_MS_DEFAULT},
    {"radar.tx3_enable", "", "allow three TX in one chirp (1V0 not yet verified for it)", &cfg_tx3, 0, 1, 0},
    {"pmic.trim_mv", "mV", "Buck2 (1V0) above 1000 mV while the radar runs", &cfg_trim_mv,
     0, (int32_t)(BUCK2_MV_MAX - BUCK2_MV_MIN), 0},
};
const unsigned cfg_count = sizeof cfg_items / sizeof cfg_items[0];

void cfg_defaults(void)
{
    for (unsigned i = 0; i < cfg_count; i++)
        *cfg_items[i].val = cfg_items[i].def;
}

const cfg_item_t *cfg_find(const char *key)
{
    for (unsigned i = 0; i < cfg_count; i++)
        if (!strcmp(cfg_items[i].key, key))
            return &cfg_items[i];
    return 0;
}

int cfg_set(const char *key, int32_t v)
{
    const cfg_item_t *c = cfg_find(key);
    if (!c)
        return SAFE_ERR_KEY;
    if (v < c->min || v > c->max)
        return SAFE_ERR_RANGE;
    int32_t old = *c->val;
    *c->val = v;
    /* the pairs must keep their hysteresis whichever one the host changes */
    if (cfg_release_c > cfg_throttle_c - SAFE_HYST_C_MIN || cfg_resume_c > cfg_stop_c - SAFE_HYST_C_MIN ||
        cfg_throttle_c >= cfg_stop_c) {
        *c->val = old;
        return SAFE_ERR_ORDER;
    }
    return 0;
}

/* governor -------------------------------------------------------------------------------- */
const char *safety_state_name(void)
{
    static const char *const n[] = {"idle", "run", "stopped (hot)", "stopped (fault)"};
    return n[safety.state];
}

static void note(const char *what)
{
    uprintf(&sci_cli, "\nthermal: %s, radar %d.%d C, level %u\n", what, (int)(safety.temp_mc / 1000),
            (int)((safety.temp_mc < 0 ? -safety.temp_mc : safety.temp_mc) % 1000 / 100), safety.level);
}

static int run_at(uint32_t level)
{
    radar_stop();
    safety.level = level;
    radar.period_scale = 1u << level;
    radar_cfg.buck2_mv_running = cfg_trim_mv ? BUCK2_MV_MIN + (uint32_t)cfg_trim_mv : 0;
    safety.t_change_ms = time_ms();
    int st = radar_start();
    safety.state = st ? SAFE_FAULT : SAFE_RUN;
    return st;
}

static void go_hot(const char *why)
{
    radar_stop();
    safety.state = SAFE_HOT;
    safety.stops++;
    safety.t_change_ms = time_ms();
    note(why);
}

static bool read_temp(void)
{
    int32_t mc;
    safety.t_read_ms = time_ms();
    if (radar_get_temp(&mc)) {
        if (++safety.temp_fails >= SAFE_TEMP_FAILS_MAX) {
            radar_stop();
            safety.state = SAFE_FAULT;
            note("temperature unreadable, radar stopped");
        }
        return false;
    }
    safety.temp_fails = 0;
    safety.temp_mc = mc;
    radar.temp_mc = mc;
    return true;
}

static bool pmic_hot(void)
{
    /* LP87524 die temperature warning (TOP_STAT live, INT_TOP1 latched) */
    if (pmic_poll())
        return safety.pmic_hot;
    safety.pmic_flags |= (uint32_t)pmic.int_top1 | ((uint32_t)pmic.int_b23 << 8);
    safety.pmic_hot = (pmic.top_stat & LP_TOP_STAT_TDIE_WARN) || (pmic.int_top1 & LP_INT_TOP1_TDIE_WARN);
    return safety.pmic_hot;
}

int safety_start(void)
{
    if (safety.state == SAFE_RUN && radar.running)
        return 0;
    safety.temp_fails = 0;
    if (!read_temp())
        return BSS_ERR_STATE;
    if (safety.temp_mc >= cfg_resume_c * 1000 || pmic_hot()) {
        /* too warm to start: wait, and start by itself once cool */
        safety.state = SAFE_HOT;
        safety.t_change_ms = time_ms();
        note("too warm to start, waiting");
        return 0;
    }
    return run_at(safety.temp_mc >= cfg_throttle_c * 1000 ? 1u : 0u);
}

int safety_stop(void)
{
    safety.state = SAFE_IDLE;
    return radar_stop();
}

void safety_on_radar_error(uint32_t code)
{
    (void)code;
    if (safety.state == SAFE_RUN)
        safety.state = SAFE_FAULT;
}

void safety_on_frame(void)
{
    uint32_t now = time_ms();
    if (safety.state != SAFE_RUN || now - safety.t_read_ms < (uint32_t)cfg_read_ms)
        return;
    if (!read_temp())
        return;
    int32_t t = safety.temp_mc;
    bool may_step = now - safety.t_change_ms >= (uint32_t)cfg_hold_ms;
    if (t >= cfg_stop_c * 1000) {
        go_hot("stop temperature reached, radar stopped");
    } else if (pmic_hot()) {
        go_hot("PMIC thermal warning, radar stopped");
    } else if (t >= cfg_throttle_c * 1000 && safety.level < SAFE_MAX_LEVEL && may_step) {
        safety.throttles++;
        run_at(safety.level + 1u);
        note("throttling, frame rate halved");
    } else if (t <= cfg_release_c * 1000 && safety.level > 0 && may_step) {
        run_at(safety.level - 1u);
        note("cooler, frame rate doubled");
    }
}

void safety_poll(void)
{
    static uint32_t last;
    uint32_t now = time_ms();
    if (safety.state != SAFE_HOT || now - last < (uint32_t)cfg_read_ms)
        return;
    last = now;
    if (!read_temp() || safety.state != SAFE_HOT)
        return;
    if (safety.temp_mc <= cfg_resume_c * 1000 && !pmic_hot() && now - safety.t_change_ms >= (uint32_t)cfg_hold_ms) {
        safety.restarts++;
        run_at(SAFE_MAX_LEVEL);
        note("cool again, restarted at the lowest frame rate");
    }
}
