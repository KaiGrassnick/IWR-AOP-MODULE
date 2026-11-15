#ifndef RADAR_H
#define RADAR_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t rx_mask, tx_mask;
    /* profile, TI CLI units: us, MHz/us, ksps */
    uint32_t idle_10ns, adc_start_10ns, ramp_end_10ns, tx_start_10ns, samples, ksps, slope_khz_us;
    uint32_t chirp_start, chirp_end, chirp_tx_mask[8];
    uint32_t frame_chirp_start, frame_chirp_end, loops, frames, period_us;
    uint32_t buck2_mv_running; /* 0 = leave Buck2 alone */
} radar_cfg_t;

typedef struct {
    bool bss_ready, rf_init, running;
    uint32_t frames_done, last_frame, last_error, errors;
    int32_t temp_mc;
    uint32_t period_scale; /* thermal throttle: frame period multiplier (1, 2, 4) */
} radar_state_t;

extern radar_cfg_t radar_cfg;
extern radar_state_t radar;

void radar_link_init(void);
int radar_send(uint32_t id, const uint32_t *payload, unsigned n, uint32_t *resp, unsigned resp_n);
int radar_poll_event(uint32_t *msg); /* copies one event (64 words), returns 1 if one was waiting */
bool evq_empty(void);
int radar_start(void);
int radar_stop(void);
int radar_get_temp(int32_t *mc);
void radar_defaults(void);

#endif
