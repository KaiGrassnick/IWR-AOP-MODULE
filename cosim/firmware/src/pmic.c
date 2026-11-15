/* LP87524J-Q1 driver (SNVSAW2B 7.5-7.6, SNVU663A 3.1.2). The board starts from OTP;
 * nothing here is needed for the rails to come up (spec 3.2). */
#include "pmic.h"
#include "board.h"
#include "soc.h"

pmic_state_t pmic;

uint32_t pmic_vset_to_mv(uint8_t c)
{
    /* SNVSAW2B Table 19: 0x0A-0x17 0.6-0.73 V / 10 mV, 0x18-0x9D 0.735-1.4 V / 5 mV,
     * 0x9E-0xFF 1.42-3.36 V / 20 mV */
    if (c < 0x0A)
        return 0;
    if (c <= 0x17)
        return 600u + 10u * (c - 0x0Au);
    if (c <= 0x9D)
        return 735u + 5u * (c - 0x18u);
    return 1420u + 20u * (c - 0x9Eu);
}

static uint8_t mv_to_vset_5mv(uint32_t mv) { return (uint8_t)(0x18u + (mv - 735u) / 5u); }

int pmic_raw_read(uint8_t reg, uint8_t *val) { return i2c_read_reg(PMIC_I2C_ADDR, reg, val); }
int pmic_raw_write(uint8_t reg, uint8_t val) { return i2c_write_reg(PMIC_I2C_ADDR, reg, val); }

int pmic_probe(void)
{
    pmic.present = false;
    int err = pmic_raw_read(LP_OTP_REV, &pmic.otp_rev);
    if (err)
        return err;
    pmic.present = pmic.otp_rev == PMIC_OTP_REV_J;
    for (unsigned b = 0; b < 4; b++) {
        if ((err = pmic_raw_read((uint8_t)(LP_BUCK0_VOUT + 2 * b), &pmic.vout[b])) ||
            (err = pmic_raw_read((uint8_t)(LP_BUCK0_CTRL1 + 2 * b), &pmic.ctrl1[b])))
            return err;
    }
    return pmic.present ? 0 : -10;
}

int pmic_poll(void)
{
    int err;
    if ((err = pmic_raw_read(LP_TOP_STAT, &pmic.top_stat)) || (err = pmic_raw_read(LP_BUCK_0_1_STAT, &pmic.stat01)) ||
        (err = pmic_raw_read(LP_BUCK_2_3_STAT, &pmic.stat23)) || (err = pmic_raw_read(LP_INT_TOP1, &pmic.int_top1)) ||
        (err = pmic_raw_read(LP_INT_TOP2, &pmic.int_top2)) || (err = pmic_raw_read(LP_INT_BUCK_0_1, &pmic.int_b01)) ||
        (err = pmic_raw_read(LP_INT_BUCK_2_3, &pmic.int_b23)) || (err = pmic_raw_read(LP_BUCK2_VOUT, &pmic.vout[2])))
        return err;
    /* latched flags are cleared by writing 1 (SNVSAW2B 7.6.1.22-25) */
    if (pmic.int_top1 & 0x1F)
        err |= pmic_raw_write(LP_INT_TOP1, pmic.int_top1 & 0x1F);
    if (pmic.int_top2)
        err |= pmic_raw_write(LP_INT_TOP2, pmic.int_top2);
    if (pmic.int_b01)
        err |= pmic_raw_write(LP_INT_BUCK_0_1, pmic.int_b01);
    if (pmic.int_b23)
        err |= pmic_raw_write(LP_INT_BUCK_2_3, pmic.int_b23);
    return err;
}

int pmic_buck2_set_mv(uint32_t mv)
{
    if (mv < BUCK2_MV_MIN || mv > BUCK2_MV_MAX || (mv - 735u) % 5u)
        return -20;
    uint8_t code = mv_to_vset_5mv(mv), rb = 0;
    int err = pmic_raw_write(LP_BUCK2_VOUT, code);
    if (!err)
        err = pmic_raw_read(LP_BUCK2_VOUT, &rb);
    if (!err && rb != code)
        return -21;
    if (!err)
        pmic.vout[2] = code;
    return err;
}

int pmic_load_ma(unsigned buck, uint32_t *ma)
{
    uint8_t f = 0, hi = 0, lo = 0;
    int err = pmic_raw_write(LP_INT_TOP1, LP_INT_TOP1_I_LOAD_READY);
    if (!err)
        err = pmic_raw_write(LP_SEL_I_LOAD, (uint8_t)(buck & 3u)); /* starts a 50 us measurement */
    uint32_t t0 = time_us();
    while (!err && !(f & LP_INT_TOP1_I_LOAD_READY)) {
        if (time_us() - t0 > 500u)
            return I2C_ERR_TIMEOUT;
        err = pmic_raw_read(LP_INT_TOP1, &f);
    }
    if (!err)
        err = pmic_raw_read(LP_I_LOAD_2, &hi);
    if (!err)
        err = pmic_raw_read(LP_I_LOAD_1, &lo);
    if (!err) {
        *ma = 20u * ((((uint32_t)hi & 3u) << 8) | lo);
        err = pmic_raw_write(LP_INT_TOP1, LP_INT_TOP1_I_LOAD_READY);
    }
    return err;
}
