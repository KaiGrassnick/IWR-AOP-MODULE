"""IWR6843 MSS peripherals the firmware uses, modelled at register level from TI SWRU522E
(chapter and table noted per class) and SWRS237C (pad registers).

What is NOT modelled: DMA, EDMA, ESM, QSPI controller (the boot ROM model reads the flash
directly), MibSPI, CAN, the DSP subsystem and the hardware accelerator. Accesses to anything
unmodelled are counted in Sim.unmodelled and listed in the results.
"""
from .core import Reg32Device

VCLK = 200e6


def bit(v, n):
    return (v >> n) & 1


# ---------------------------------------------------------------------------- VIM
class VIM(Reg32Device):
    """Vectored Interrupt Manager, SWRU522E chapter 8. Control registers at 0xFFFFFE00, the
    vector table (VIM RAM) at 0xFFF82000 is plain RAM in the CPU's memory map."""

    RAM = 0xFFF82000

    def __init__(self, sim):
        super().__init__(sim, "VIM")
        self.ena = [0, 0, 0, 0]
        self.firqpr = [3, 0, 0, 0]
        self.lines = set()

    def set_line(self, ch, level):
        if level:
            self.lines.add(ch)
        else:
            self.lines.discard(ch)

    def pending(self):
        return sorted(ch for ch in self.lines if (self.ena[ch // 32] >> (ch % 32)) & 1 and ch > 1)

    def irq_pending(self):
        return bool(self.pending())

    def read(self, off, size):
        if off == 0x00:                                   # IRQINDEX
            p = self.pending()
            return p[0] + 1 if p else 0
        if 0x10 <= off < 0x20:
            return self.firqpr[(off - 0x10) // 4]
        if 0x20 <= off < 0x30:                            # INTREQ
            i = (off - 0x20) // 4
            return sum(1 << (c % 32) for c in self.lines if c // 32 == i)
        if 0x30 <= off < 0x50:                            # REQENASET/CLR read back the enables
            return self.ena[(off - 0x30) % 0x10 // 4]
        if off == 0x70:                                   # IRQVECREG
            p = self.pending()
            return self.sim.mem_read32(self.RAM + 4 * (p[0] + 1 if p else 0))
        return 0

    def write(self, off, size, v):
        if 0x10 <= off < 0x20:
            self.firqpr[(off - 0x10) // 4] = v
        elif 0x30 <= off < 0x40:
            self.ena[(off - 0x30) // 4] |= v
        elif 0x40 <= off < 0x50:
            self.ena[(off - 0x40) // 4] &= ~v


# ---------------------------------------------------------------------------- RTI
class RTI(Reg32Device):
    """Real-time interrupt module, counter 0 and compare 0 only. SWRU522E 16.3.
    RTICLK = VCLK (200 MHz) [assumption, mmWave SDK value]."""

    def __init__(self, sim, vim, ch_comp0=2, clk=VCLK):
        super().__init__(sim, "RTIA")
        self.vim, self.ch, self.clk = vim, ch_comp0, clk
        self.gctrl = self.compctrl = self.cpuc0 = self.comp0 = self.udcp0 = 0
        self.intena = self.intflag = 0
        self.frc_base, self.t_base = 0, 0.0
        self.ev = None

    def _rate(self):
        return self.clk / (self.cpuc0 + 1) if self.cpuc0 else self.clk / 2**32

    def frc(self):
        if not self.gctrl & 1:
            return self.frc_base
        return (self.frc_base + int((self.sim.t - self.t_base) * self._rate() + 1e-6)) & 0xFFFFFFFF

    def _rebase(self):
        self.frc_base, self.t_base = self.frc(), self.sim.t

    def _schedule(self):
        self.sim.cancel(self.ev)
        self.ev = None
        if not self.gctrl & 1:
            return
        now = self.frc()
        ticks = (self.comp0 - now) & 0xFFFFFFFF
        if ticks == 0:
            ticks = 1 << 32
        t_match = self.t_base + ((now - self.frc_base) % 2**32 + ticks) / self._rate()
        self.ev = self.sim.at(t_match, self._match)

    def _match(self):
        self.ev = None
        self.intflag |= 1
        self.comp0 = (self.comp0 + self.udcp0) & 0xFFFFFFFF
        self._irq()
        self._schedule()

    def _irq(self):
        self.vim.set_line(self.ch, self.intflag & self.intena & 1)

    def read(self, off, size):
        return {0x00: self.gctrl, 0x0C: self.compctrl, 0x10: self.frc(), 0x18: self.cpuc0, 0x50: self.comp0,
                0x54: self.udcp0, 0x80: self.intena, 0x84: self.intena, 0x88: self.intflag}.get(off, 0)

    def write(self, off, size, v):
        if off == 0x00:
            self._rebase()
            self.gctrl = v
            self.t_base = self.sim.t
        elif off == 0x0C:
            self.compctrl = v
        elif off == 0x10:
            self.frc_base, self.t_base = v, self.sim.t
        elif off == 0x18:
            self._rebase()
            self.cpuc0 = v
        elif off == 0x50:
            self.comp0 = v
        elif off == 0x54:
            self.udcp0 = v
        elif off == 0x80:
            self.intena |= v
        elif off == 0x84:
            self.intena &= ~v
        elif off == 0x88:
            self.intflag &= ~v
        self._irq()
        if off in (0x00, 0x10, 0x18, 0x50):
            self._schedule()


# ---------------------------------------------------------------------------- IOMUX
# Pad register address -> ball, SWRS237C Table 6-2 (the balls this board uses)
PADS = {0xEA00: "B2", 0xEA0C: "F2", 0xEA10: "D1", 0xEA14: "D2", 0xEA18: "C2", 0xEA1C: "G1", 0xEA20: "G3",
        0xEA24: "E2", 0xEA64: "K3", 0xEA68: "V10", 0xEA6C: "U12", 0xEA74: "V16", 0xEA78: "U16"}


class IOMUX(Reg32Device):
    """Pad configuration registers, SWRS237C Table 6-3. Reset value: OE_OVERRIDE = 1,
    OE_OVERRIDE_CTRL = 1 (output forced off), FUNC_SEL = 1. A peripheral reaches the ball only
    when FUNC_SEL selects it, and drives it only when the output override is released."""

    def __init__(self, sim):
        super().__init__(sim, "IOMUX")
        self.regs = {}

    def reg(self, ball):
        for off, b in PADS.items():
            if b == ball:
                return self.regs.get(off, 0xC1)
        raise KeyError(ball)

    def func(self, ball):
        return self.reg(ball) & 0xF

    def drives(self, ball, mode):
        r = self.reg(ball)
        return (r & 0xF) == mode and (not bit(r, 6) or not bit(r, 7))

    def receives(self, ball, mode):
        r = self.reg(ball)
        return (r & 0xF) == mode and not bit(r, 4)

    def read(self, off, size):
        return self.regs.get(off + 0xEA00, 0xC1)

    def write(self, off, size, v):
        self.regs[off + 0xEA00] = v & 0x7FF


# ---------------------------------------------------------------------------- SCI
class SCI(Reg32Device):
    """SCI / UART in asynchronous mode, SWRU522E chapter 26. Double-buffered transmitter
    (SCITD + shift register), single receive buffer. Baud = VCLK / (16 (BRS + 1))."""

    def __init__(self, sim, name, vim, ch_lvl0, ch_lvl1, iomux, tx_pad, rx_pad):
        super().__init__(sim, name)
        self.vim, self.ch0, self.ch1, self.iomux = vim, ch_lvl0, ch_lvl1, iomux
        self.tx_pad, self.rx_pad = tx_pad, rx_pad     # (ball, mode) or None
        self.gcr0 = self.gcr1 = self.setint = self.setintlvl = self.fmt = self.brs = 0
        self.pio0 = 0
        self.flr = 0
        self.rd = 0
        self.td_full = False
        self.td = 0
        self.shifting = False
        self.tx_sink = None        # callable(byte, baud) -- the net the TX ball drives
        self.tx_count = 0
        self.warned = set()

    @property
    def baud(self):
        return VCLK / (16 * (self.brs + 1))

    def ready(self):
        return bool(self.gcr0 & 1) and bool(self.gcr1 & (1 << 7))

    def _irq(self):
        act = 0
        if self.setint & (1 << 9) and self.flr & (1 << 9):
            act |= 1 << 9
        if self.setint & (1 << 8) and self.flr & (1 << 8):
            act |= 1 << 8
        for b in (24, 25, 26):
            if self.setint & (1 << b) and self.flr & (1 << b):
                act |= 1 << b
        lvl1 = act & self.setintlvl
        self.vim.set_line(self.ch0, bool(act & ~self.setintlvl))
        self.vim.set_line(self.ch1, bool(lvl1))

    def _warn(self, key, text):
        if key not in self.warned:
            self.warned.add(key)
            self.sim.info(self.name, text)

    def _start_shift(self, byte):
        self.shifting = True
        bits = 1 + (self.fmt & 7) + 1 + 1 + bit(self.gcr1, 4) + bit(self.gcr1, 2)
        self.sim.after(bits / self.baud, self._shift_done, byte)

    def _shift_done(self, byte):
        self.shifting = False
        self.tx_count += 1
        ok = bit(self.pio0, 2)
        if not ok:
            self._warn("pio", f"{self.name}: TX byte not on the pin, SCIPIO0 TX FUNC = 0")
        elif self.tx_pad and not self.iomux.drives(*self.tx_pad):
            self._warn("mux", f"{self.name}: TX byte not on ball {self.tx_pad[0]}: pad mode "
                              f"{self.iomux.func(self.tx_pad[0])}, needs {self.tx_pad[1]} with the output released")
        elif self.tx_sink:
            self.tx_sink(byte, self.baud)
        if self.td_full:
            self.td_full = False
            self.flr |= 1 << 8
            self._start_shift(self.td)
        else:
            self.flr |= 1 << 11
        self._irq()

    def rx_byte(self, byte, host_baud):
        """A character arrives on the RX ball from a host running at host_baud."""
        if not self.ready() or not (self.gcr1 & (1 << 24)):
            return
        if not bit(self.pio0, 1) or (self.rx_pad and not self.iomux.receives(*self.rx_pad)):
            self._warn("rxmux", f"{self.name}: RX ball not connected to the SCI (pad mode / SCIPIO0)")
            return
        err = host_baud / self.baud - 1
        # 16x oversampling, sample at mid-bit: the stop bit (9.5 bit times in) is still sampled
        # correctly while the accumulated error stays under about 0.47 bit.
        if abs(err) * 9.5 > 0.47:
            self.flr |= 1 << 26
            byte = (byte >> 1) | 0x80 if err > 0 else (byte << 1) & 0xFF
        if self.flr & (1 << 9):
            self.flr |= 1 << 25
        self.rd = byte
        self.flr |= 1 << 9
        self._irq()

    def read(self, off, size):
        if off == 0x00:
            return self.gcr0
        if off == 0x04:
            return self.gcr1
        if off in (0x0C, 0x10):
            return self.setint
        if off in (0x14, 0x18):
            return self.setintlvl
        if off == 0x1C:
            return self.flr
        if off == 0x28:
            return self.fmt
        if off == 0x2C:
            return self.brs
        if off in (0x30, 0x34):
            v = self.rd
            if off == 0x34:
                self.flr &= ~(1 << 9)
                self._irq()
            return v
        if off == 0x3C:
            return self.pio0
        return 0

    def write(self, off, size, v):
        if off == 0x00:
            self.gcr0 = v & 1
            if not v & 1:
                self.gcr1 = self.setint = self.setintlvl = self.flr = 0
        elif off == 0x04:
            was = self.ready()
            self.gcr1 = v
            if self.ready() and not was:
                self.flr = (self.flr | (1 << 8) | (1 << 11)) & ~(1 << 9)
            elif not self.ready():
                self.flr = 0
                self.td_full = False
        elif off == 0x0C:
            self.setint |= v
        elif off == 0x10:
            self.setint &= ~v
        elif off == 0x14:
            self.setintlvl |= v
        elif off == 0x18:
            self.setintlvl &= ~v
        elif off == 0x1C:
            self.flr &= ~(v & ((1 << 24) | (1 << 25) | (1 << 26)))
        elif off == 0x28:
            self.fmt = v & 7
        elif off == 0x2C:
            self.brs = v & 0xFFFFFF
        elif off == 0x38:
            if not self.ready() or not self.gcr1 & (1 << 25):
                self._warn("txen", f"{self.name}: write to SCITD with the transmitter disabled")
                return
            v &= 0xFF
            self.flr &= ~(1 << 11)
            if not self.shifting:
                self._start_shift(v)
            elif not self.td_full:
                self.td, self.td_full = v, True
                self.flr &= ~(1 << 8)
            else:
                self.td = v           # overwritten: firmware did not wait for TXRDY
                self._warn("ovw", f"{self.name}: SCITD overwritten before TXRDY")
        elif off == 0x3C:
            self.pio0 = v & 6
        self._irq()


# ---------------------------------------------------------------------------- I2C
class I2C(Reg32Device):
    """I2C controller, master mode, 7-bit addressing. SWRU522E chapter 25.
    SCL = (VCLK / (ICPSC + 1)) / ((ICCLKL + d) + (ICCLKH + d)), d = 7/6/5 for ICPSC 0/1/>1
    (TI's convention for this module; the TRM lists the registers only)."""

    def __init__(self, sim, vim, ch, iomux, bus):
        super().__init__(sim, "I2C")
        self.vim, self.ch, self.iomux, self.bus = vim, ch, iomux, bus
        self.oar = self.imr = self.clkl = self.clkh = self.cnt = self.sar = self.psc = 0
        self.mdr = 0
        self.str = 0
        self.drr = 0
        self.dxr = None
        self.state = "idle"       # idle | addr | tx | rx | hold | nack
        self.target = None
        self.cnt_left = 0
        self.busy_ev = None
        self.transactions = 0
        self.warned = set()

    def scl(self):
        fmod = VCLK / (self.psc + 1)
        d = 7 if self.psc == 0 else 6 if self.psc == 1 else 5
        return fmod / ((self.clkl + d) + (self.clkh + d)), fmod

    def _irq(self):
        self.vim.set_line(self.ch, bool(self.str & self.imr & 0x3F))

    def _bit(self):
        return 1 / self.scl()[0]

    def _connected(self):
        return self.iomux.drives("G3", 2) and self.iomux.drives("G1", 2)

    def _start(self):
        f, fmod = self.scl()
        if "clk" not in self.warned:
            self.warned.add("clk")
            self.sim.info("I2C", f"SCL {f/1e3:.1f} kHz (module clock {fmod/1e6:.2f} MHz)")
            if not 6.7e6 <= fmod <= 13.3e6:
                self.sim.violation("I2C", f"module clock {fmod/1e6:.2f} MHz outside 6.7-13.3 MHz")
            if f > 400e3 * 1.001:
                self.sim.violation("I2C", f"SCL {f/1e3:.0f} kHz above the 400 kHz fast-mode limit")
        self.str |= 1 << 12                     # BB
        self.mdr &= ~(1 << 13)                  # STT clears once the start condition is out
        self.state = "addr"
        self.cnt_left = self.cnt or 65536
        self.busy_ev = self.sim.after(10 * self._bit(), self._addr_done)

    def _addr_done(self):
        self.transactions += 1
        read = not (self.mdr & (1 << 9))
        ack = self._connected() and self.bus.start(self.sar & 0x7F, read)
        if not self._connected() and "mux" not in self.warned:
            self.warned.add("mux")
            self.sim.info("I2C", "SCL/SDA balls G3/G1 not in mode 2: nothing reaches the PMIC")
        if not ack:
            self.str |= 1 << 1                   # NACK
            self.state = "nack"
            if self.mdr & (1 << 11):
                self._stop()
        elif not read:
            self.state = "tx"
            self.str |= 1 << 4                   # XRDY
            if self.dxr is not None:
                self._tx_byte()
        else:
            self.state = "rx"
            self.busy_ev = self.sim.after(9 * self._bit(), self._rx_done)
        self._irq()

    def _tx_byte(self):
        b, self.dxr = self.dxr, None
        self.str &= ~(1 << 4)
        self.state = "tx_busy"
        self.busy_ev = self.sim.after(9 * self._bit(), self._tx_done, b)

    def _tx_done(self, b):
        ack = self._connected() and self.bus.write(b)
        self.cnt_left -= 1
        if not ack:
            self.str |= 1 << 1
            self.state = "nack"
            if self.mdr & (1 << 11):
                self._stop()
        elif self.cnt_left == 0:
            self._end_count()
        else:
            self.state = "tx"
            self.str |= 1 << 4
            if self.dxr is not None:
                self._tx_byte()
        self._irq()

    def _rx_done(self):
        self.drr = self.bus.read() if self._connected() else 0xFF
        self.str |= 1 << 3
        self.cnt_left -= 1
        if self.cnt_left == 0:
            self._end_count()
        else:
            self.busy_ev = self.sim.after(9 * self._bit(), self._rx_done)
        self._irq()

    def _end_count(self):
        if self.mdr & (1 << 11):
            self._stop()
        else:
            self.state = "hold"
            self.str |= 1 << 2                   # ARDY, bus held for a repeated start

    def _stop(self):
        self.state = "stopping"
        self.sim.after(self._bit(), self._stopped)

    def _stopped(self):
        self.bus.stop()
        self.state = "idle"
        self.str = (self.str | (1 << 5)) & ~(1 << 12)
        self.mdr &= ~((1 << 11) | (1 << 10) | (1 << 13))
        self._irq()

    def read(self, off, size):
        regs = {0x00: self.oar, 0x04: self.imr, 0x08: self.str, 0x0C: self.clkl, 0x10: self.clkh, 0x14: self.cnt,
                0x1C: self.sar, 0x24: self.mdr, 0x30: self.psc, 0x34: 0x0104, 0x38: 0x0045}
        if off == 0x18:
            self.str &= ~(1 << 3)
            self._irq()
            return self.drr
        return regs.get(off, 0)

    def write(self, off, size, v):
        if off == 0x00:
            self.oar = v
        elif off == 0x04:
            self.imr = v
        elif off == 0x08:
            self.str &= ~(v & 0x27)              # AL, NACK, ARDY, SCD: write 1 to clear
        elif off == 0x0C:
            self.clkl = v & 0xFFFF
        elif off == 0x10:
            self.clkh = v & 0xFFFF
        elif off == 0x14:
            self.cnt = v & 0xFFFF
        elif off == 0x1C:
            self.sar = v & 0x3FF
        elif off == 0x20:
            self.dxr = v & 0xFF
            self.str &= ~(1 << 4)
            if self.state == "tx":
                self._tx_byte()
        elif off == 0x30:
            self.psc = v & 0xFF
        elif off == 0x24:
            old = self.mdr
            self.mdr = v
            if not v & (1 << 5):                 # IRS = 0: module reset
                self.sim.cancel(self.busy_ev)
                self.state, self.str, self.dxr = "idle", 0, None
            elif not old & (1 << 5):
                self.str = (1 << 4) | (1 << 10)
            elif v & (1 << 13) and v & (1 << 10):  # STT in master mode
                if self.state in ("idle", "hold"):
                    if self.state == "hold":
                        self.str &= ~(1 << 2)
                    self._start()
            elif v & (1 << 11) and self.state in ("hold", "nack"):
                self._stop()
        self._irq()


# ---------------------------------------------------------------------------- mailbox
class MailboxRegs(Reg32Device):
    """One mailbox register block (SWRU522E 18.2): INT_STS_CLR 0x18, INT_ACK 0x20, INT_TRIG 0x28,
    INT_STS_MASKED 0x30, INT_STS_RAW 0x38. Bit 0 = new message, bit 1 = read complete."""

    def __init__(self, sim, name, on_trig=None, vim=None, chans=None):
        super().__init__(sim, name)
        self.raw = 0
        self.on_trig = on_trig
        self.vim, self.chans = vim, chans

    def _irq(self):
        if self.vim:
            for b, ch in enumerate(self.chans):
                self.vim.set_line(ch, bool(self.raw & (1 << b)))

    def trigger(self, bits):
        self.raw |= bits
        self._irq()
        if self.on_trig:
            self.on_trig(bits)

    def read(self, off, size):
        return self.raw if off in (0x30, 0x38) else 0

    def write(self, off, size, v):
        if off in (0x18, 0x20):
            self.raw &= ~v
            self._irq()
        elif off == 0x28:
            self.trigger(v & 3)
