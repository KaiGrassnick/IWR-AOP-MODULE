/*
 * Safety layer: thermal governor and host configuration with hard limits.
 *
 * Everything the host may change goes through the table in safety.c (cfg_set). Each entry has
 * bounds compiled into the firmware (SAFE_* in board.h); a host can tighten a limit (throttle
 * earlier, stop earlier) but never loosen it past those bounds. The governor runs on its own:
 * no host command turns it off.
 */
#ifndef SAFETY_H
#define SAFETY_H

#include <stdbool.h>
#include <stdint.h>

enum safe_state {
    SAFE_IDLE = 0,     /* host has not asked for the radar to run */
    SAFE_RUN = 1,      /* running, possibly throttled (see level) */
    SAFE_HOT = 2,      /* stopped: too hot; restarts at the slowest level once cool */
    SAFE_FAULT = 3,    /* stopped: temperature unknown (reads failing) or radar error; needs sensorStart */
};

typedef struct {
    enum safe_state state;
    uint32_t level;          /* 0: host frame period, 1: x2, 2: x4 */
    int32_t temp_mc;         /* last radar temperature */
    uint32_t t_change_ms;    /* last level or state change */
    uint32_t temp_fails;     /* consecutive failed reads */
    uint32_t throttles, stops, restarts;
    bool pmic_hot;           /* LP87524 TDIE_WARN seen */
    uint32_t pmic_flags;     /* INT_TOP1 | INT_BUCK_2_3 << 8, accumulated for the telemetry */
} safety_t;

extern safety_t safety;

/* configuration ------------------------------------------------------------------------- */
typedef struct {
    const char *key, *unit, *help;
    int32_t *val;
    int32_t min, max, def;
} cfg_item_t;

extern const cfg_item_t cfg_items[];
extern const unsigned cfg_count;

int cfg_set(const char *key, int32_t v); /* 0, or SAFE_ERR_* */
const cfg_item_t *cfg_find(const char *key);
void cfg_defaults(void);

/* thermal settings, in degC and ms; read by the governor */
extern int32_t cfg_throttle_c, cfg_release_c, cfg_stop_c, cfg_resume_c, cfg_hold_ms, cfg_trim_mv;

/* governor ------------------------------------------------------------------------------- */
int safety_start(void);   /* host wants the radar running */
int safety_stop(void);    /* host wants it stopped */
void safety_on_frame(void);
void safety_poll(void);   /* call from the main loop: checks while stopped hot */
void safety_on_radar_error(uint32_t code);
const char *safety_state_name(void);

#define SAFE_ERR_KEY (-50)
#define SAFE_ERR_RANGE (-51)
#define SAFE_ERR_ORDER (-52)    /* release must be under throttle, resume under stop */
#define SAFE_ERR_TX (-53)       /* more TX per chirp than SAFE_MAX_TX_PER_CHIRP */
#define SAFE_ERR_DUTY (-54)     /* chirps take more than SAFE_MAX_DUTY_PCT of the frame period */
#define SAFE_ERR_PERIOD (-55)

#endif
