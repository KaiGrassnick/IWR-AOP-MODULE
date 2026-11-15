"""Simulation kernel: virtual time, events, the Cortex-R core and the memory-mapped bus.

The CPU is Unicorn's Cortex-R5F (QEMU's TCG). The IWR6843's MSS core is a Cortex-R4F; the R5F
runs the same ARMv7-R ARM/Thumb-2 and VFPv3-D16 code, which is all the firmware uses. Time is
counted as one instruction per 5 ns (200 MHz, one instruction per cycle). That is not cycle
accurate: instruction timing on the real core depends on the pipeline and TCM wait states.
Every timing that matters to the hardware (UART bits, I2C clocks, RTI counters, rails, reset)
comes from the peripheral and board models in virtual time, not from instruction counts.
"""
import heapq
import itertools

from unicorn import UC_ARCH_ARM, UC_HOOK_MEM_READ_UNMAPPED, UC_HOOK_MEM_WRITE_UNMAPPED, UC_MODE_ARM, Uc, UcError
from unicorn.arm_const import (UC_ARM_REG_CPSR, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SPSR,
                               UC_CPU_ARM_CORTEX_R5F)

IPS = 200e6           # instructions per second of virtual time
SLICE = 400           # instructions per CPU slice (2 us)
WFI = 0xE320F003


class Sim:
    def __init__(self, name="sim"):
        self.name = name
        self.t = 0.0
        self._q = []
        self._seq = itertools.count()
        self._cancelled = set()
        self.log = []            # (t, source, text)
        self.violations = []     # (t, source, text): rule broken by firmware or a host
        self.unmodelled = {}     # address -> count
        self.uc = None
        self.cpu = "off"         # off | run | wfi | halted
        self.devices = []        # (lo, hi, dev)
        self.ram_maps = []       # (base, size)
        self.vim = None
        self.instructions = 0
        self.fault = None

    # ------------------------------------------------------------------ events
    def at(self, t, fn, *args):
        tok = next(self._seq)
        heapq.heappush(self._q, (t, tok, fn, args))
        return tok

    def after(self, dt, fn, *args):
        return self.at(self.t + dt, fn, *args)

    def cancel(self, tok):
        if tok is not None:
            self._cancelled.add(tok)

    def info(self, src, text):
        self.log.append((self.t, src, text))

    def violation(self, src, text):
        self.violations.append((self.t, src, text))
        self.log.append((self.t, src, "VIOLATION: " + text))

    # ------------------------------------------------------------------ bus
    def add_device(self, lo, size, dev):
        self.devices.append((lo, lo + size, dev))

    def add_ram(self, base, size):
        self.ram_maps.append((base, size))

    def _find(self, addr):
        for lo, hi, dev in self.devices:
            if lo <= addr < hi:
                return lo, dev
        return None, None

    def _mmio_read(self, uc, off, size, page):
        addr = page + off
        lo, dev = self._find(addr)
        if dev is None:
            self.unmodelled[addr] = self.unmodelled.get(addr, 0) + 1
            return 0
        return dev.read(addr - lo, size) & ((1 << (8 * size)) - 1)

    def _mmio_write(self, uc, off, size, value, page):
        addr = page + off
        lo, dev = self._find(addr)
        if dev is None:
            self.unmodelled[addr] = self.unmodelled.get(addr, 0) + 1
            return
        dev.write(addr - lo, size, value)

    def _unmapped(self, uc, access, addr, size, value, _):
        page = addr & ~0xFFF
        uc.mmio_map(page, 0x1000, self._mmio_read, page, self._mmio_write, page)
        return True

    # ------------------------------------------------------------------ CPU
    def cpu_power_on(self, image_segments, entry=0):
        """New core (power-on reset of the R4F), memories loaded by the boot ROM model."""
        uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_R5F)
        for base, size in self.ram_maps:
            uc.mem_map(base, size)
        pages = sorted({lo & ~0xFFF for lo, hi, _ in self.devices} |
                       {p for lo, hi, _ in self.devices for p in range(lo & ~0xFFF, hi, 0x1000)})
        for page in pages:
            uc.mmio_map(page, 0x1000, self._mmio_read, page, self._mmio_write, page)
        uc.hook_add(UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED, self._unmapped)
        for addr, data in image_segments:
            uc.mem_write(addr, data)
        self.uc = uc
        self.pc = entry
        # reset state: SVC mode, IRQ/FIQ masked, ARM state
        uc.reg_write(UC_ARM_REG_CPSR, 0x1D3)
        self.cpu = "run"

    def cpu_off(self):
        self.uc = None
        self.cpu = "off"

    def mem_read32(self, addr):
        return int.from_bytes(self.uc.mem_read(addr, 4), "little") if self.uc else 0

    def mem_read(self, addr, n):
        return bytes(self.uc.mem_read(addr, n)) if self.uc else bytes(n)

    def mem_write(self, addr, data):
        if self.uc:
            self.uc.mem_write(addr, bytes(data))

    def _take_irq(self):
        uc = self.uc
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        # IRQ entry (ARMv7-R): IRQ mode, I and A set, ARM state, LR = return + 4, PC = 0x18
        uc.reg_write(UC_ARM_REG_CPSR, (cpsr & ~0x3F) | 0x12 | 0x80 | 0x100)
        uc.reg_write(UC_ARM_REG_SPSR, cpsr)
        uc.reg_write(UC_ARM_REG_LR, self.pc + 4)
        self.pc = 0x18

    def _step_cpu(self, t_limit):
        uc = self.uc
        irq = self.vim is not None and self.vim.irq_pending()
        if self.cpu == "wfi":
            if irq:
                self.cpu = "run"
            else:
                self.t = t_limit
                return
        masked = bool(uc.reg_read(UC_ARM_REG_CPSR) & 0x80)
        if irq and not masked:
            self._take_irq()
        n = int((t_limit - self.t) * IPS) + 1
        # an interrupt waiting behind CPSR.I is taken as soon as the core unmasks it:
        # step in small slices until then
        n = max(1, min(4 if (irq and masked) else SLICE, n))
        thumb = uc.reg_read(UC_ARM_REG_CPSR) & 0x20
        try:
            uc.emu_start(self.pc | (1 if thumb else 0), 0xFFFFFFFF, count=n)
        except UcError as e:
            pc = uc.reg_read(UC_ARM_REG_PC)
            self.fault = f"{e} at pc=0x{pc:08x}"
            self.info("cpu", "CPU stopped: " + self.fault)
            self.cpu = "halted"
            return
        self.pc = uc.reg_read(UC_ARM_REG_PC)
        if self.cpu == "off":          # a device reset the core from inside a hook
            return
        self.instructions += n
        self.t += n / IPS
        if not uc.reg_read(UC_ARM_REG_CPSR) & 0x20 and \
                int.from_bytes(uc.mem_read(self.pc - 4, 4), "little") == WFI:
            self.cpu = "wfi"

    def run(self, until):
        while True:
            while self._q and self._q[0][0] <= self.t:
                t, tok, fn, args = heapq.heappop(self._q)
                if tok in self._cancelled:
                    self._cancelled.discard(tok)
                    continue
                fn(*args)
            if self.t >= until:
                break
            t_next = min(self._q[0][0] if self._q else until, until)
            if self.cpu in ("run", "wfi"):
                self._step_cpu(t_next)
            else:
                self.t = t_next


class Reg32Device:
    """Base for register-block peripherals: word registers in a dict, with hooks."""

    def __init__(self, sim, name):
        self.sim = sim
        self.name = name

    def read(self, off, size):
        return 0

    def write(self, off, size, value):
        pass
