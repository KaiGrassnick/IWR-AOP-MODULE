"""IWR-AOP-MODULE Rev 1.1 assembled from the models: the radar's MSS peripherals, the power tree,
the PMIC, the boot ROM and QSPI flash, the radar subsystem stand-in, the CP2105 USB bridge and a
carrier on J400/J401. Scenarios drive it through the methods at the bottom, the way a person at
the bench (or a carrier board) would."""
import re
import struct

from .bss import BSS
from .core import Sim
from .pmic import LP87524, I2CBus
from .power import PowerTree
from .soc import I2C, IOMUX, RTI, SCI, VIM, MailboxRegs

FLASH_QE_TW = 40e-3        # MX25L3233F tW, write status register, maximum (datasheet table)
FLASH_TPP = 0.33e-3        # page program, typical
FLASH_TSE = 25e-3          # 4 KB sector erase, typical
ROM_OVERHEAD = 5e-3        # boot ROM before it reads the image [assumption]
QSPI_RATE = 20e6           # bytes/s: quad read at 40 MHz [assumption]


def load_elf(path):
    d = open(path, "rb").read()
    assert d[:4] == b"\x7fELF" and d[4] == 1 and d[5] == 1, "32-bit little-endian ELF expected"
    phoff, = struct.unpack_from("<I", d, 28)
    phentsize, phnum = struct.unpack_from("<HH", d, 42)
    segs = []
    for i in range(phnum):
        p_type, p_off, p_vaddr, p_paddr, p_filesz, p_memsz, flags, align = struct.unpack_from("<8I", d, phoff + i * phentsize)
        if p_type == 1 and p_filesz:
            segs.append((p_paddr, d[p_off:p_off + p_filesz]))
    return segs


class Flash:
    """MX25L3233F as the boot ROM and the flashing tool see it: an image, the QE bit and WIP."""

    def __init__(self, sim):
        self.sim = sim
        self.segments = None
        self.qe = False
        self.busy_until = 0.0

    def busy(self):
        return self.sim.t < self.busy_until

    @property
    def size(self):
        return sum(len(d) for a, d in self.segments) if self.segments else 0


class HostUart:
    """One host-side UART: a CP2105 channel (48 MHz baud generator, assumed) or a carrier MCU."""

    def __init__(self, board, name, baud, powered, clock=48e6, rx_target=None):
        self.board, self.sim, self.name = board, board.sim, name
        self.baud_nominal = baud
        self.baud = clock / round(clock / baud) if clock else baud
        self.powered = powered
        self.rx_target = rx_target      # 'A' -> UARTA RX ball, None -> receive only
        self.data = bytearray()
        self.framing_errors = 0
        self.busy_until = 0.0

    def from_radar(self, byte, radar_baud):
        if not self.powered():
            return
        err = radar_baud / self.baud - 1
        if abs(err) * 9.5 > 0.47:
            self.framing_errors += 1
            byte = (byte >> 1) | 0x80 if err > 0 else (byte << 1) & 0xFF
        self.data.append(byte)

    def text(self):
        return self.data.decode("latin-1").replace("\r", "")

    def send(self, s):
        if isinstance(s, str):
            s = s.encode()
        t = max(self.sim.t, self.busy_until)
        dt = 10 / self.baud
        for b in s:
            t += dt
            self.sim.at(t, self._deliver, b)
        self.busy_until = t

    def _deliver(self, b):
        if not self.powered():
            return
        sci = self.board.scia if self.rx_target == "A" else None
        if sci is not None and self.board.radar_running():
            sci.rx_byte(b, self.baud)


