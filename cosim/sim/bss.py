"""Stand-in for the radar subsystem (BSS) and the radar's supply current.

TI's BSS firmware is closed and its mmWaveLink protocol is not published at register level,
so this is a behavioural model, not an emulation. It answers the MSS over the real mailbox
transport (SWRU522E chapter 18) with the message layout of firmware/src/bss_msg.h, enforces
the rules that involve this board (external 1.0 V needs ldoBypassEnable = 3 before RF
power-up, three simultaneous TX only in LDO bypass, all rails inside their ranges), runs the
frame timing the firmware configured, and turns it into supply current per rail. The
detected points it reports are a fixed synthetic scene (no RF, no DSP).

Supply current per rail, from SWRS237C 7.7:
  'max' set: Table 7-3 worst case (2.0 A on 1.0 V with two TX, 2.5 A with three, 0.85 A on
             1.8 V, 1.0 A on 1.2 V), split into an RF-on base and a per-TX share [split is an
             assumption]. Used for the electrical replay.
  'typ' set: fitted so the frame-average power reproduces Table 7-4 (1.19 / 1.25 / 1.62 /
             1.75 W). Used for temperature and the PMIC current readback.
"""
import math
import struct

MAGIC = 0x4C4D4D43
WORDS = 64
MSS_MBOX4BSS_MEM = 0xF0601000
BSS_MBOX4MSS_MEM = 0xF0602000

ID = dict(CHANNEL_CFG=0x01, LDO_BYPASS_CFG=0x02, RF_INIT=0x03, PROFILE_CFG=0x04, CHIRP_CFG=0x05, FRAME_CFG=0x06,
          SENSOR_START=0x07, SENSOR_STOP=0x08, GET_TEMP=0x09, RESPONSE=0x8000, EV_READY=0x100,
          EV_RF_INIT_DONE=0x101, EV_FRAME_DONE=0x102, EV_ERROR=0x103, EV_STOPPED=0x104)
OK, ERR_LDO_NOT_SET, ERR_LDO_MISMATCH, ERR_3TX, ERR_PROFILE, ERR_FRAME, ERR_STATE, ERR_SUPPLY, ERR_UNKNOWN = range(9)
ERR_NAMES = ["OK", "LDO bypass not configured before RF power-up", "LDO setting does not match the board",
             "3 TX needs LDO bypass", "bad profile", "bad frame", "wrong state", "supply out of range", "unknown"]

LOAD = {
    "max": {"1V0": (0.10, 1.00, 0.50), "1V8": (0.10, 0.85, 0.0), "1V2": (0.30, 0.60, 0.0), "3V3": (0.03, 0.05, 0.0)},
    "typ": {"1V0": (0.05, 0.80, 0.29), "1V8": (0.10, 0.45, 0.0), "1V2": (0.40, 0.50, 0.0), "3V3": (0.013, 0.013, 0.0)},
}
VNOM = {"1V0": 1.0, "1V8": 1.8, "1V2": 1.2, "3V3": 3.3}
BSS_BOOT = 8e-3          # BSS ready after the MSS starts [assumption]
RF_INIT_TIME = 30e-3     # RF calibrations [assumption]
LATENCY = 30e-6          # BSS message handling [assumption]
TAU_THERMAL = 150.0      # s, heat sink + local board heat capacity x 27 K/W [estimate]
PMIC_K_PER_W = 22.5      # PMIC junction rise per radar watt with HS300 (thermal_summary.txt)


