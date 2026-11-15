#ifndef PMIC_H
#define PMIC_H

#include <stdbool.h>
#include <stdint.h>

/* LP87524J-Q1 register addresses, SNVSAW2B Table 9 */
enum {
    LP_OTP_REV = 0x01,
    LP_BUCK0_CTRL1 = 0x02,
    LP_BUCK1_CTRL1 = 0x04,
    LP_BUCK2_CTRL1 = 0x06,
    LP_BUCK3_CTRL1 = 0x08,
    LP_BUCK0_VOUT = 0x0A,
    LP_BUCK1_VOUT = 0x0C,
    LP_BUCK2_VOUT = 0x0E,
    LP_BUCK3_VOUT = 0x10,
    LP_BUCK0_DELAY = 0x12,
    LP_GPIO2_DELAY = 0x16,
    LP_RESET = 0x18,
    LP_CONFIG = 0x19,
    LP_INT_TOP1 = 0x1A,
    LP_INT_TOP2 = 0x1B,
    LP_INT_BUCK_0_1 = 0x1C,
    LP_INT_BUCK_2_3 = 0x1D,
    LP_TOP_STAT = 0x1E,
    LP_BUCK_0_1_STAT = 0x1F,
    LP_BUCK_2_3_STAT = 0x20,
    LP_SEL_I_LOAD = 0x25,
    LP_I_LOAD_2 = 0x26,
    LP_I_LOAD_1 = 0x27,
    LP_PGOOD_FLT = 0x2A,
};

#define LP_INT_TOP1_I_LOAD_READY 0x01u
#define LP_INT_TOP1_INT_OVP 0x02u
#define LP_INT_TOP1_TDIE_WARN 0x04u
#define LP_INT_TOP1_TDIE_SD 0x08u
#define LP_INT_TOP2_RESET_REG 0x01u
#define LP_TOP_STAT_TDIE_WARN 0x04u

typedef struct {
    bool present;
    uint8_t otp_rev;
    uint8_t vout[4];
    uint8_t ctrl1[4];
    uint8_t top_stat, stat01, stat23;
    uint8_t int_top1, int_top2, int_b01, int_b23;  /* flags found (and cleared) at the last poll */
} pmic_state_t;

extern pmic_state_t pmic;

int pmic_probe(void);
int pmic_poll(void);                 /* read status, clear and remember latched flags */
int pmic_buck2_set_mv(uint32_t mv);  /* refuses values outside BUCK2_MV_MIN..MAX */
uint32_t pmic_vset_to_mv(uint8_t code);
int pmic_load_ma(unsigned buck, uint32_t *ma);
int pmic_raw_write(uint8_t reg, uint8_t val); /* debug only, no guard */
int pmic_raw_read(uint8_t reg, uint8_t *val);

#endif
