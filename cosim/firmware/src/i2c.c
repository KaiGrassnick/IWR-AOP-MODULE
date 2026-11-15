/* I2C master, polled. SWRU522E chapter 25. */
#include "soc.h"

#define I2C(r) REG32(I2C_BASE + (r))
#define I2C_TIMEOUT_US 2000u

static uint32_t scl_hz;

/* SCL period = (ICCLKL + d) + (ICCLKH + d) module clocks, with d = 7 / 6 / 5 for
 * ICPSC = 0 / 1 / >1 (TI's convention for this I2C module; SWRU522E gives the
 * registers but not d). Module clock = VCLK / (ICPSC + 1), kept near 10 MHz. */
void i2c_init(uint32_t hz)
{
    const uint32_t psc = 19u, d = 5u;
    uint32_t fmod = VCLK_HZ / (psc + 1u);
    uint32_t total = fmod / hz;                 /* module clocks per SCL period */
    uint32_t low = (total * 13u + 12u) / 25u;   /* about 52 % low, as I2C fast mode wants */
    uint32_t clkl = low - d, clkh = total - low - d;
    I2C(I2C_MDR) = 0;                           /* IRS = 0: hold in reset while configuring */
    I2C(I2C_PSC) = psc;
    I2C(I2C_CLKL) = clkl;
    I2C(I2C_CLKH) = clkh;
    I2C(I2C_OAR) = 0x01u;
    I2C(I2C_IMR) = 0;                           /* polled */
    I2C(I2C_MDR) = I2C_MDR_IRS | I2C_MDR_FREE;
    scl_hz = fmod / ((clkl + d) + (clkh + d));
}

uint32_t i2c_scl_hz(void) { return scl_hz; }

static int wait_flag(uint32_t mask, uint32_t *str_out)
{
    uint32_t t0 = time_us();
    for (;;) {
        uint32_t s = I2C(I2C_STR);
        if (s & (mask | I2C_STR_NACK | I2C_STR_AL)) {
            if (str_out)
                *str_out = s;
            if (s & I2C_STR_AL)
                return I2C_ERR_AL;
            if ((s & I2C_STR_NACK) && !(mask & I2C_STR_NACK))
                return I2C_ERR_NACK;
            return I2C_OK;
        }
        if (time_us() - t0 > I2C_TIMEOUT_US)
            return I2C_ERR_TIMEOUT;
    }
}

static int finish(int err)
{
    if (err != I2C_OK) {
        /* end the transfer: stop condition, clear NACK/AL */
        I2C(I2C_MDR) |= I2C_MDR_STP;
        I2C(I2C_STR) = I2C_STR_NACK | I2C_STR_AL;
    }
    uint32_t t0 = time_us();
    while ((I2C(I2C_STR) & I2C_STR_BB) && time_us() - t0 < I2C_TIMEOUT_US)
        ;
    I2C(I2C_STR) = I2C_STR_SCD | I2C_STR_ARDY;
    if (err == I2C_OK && (I2C(I2C_STR) & I2C_STR_BB))
        return I2C_ERR_TIMEOUT;
    return err;
}

static int start(uint8_t addr, uint32_t count, uint32_t mdr)
{
    if (I2C(I2C_STR) & I2C_STR_BB)
        return I2C_ERR_BUSY;
    I2C(I2C_STR) = I2C_STR_NACK | I2C_STR_AL | I2C_STR_SCD | I2C_STR_ARDY;
    I2C(I2C_SAR) = addr;
    I2C(I2C_CNT) = count;
    I2C(I2C_MDR) = I2C_MDR_IRS | I2C_MDR_FREE | I2C_MDR_MST | I2C_MDR_STT | mdr;
    return I2C_OK;
}

int i2c_write_reg(uint8_t addr, uint8_t reg, uint8_t val)
{
    int err = start(addr, 2, I2C_MDR_TRX | I2C_MDR_STP);
    if (err)
        return err;
    if ((err = wait_flag(I2C_STR_XRDY, 0)) == I2C_OK) {
        I2C(I2C_DXR) = reg;
        if ((err = wait_flag(I2C_STR_XRDY, 0)) == I2C_OK) {
            I2C(I2C_DXR) = val;
            err = wait_flag(I2C_STR_SCD, 0);
        }
    }
    return finish(err);
}

int i2c_read_reg(uint8_t addr, uint8_t reg, uint8_t *val)
{
    /* write the register address, repeated start, read one byte (SNVSAW2B Figure 20) */
    int err = start(addr, 1, I2C_MDR_TRX);
    if (err)
        return err;
    if ((err = wait_flag(I2C_STR_XRDY, 0)) == I2C_OK) {
        I2C(I2C_DXR) = reg;
        if ((err = wait_flag(I2C_STR_ARDY, 0)) == I2C_OK) {
            I2C(I2C_CNT) = 1;
            I2C(I2C_MDR) = I2C_MDR_IRS | I2C_MDR_FREE | I2C_MDR_MST | I2C_MDR_STT | I2C_MDR_STP;
            if ((err = wait_flag(I2C_STR_RRDY, 0)) == I2C_OK) {
                *val = (uint8_t)I2C(I2C_DRR);
                err = wait_flag(I2C_STR_SCD, 0);
            }
        }
    }
    return finish(err);
}