class BSS:
    def __init__(self, sim, board, ambient=25.0, rth=27.3, tau=TAU_THERMAL):
        self.sim, self.board = sim, board
        self.ambient, self.rth, self.tau = ambient, rth, tau
        self.fail_temp = False    # inject failing temperature reads
        self.pmic_extra = 0.0     # extra PMIC die rise, to inject a PMIC over-temperature
        self.temp_trace = []      # (t, radar Tj) at every thermal update
        self.tj = ambient
        self.t_thermal = 0.0
        self.p_avg = 0.0
        self.frames = []          # trace for the replay
        self.frame_starts = []    # FRAME_START pulses seen on K3
        self.errors = []
        self.reset()

    # ---------------------------------------------------------------- state
    def reset(self):
        self.on = False
        self.can_send = True
        self.outq = []
        self.ldo = None
        self.channel = (0, 0)
        self.rf_init = False
        self.rf_init_until = 0.0
        self.profile = None
        self.chirps = {}
        self.frame = None
        self.running = False
        self.stop_req = False
        self.frame_ev = None
        self.cur_frame = None
        self.frame_no = 0
        self.gen = getattr(self, "gen", 0) + 1

    def boot(self):
        self.reset()
        self.on = True
        self.sim.after(BSS_BOOT, self._gen_wrap(self._post), ID["EV_READY"], [])

    def _gen_wrap(self, fn):
        g = self.gen

        def w(*a):
            if g == self.gen:
                fn(*a)
        return w

    # ---------------------------------------------------------------- mailbox
    def on_mss_trigger(self, bits):
        """MSS wrote BSS_MBOX4MSS_REG INT_TRIG."""
        if not self.on:
            return
        if bits & 1:
            self.sim.after(LATENCY, self._gen_wrap(self._receive))
        if bits & 2:
            self.board.mb_bss.raw &= ~2
            self.can_send = True
            self._pump()

    def _receive(self):
        self.board.mb_bss.raw &= ~1
        w = struct.unpack("<64I", self.sim.mem_read(BSS_MBOX4MSS_MEM, 4 * WORDS))
        self.board.mb_mss.trigger(2)          # read complete -> MSS
        if w[0] != MAGIC:
            return
        self._handle(w[1], w[2], w[3:])

    def _post(self, mid, payload, seq=0):
        self.outq.append([MAGIC, mid, seq] + list(payload))
        self._pump()

    def _pump(self):
        if not self.on or not self.can_send or not self.outq or self.sim.uc is None:
            return
        msg = self.outq.pop(0)
        msg = (msg + [0] * WORDS)[:WORDS]
        self.sim.mem_write(MSS_MBOX4BSS_MEM, struct.pack("<64I", *[x & 0xFFFFFFFF for x in msg]))
        self.can_send = False
        self.board.mb_mss.trigger(1)

    def _respond(self, mid, seq, status, payload=()):
        if status:
            self.errors.append((self.sim.t, ERR_NAMES[status]))
            self.sim.info("BSS", f"command 0x{mid:02X} refused: {ERR_NAMES[status]}")
        self._post(mid | ID["RESPONSE"], [status] + list(payload), seq)

    # ---------------------------------------------------------------- commands
    def _handle(self, mid, seq, p):
        if mid == ID["CHANNEL_CFG"]:
            self.channel = (p[0] & 0xF, p[1] & 0x7)
            return self._respond(mid, seq, OK)
        if mid == ID["LDO_BYPASS_CFG"]:
            self.ldo = (p[0], p[1], p[2])
            self.sim.info("BSS", f"LDO bypass config: ldoBypassEnable={p[0]} supplyMonIrDrop={p[1]} "
                                 f"ioSupplyIndicator={p[2]}")
            # this board feeds 1.0 V to VIN_13RF1/2 and VOUT_PA: only mode 3 fits (spec 8.1)
            return self._respond(mid, seq, OK if p[0] == 3 and p[2] == 0 else ERR_LDO_MISMATCH)
        if mid == ID["RF_INIT"]:
            if self.ldo is None:
                self.sim.violation("BSS", "rlRfInit before rlRfSetLdoBypassConfig: internal RF/PA LDOs would "
                                          "drive against the external 1.0 V rail (spec 8.1)")
                return self._respond(mid, seq, ERR_LDO_NOT_SET)
            if self.ldo[0] != 3:
                return self._respond(mid, seq, ERR_LDO_MISMATCH)
            if not self.board.power.rails_ok():
                return self._respond(mid, seq, ERR_SUPPLY)
            self._respond(mid, seq, OK)
            self.rf_init_until = self.sim.t + RF_INIT_TIME
            self.frames.append(dict(kind="rf_init", t=self.sim.t, dur=RF_INIT_TIME, vset=self._vsets()))
            self.sim.after(RF_INIT_TIME, self._gen_wrap(self._rf_init_done))
            return
        if mid == ID["PROFILE_CFG"]:
            idle, adc, ramp, txs, n, ksps, slope = [x * 1e-8 if i < 4 else x for i, x in enumerate(p[:7])]
            t_adc = n / (ksps * 1e3) if ksps else 1.0
            ok = idle >= 2e-6 and ramp <= 500e-6 and txs < ramp and adc + t_adc <= ramp + 1e-9 and n > 0
            if ok:
                self.profile = dict(idle=idle, adc=adc, ramp=ramp, txs=txs, n=n, ksps=ksps, slope=slope)
            return self._respond(mid, seq, OK if ok else ERR_PROFILE)
        if mid == ID["CHIRP_CFG"]:
            for i in range(p[0], p[1] + 1):
                self.chirps[i] = p[2] & 7
            return self._respond(mid, seq, OK)
        if mid == ID["FRAME_CFG"]:
            cs, ce, loops, nframes, period = p[0], p[1], p[2], p[3], p[4] * 1e-6
            if self.profile is None or ce < cs or loops < 1:
                return self._respond(mid, seq, ERR_FRAME)
            seqc = [self.chirps.get(i, 0) for i in range(cs, ce + 1)] * loops
            active = len(seqc) * (self.profile["idle"] + self.profile["ramp"])
            if active > 0.95 * period:
                return self._respond(mid, seq, ERR_FRAME)
            self.frame = dict(seq=seqc, period=period, nframes=nframes, active=active)
            return self._respond(mid, seq, OK)
        if mid == ID["SENSOR_START"]:
            if not self.rf_init or self.frame is None:
                return self._respond(mid, seq, ERR_STATE)
            txs = [bin(m).count("1") for m in self.frame["seq"]]
            if max(txs) == 3 and (self.ldo is None or self.ldo[0] != 3):
                return self._respond(mid, seq, ERR_3TX)
            if any(m & ~self.channel[1] for m in self.frame["seq"]):
                return self._respond(mid, seq, ERR_FRAME)
            if not self.board.power.rails_ok():
                return self._respond(mid, seq, ERR_SUPPLY)
            self._respond(mid, seq, OK)
            self.running, self.stop_req, self.frame_no = True, False, 0
            self.frame_ev = self.sim.after(1e-3, self._gen_wrap(self._frame_start))
            return
        if mid == ID["SENSOR_STOP"]:
            self._respond(mid, seq, OK)
            if self.running:
                self.stop_req = True
            else:
                self._post(ID["EV_STOPPED"], [])
            return
        if mid == ID["GET_TEMP"]:
            if self.fail_temp:
                return self._respond(mid, seq, ERR_UNKNOWN)
            self._thermal_update()
            return self._respond(mid, seq, OK, [int(self.tj * 1000)])
        return self._respond(mid, seq, ERR_UNKNOWN)

    def _rf_init_done(self):
        self.rf_init = True
        self._post(ID["EV_RF_INIT_DONE"], [])

    def _vsets(self):
        from .pmic import vset_to_v
        r = self.board.pmic.regs
        return {"1V0": vset_to_v(r[0x0E]), "1V8": vset_to_v(r[0x10])}

    # ---------------------------------------------------------------- frames
    def _frame_start(self):
        t = self.sim.t
        if not self.board.power.rails_ok():
            self.running = False
            self.errors.append((t, ERR_NAMES[ERR_SUPPLY]))
            self._post(ID["EV_ERROR"], [ERR_SUPPLY])
            return
        self._thermal_update()
        self.frame_no += 1
        f = dict(kind="frame", t=t, n=self.frame_no, seq=[bin(m).count("1") for m in self.frame["seq"]],
                 idle=self.profile["idle"], ramp=self.profile["ramp"], txs=self.profile["txs"],
                 period=self.frame["period"], vset=self._vsets())
        self.cur_frame = f
        if len(self.frames) < 2000:
            self.frames.append(f)
        if self.board.iomux.drives("K3", 13):
            self.frame_starts.append(t)
        self.board.vim.set_line(98, 1)
        self.sim.after(1e-6, self._gen_wrap(self.board.vim.set_line), 98, 0)
        end = t + self.frame["active"]
        self.sim.at(end + 0.5e-3, self._gen_wrap(self._frame_done), f)
        last = self.frame["nframes"] and self.frame_no >= self.frame["nframes"]
        if not last:
            self.frame_ev = self.sim.at(t + self.frame["period"], self._gen_wrap(self._next_frame))

    def _next_frame(self):
        if self.stop_req:
            self.running = False
            self.cur_frame = None
            self._post(ID["EV_STOPPED"], [])
            return
        self._frame_start()

    def _frame_done(self, f):
        pts = self.scene(self.sim.t)
        payload = [f["n"], len(f["seq"]), len(pts)]
        for r, v, az in pts:
            payload += [int(r * 1000), int(v * 1000) & 0xFFFFFFFF, int(az * 1000) & 0xFFFFFFFF]
        self._post(ID["EV_FRAME_DONE"], payload)
        last = self.frame["nframes"] and f["n"] >= self.frame["nframes"]
        if self.stop_req or last:
            self.sim.cancel(self.frame_ev)
            self.running = False
            self.cur_frame = None
            self._post(ID["EV_STOPPED"], [])

    @staticmethod
    def scene(t):
        """Synthetic: a person walking between 1.5 and 2.5 m, and a wall at 4.6 m."""
        r = 2.0 + 0.5 * math.sin(2 * math.pi * t / 4.0)
        v = 0.5 * 2 * math.pi / 4.0 * math.cos(2 * math.pi * t / 4.0)
        return [(r, v, 12.0), (4.6, 0.0, -20.0)]

    # ---------------------------------------------------------------- supply current
    def current(self, rail, t, kind="typ"):
        idle, act, per_tx = LOAD[kind][rail]
        if not self.on:
            return 0.0
        if t < self.rf_init_until:
            return act
        f = self.cur_frame
        if not f or not self.running or t < f["t"]:
            return idle
        tc = f["idle"] + f["ramp"]
        k = int((t - f["t"]) / tc)
        if k >= len(f["seq"]):
            return idle
        x = t - f["t"] - k * tc - f["idle"]
        on = f["txs"] <= x < f["ramp"]
        return act + (per_tx * f["seq"][k] if on else 0.0)

    def frame_power(self, f, kind="typ"):
        """Average radar power over one frame period."""
        tc = f["idle"] + f["ramp"]
        active = len(f["seq"]) * tc
        p = 0.0
        for rail, (idle, act, per_tx) in LOAD[kind].items():
            tx_time = sum(f["seq"]) * (f["ramp"] - f["txs"])
            q = idle * (f["period"] - active) + act * active + per_tx * tx_time
            p += VNOM[rail] * q / f["period"]
        return p

    def idle_power(self, kind="typ"):
        return sum(VNOM[r] * v[0] for r, v in LOAD[kind].items())

    def _thermal_update(self):
        t = self.sim.t
        dt = t - self.t_thermal
        self.t_thermal = t
        p = self.frame_power(self.cur_frame) if (self.running and self.cur_frame) else \
            (self.idle_power() if self.on else 0.0)
        self.p_avg = p
        target = self.ambient + self.rth * p
        self.tj = target + (self.tj - target) * math.exp(-dt / self.tau)
        self.temp_trace.append((t, self.tj))
        warn = 137 if self.board.pmic.regs[0x19] & 0x08 else 125     # TDIE_WARN_LEVEL (CONFIG)
        if self.pmic_tj() > warn:
            self.board.pmic.flag(0x1A, 0x04)                          # INT_TOP1.TDIE_WARN

    def pmic_tj(self):
        return self.ambient + PMIC_K_PER_W * getattr(self, "p_avg", 0.0) + 5.0 + self.pmic_extra
