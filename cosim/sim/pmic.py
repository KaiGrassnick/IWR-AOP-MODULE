"""LP87524J-Q1 as the firmware sees it over I2C: the register map of SNVSAW2B 7.6 with the
J-variant OTP defaults of SNVU663A Table 3-8..3-13. Which registers change the rails is
handled by power.PowerTree; this class is the register file and the I2C target."""

SLEW_V_PER_S = 3.8e3            # 3.8 mV/us, SNVU663A Table 3-8

# OTP defaults (J). CTRL1: EN_BUCK, EN_PIN_CTRL, EN1, EN_RDIS, FPWM on Buck2/3.
OTP = {
    0x01: 0x72,                                     # OTP_REV, Table 10
    0x02: 0xC4, 0x04: 0xC4, 0x06: 0xC6, 0x08: 0xC6,  # BUCKx_CTRL1
    0x0A: 0xFC, 0x0C: 0x75, 0x0E: 0x4D, 0x10: 0xB1,  # VOUT 3.30 / 1.20 / 1.00 / 1.80 V
    0x0B: 0x00, 0x0D: 0x00, 0x0F: 0x00, 0x11: 0x00,  # FLOOR_VOUT
    0x12: 0x05, 0x13: 0x05, 0x14: 0x02, 0x15: 0x10,  # DELAY: start 5/5/2/0 ms, Buck3 stop 1 ms
    0x16: 0x05, 0x17: 0x03,                          # GPIO2/3 delay
    0x18: 0x00,                                      # RESET
    0x19: 0x4A,                                      # CONFIG: CLKIN_PD, TDIE_WARN_LEVEL 137 C, EN1_PD
    0x1A: 0x00, 0x1B: 0x00, 0x1C: 0x00, 0x1D: 0x00,  # INT_TOP1/2, INT_BUCK
    0x21: 0x01, 0x22: 0x01, 0x23: 0x55, 0x24: 0x55,  # masks (Table 3-13)
    0x25: 0x00, 0x26: 0x00, 0x27: 0x00,
    0x28: 0x55, 0x29: 0x27, 0x2A: 0x00,
    0x2B: 0x41, 0x2C: 0x56, 0x2D: 0x66, 0x2F: 0x00,
}
READ_ONLY = {0x01, 0x1E, 0x1F, 0x20, 0x26, 0x27, 0x2A, 0x2E}
W1C = {0x1A, 0x1B, 0x1C, 0x1D}
NAMES = {0x0A: "BUCK0_VOUT", 0x0C: "BUCK1_VOUT", 0x0E: "BUCK2_VOUT", 0x10: "BUCK3_VOUT",
         0x02: "BUCK0_CTRL1", 0x04: "BUCK1_CTRL1", 0x06: "BUCK2_CTRL1", 0x08: "BUCK3_CTRL1"}


def vset_to_v(code):
    """SNVSAW2B Table 19."""
    if code < 0x0A:
        return 0.0
    if code <= 0x17:
        return 0.600 + 0.010 * (code - 0x0A)
    if code <= 0x9D:
        return 0.735 + 0.005 * (code - 0x18)
    return 1.420 + 0.020 * (code - 0x9E)


class LP87524:
    ADDR = 0x60

    def __init__(self, sim, power):
        self.sim, self.power = sim, power
        self.regs = dict(OTP)
        self.ptr = None
        self.first = True
        self.read_mode = False
        self.writes = []         # (t, reg, value) for the results

    # power events ---------------------------------------------------------
    def otp_reload(self):
        self.regs = dict(OTP)
        self.regs[0x1B] |= 0x01  # RESET_REG
        self.sim.info("PMIC", "registers loaded from OTP (RESET_REG set)")

    # status registers, computed from the board state ------------------------
    def _status(self, reg):
        p = self.power
        if reg == 0x1E:                      # TOP_STAT
            return 0x04 if p.pmic_tj() > (137 if self.regs[0x19] & 0x08 else 125) else 0
        if reg in (0x1F, 0x20):
            lo, hi = (0, 1) if reg == 0x1F else (2, 3)
            v = 0
            for b, sh in ((lo, 0), (hi, 4)):
                if p.buck_on(b):
                    v |= 0x08 << sh
                if p.buck_pg(b):
                    v |= 0x04 << sh
            return v
        return self.regs.get(reg, 0)

    def flag(self, reg, bits):
        self.regs[reg] = self.regs.get(reg, 0) | bits

    # I2C target -----------------------------------------------------------
    def i2c_start(self, read):
        self.read_mode = read
        if not read:
            self.first = True
        return self.power.pmic_powered()

    def i2c_write(self, b):
        if self.first:
            self.ptr, self.first = b, False
            return True
        reg = self.ptr
        self.ptr = (self.ptr + 1) & 0xFF      # auto-increment on writes (SNVSAW2B 7.5.1.5)
        self.writes.append((self.sim.t, reg, b))
        if reg in READ_ONLY:
            return True
        if reg in W1C:
            self.regs[reg] &= ~b
            return True
        if reg == 0x18 and b & 1:
            self.sim.info("PMIC", "SW_RESET written: registers back to OTP")
            self.otp_reload()
            self.power.pmic_registers_changed(None)
            return True
        old = self.regs.get(reg, 0)
        self.regs[reg] = b
        if reg == 0x25:
            self.power.start_load_measurement(b & 3)
        if reg in NAMES and old != b:
            self.sim.info("PMIC", f"{NAMES[reg]} 0x{old:02X} -> 0x{b:02X}"
                          + (f" ({vset_to_v(b)*1e3:.0f} mV)" if "VOUT" in NAMES[reg] else ""))
            self.power.pmic_registers_changed(reg)
        return True

    def i2c_read(self):
        if self.ptr is None:
            return 0xFF
        return self._status(self.ptr) & 0xFF

    def i2c_stop(self):
        pass


class I2CBus:
    """The radar's I2C bus on this board: the LP87524 only (R605/R606 0 ohm, pull-ups to 3V3_R)."""

    def __init__(self, sim, targets):
        self.sim, self.targets = sim, targets
        self.cur = None

    def start(self, addr, read):
        self.cur = self.targets.get(addr)
        return bool(self.cur and self.cur.i2c_start(read))

    def write(self, b):
        return bool(self.cur and self.cur.i2c_write(b))

    def read(self):
        return self.cur.i2c_read() if self.cur else 0xFF

    def stop(self):
        if self.cur:
            self.cur.i2c_stop()
        self.cur = None