class Board:
    def __init__(self, fw_elf, ambient=25.0, name="board"):
        self.sim = Sim(name)
        self.fw = load_elf(fw_elf)
        self.flash = Flash(self.sim)
        self.power = PowerTree(self.sim, self)
        self.pmic = LP87524(self.sim, self.power)
        self.power.pmic = self.pmic
        self.bus = I2CBus(self.sim, {LP87524.ADDR: self.pmic})
        from . import calib
        self.bss = BSS(self.sim, self, ambient=ambient, rth=calib.load()["rth_hs"])
        self.boot_button = False
        self.jp300 = False
        self.carrier_present = False
        self.carrier_sop2 = False
        self.rom_mode = None
        self.boots = []            # (t, mode, result)
        usb = lambda: self.power.usb
        car = lambda: self.carrier_present
        self.eci = HostUart(self, "CP2105 ECI", 115200, usb, rx_target="A")
        self.sci_port = HostUart(self, "CP2105 SCI", 460800, usb)
        self.car_uart = HostUart(self, "carrier UART", 115200, car, clock=None, rx_target="A")
        self.car_data = HostUart(self, "carrier data", 460800, car, clock=None)
        self.sim.add_ram(0x00000000, 512 * 1024)      # TCMA
        self.sim.add_ram(0x08000000, 192 * 1024)      # TCMB
        self.sim.add_ram(0x0C200000, 0x2000)          # SW buffer
        self.sim.add_ram(0xF0601000, 0x7000)          # mailbox memories
        self.sim.add_ram(0xFFF82000, 0x1000)          # VIM RAM
        self._build_soc()

    # ---------------------------------------------------------------- SoC
    def _build_soc(self):
        s = self.sim
        s.devices = []
        self.vim = VIM(s)
        s.vim = self.vim
        self.iomux = IOMUX(s)
        self.rti = RTI(s, self.vim)
        self.scia = SCI(s, "UARTA", self.vim, 64, 74, self.iomux, ("U16", 5), ("V16", 2))
        self.scib = SCI(s, "UARTB", self.vim, 65, 75, self.iomux, ("E2", 6), None)
        self.scia.tx_sink = lambda b, baud: [h.from_radar(b, baud) for h in (self.eci, self.car_uart)]
        self.scib.tx_sink = lambda b, baud: [h.from_radar(b, baud) for h in (self.sci_port, self.car_data)]
        self.i2c = I2C(s, self.vim, 66, self.iomux, self.bus)
        self.mb_bss = MailboxRegs(s, "BSS_MBOX4MSS_REG", on_trig=lambda bits: self.bss.on_mss_trigger(bits))
        self.mb_mss = MailboxRegs(s, "MSS_MBOX4BSS_REG", vim=self.vim, chans=(95, 96))
        s.add_device(0xFFFFFE00, 0x100, self.vim)
        s.add_device(0xFFFFFC00, 0x100, self.rti)
        s.add_device(0xFFFFEA00, 0x100, self.iomux)
        s.add_device(0xFFF7E500, 0x100, self.scia)
        s.add_device(0xFFF7E700, 0x100, self.scib)
        s.add_device(0xFFF7D400, 0x100, self.i2c)
        s.add_device(0xF0608000, 0x100, self.mb_bss)
        s.add_device(0xF0608600, 0x100, self.mb_mss)

    def radar_running(self):
        return self.sim.cpu in ("run", "wfi")

    # ---------------------------------------------------------------- reset and boot ROM
    def nreset_changed(self, high):
        if not high:
            if self.sim.cpu != "off" or self.rom_mode:
                self.sim.info("radar", "held in reset")
            self.sim.cpu_off()
            self.bss.reset()
            self.bss.on = False
            self.rom_mode = None
            self._build_soc()
            return
        sop = (int(self.boot_button or self.carrier_sop2), int(self.jp300), 1)
        mode = {(0, 0, 1): "flash", (1, 0, 1): "uart", (0, 1, 1): "debug"}.get(sop, "unknown")
        self.rom_mode = mode
        if mode == "flash":
            f = self.flash
            why = None
            if not f.segments:
                why = "flash is empty"
            elif f.busy():
                why = "flash still busy (WIP = 1) when the ROM read it (errata MSS#45)"
            elif not f.qe:
                why = "QE bit clear: the ROM's quad read returns garbage (spec 4.2)"
            if why:
                self.boots.append((self.sim.t, mode, "failed: " + why))
                self.sim.info("ROM", "QSPI boot failed: " + why)
                return
            dt = ROM_OVERHEAD + f.size / QSPI_RATE
            self.sim.after(dt, self._start_cpu, self.sim.t)
        else:
            self.boots.append((self.sim.t, mode, "ROM waits (no application)"))
            self.sim.info("ROM", {"uart": "SOP 101: UART flashing mode, waiting for the host",
                                  "debug": "SOP 011: debug mode, R4F halted for JTAG"}.get(mode, f"SOP {sop}"))

    def _start_cpu(self, t_release):
        if not self.power.nreset or self.rom_mode != "flash":
            return
        self.sim.cpu_power_on(self.flash.segments, 0)
        self.boots.append((self.sim.t, "flash", f"application started {1e3*(self.sim.t - t_release):.1f} ms after reset release"))
        self.sim.info("ROM", "image loaded from QSPI, R4F released")
        self.bss.boot()

    # ---------------------------------------------------------------- bench / carrier actions
    def run(self, dt):
        self.sim.run(self.sim.t + dt)

    def plug_usb(self, on=True):
        if on and self.carrier_present and self.power.carrier:
            self.sim.violation("bench", "USB and carrier power together (spec 2: not supported)")
        self.power.set_source(usb=on)

    def carrier_power(self, on=True):
        self.carrier_present = True
        self.power.set_source(carrier=on)

    def host_disable(self, on):
        self.power.set_host_disable(on)

    def carrier_reset(self, low):
        self.power.set_low_source("carrier", low)

    def press_reset(self, down):
        self.power.set_low_source("button", down)

    def hold_boot(self, down):
        self.boot_button = down

    def flash_image(self, set_qe=True, wait_wip=True, segments=None):
        """UniFlash-style programming over UARTA in SOP 101 mode: erase, program, then (optionally)
        set the non-volatile QE bit. Returns the time it ends."""
        if self.rom_mode != "uart":
            raise RuntimeError("radar is not in UART flashing mode (hold BOOT, press RESET)")
        segs = segments or self.fw
        size = sum(len(d) for a, d in segs)
        t = size * 10 / self.eci.baud * 1.1            # transfer at 115200 with protocol overhead
        t += -(-size // 4096) * FLASH_TSE + -(-size // 256) * FLASH_TPP
        self.flash.segments = None
        self.flash.busy_until = self.sim.t + t

        def done():
            self.flash.segments = segs
            if set_qe:
                self.flash.qe = True
                self.flash.busy_until = self.sim.t + FLASH_QE_TW
            self.sim.info("flash", f"{size} bytes programmed" + (", QE set" if set_qe else ", QE left clear"))
        self.sim.after(t, done)
        self.sim.run(self.sim.t + t + 1e-6)
        if wait_wip:
            self.sim.run(max(self.sim.t, self.flash.busy_until) + 1e-6)
        return self.sim.t

    def preflash(self, qe=True):
        """Board already programmed (as after a previous flashing session)."""
        self.flash.segments = self.fw
        self.flash.qe = qe

    # ---------------------------------------------------------------- host helpers
    def cli(self, line, wait=0.2):
        port = self.eci if self.power.usb else self.car_uart
        start = len(port.data)
        port.send(line + "\r")
        self.wait_text(port, r"aop:/>", wait, start)
        return port.data[start:].decode("latin-1").replace("\r", "")

    def wait_text(self, port, pattern, timeout, start=0):
        t_end = self.sim.t + timeout
        rx = re.compile(pattern)
        while self.sim.t < t_end:
            self.sim.run(min(t_end, self.sim.t + 1e-3))
            if rx.search(port.data[start:].decode("latin-1")):
                return True
        return False

    def frames_from(self, port):
        """Parse TI-style packets from a data stream."""
        d = bytes(port.data)
        out = []
        magic = struct.pack("<4H", 0x0102, 0x0304, 0x0506, 0x0708)
        i = d.find(magic)
        while i >= 0 and i + 40 <= len(d):
            ver, total, plat, fno, tcpu, nobj, ntlv, sub = struct.unpack_from("<8I", d, i + 8)
            if i + total > len(d):
                break
            pkt = dict(frame=fno, nobj=nobj, points=[], telemetry=None)
            j = i + 40
            for _ in range(ntlv):
                typ, ln = struct.unpack_from("<2I", d, j)
                body = d[j + 8:j + 8 + ln]
                if typ == 1:
                    pkt["points"] = [struct.unpack_from("<4f", body, k) for k in range(0, ln, 16)]
                elif typ == 0x4D01:
                    b2, i2f, i2, i3f, i3, tmc, fl = struct.unpack("<5IiI", body[:28])
                    pkt["telemetry"] = dict(buck2_mv=b2, buck2_frame_ma=i2f, buck2_idle_ma=i2, buck3_frame_ma=i3f,
                                            buck3_idle_ma=i3, temp_c=tmc / 1000, pmic_flags=fl)
                j += 8 + ln
            out.append(pkt)
            i = d.find(magic, i + total)
        return out
