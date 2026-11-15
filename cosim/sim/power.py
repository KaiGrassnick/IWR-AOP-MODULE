"""Power entry, LP87524 rails, TPS386000 supervisor, PMIC GPIO2 and the AOP_NRESET net.

Timing comes from the ngspice results (calib.py): when EN1 rises after 5 V appears, when each
rail reaches 90 %, how long the supervisor waits, how fast the rails fall after an unplug or
HOST_DISABLE and when AOP_NRESET goes low. Rails are piecewise linear between those points;
that is enough for ordering and thresholds. Fast transients (chirp load steps) are not
computed here: replay/replay_rails.py runs the firmware's actual load through the ngspice loop
model.
"""
from . import calib
from .pmic import SLEW_V_PER_S, vset_to_v

C = calib.load()

RAILS = {  # name: buck, nominal, radar min, radar max, supervisor falling threshold (spec 3.3)
    "3V3": dict(buck=0, vmin=3.135, vmax=3.465, sup=3.067),
    "1V2": dict(buck=1, vmin=1.14, vmax=1.32, sup=1.120),
    "1V0": dict(buck=2, vmin=0.95, vmax=1.05, sup=0.900),
    "1V8": dict(buck=3, vmin=1.71, vmax=1.90, sup=1.653),
}
BUCK_RAIL = {v["buck"]: k for k, v in RAILS.items()}
SUP_HYST = 0.009       # TPS386000 rising = falling x 1.009 [assumption, datasheet not in repo]
SUP_PROP = 20e-6       # SENSE falling to RESET low [assumption]
SUP_DELAY = C["sup_delay"]
T_UVLO = 0.3e-3        # 5 V loss until VANA < UVLO (registers reset) [assumption, see README]
COPPER_DROP = {"1V0": 0.0144, "1V8": 0.016}   # V/A buck output above the radar (36 mV at 2.5 A)
NRESET_VIH = 1.57


class Rail:
    def __init__(self, name):
        self.name = name
        self.pts = [(0.0, 0.0)]
        self.gen = 0

    def v(self, t):
        p = self.pts
        if t <= p[0][0]:
            return p[0][1]
        for (t0, v0), (t1, v1) in zip(p, p[1:]):
            if t0 <= t <= t1:
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
        return p[-1][1]

    def go(self, t0, target, rate, delay=0.0):
        """From v(t0) hold for `delay`, then ramp linearly to target at `rate` V/s."""
        v0 = self.v(t0)
        self.pts = [x for x in self.pts if x[0] < t0] + [(t0, v0)]
        if delay:
            self.pts.append((t0 + delay, v0))
        ts = t0 + delay
        self.pts.append((ts + abs(target - v0) / rate, target))
        self.gen += 1

    def crossing(self, thr, rising, t_from):
        p = self.pts
        for (t0, v0), (t1, v1) in zip(p, p[1:]):
            if t1 < t_from:
                continue
            if rising and v0 < thr <= v1 or (not rising) and v0 > thr >= v1:
                tc = t0 + (thr - v0) / (v1 - v0) * (t1 - t0)
                if tc >= t_from:
                    return tc
        return None


class PowerTree:
    def __init__(self, sim, board):
        self.sim, self.board = sim, board
        self.rails = {r: Rail(r) for r in RAILS}
        self.usb = self.carrier = False
        self.host_disable = False
        self.sys5v = False
        self.t_power = None          # SYS_5V came up
        self.t_ready = None          # PMIC finished READ OTP
        self.en1 = False
        self.t_en1 = None
        self.buck_state = {b: "off" for b in range(4)}
        self.gpio2_low = True
        self.gpio2_tok = None
        self.sup_good = {r: False for r in RAILS}
        self.sup_low = True
        self.sup_ev = None
        self.release_ev = None
        self.low_sources = {"button": False, "carrier": False, "jtag": False}
        self.nreset = False
        self.events = []             # (t, text) for the timeline in the results
        self.pmic = None
        self.uvlo_ev = None
        self._ev = []

    # ---------------------------------------------------------------- inputs
    def note(self, text):
        self.events.append((self.sim.t, text))
        self.sim.info("power", text)

    def set_source(self, usb=None, carrier=None):
        if usb is not None:
            self.usb = usb
        if carrier is not None:
            self.carrier = carrier
        on = self.usb or self.carrier
        if on and not self.sys5v:
            self._sys5v_up()
        elif not on and self.sys5v:
            self._sys5v_down()

    def set_host_disable(self, on):
        self.host_disable = on
        self.note(f"HOST_DISABLE {'asserted' if on else 'released'}")
        self._update_en1()

    def set_low_source(self, name, low):
        self.low_sources[name] = low
        self._update_nreset()

    # ---------------------------------------------------------------- SYS_5V and EN1
    def _sys5v_up(self):
        t = self.sim.t
        self.sys5v = True
        self.sim.cancel(self.uvlo_ev)
        if self.uvlo_ev is not None and self.t_power is not None:
            # came back before VANA reached UVLO: PMIC kept its registers and timers
            self.uvlo_ev = None
            self.note("5 V back before PMIC UVLO")
            self._update_en1()
            return
        self.t_power = t
        self.t_ready = t + C["en1_after_5v"] + C["ready_offset"]
        self.pmic.otp_reload()
        self.note("5 V present")
        self.sim.at(t + C["en1_after_5v"], self._update_en1)

    def _sys5v_down(self):
        t = self.sim.t
        self.sys5v = False
        self.note("5 V removed")
        for r, tout in C["unplug_out"].items():
            spec = RAILS[r]
            vnom = self.rails[r].v(t)
            if vnom <= 0:
                continue
            rate = max(vnom - spec["vmin"], 0.01) / tout
            self.rails[r].go(t, 0.0, rate)
        # the supervisor sees 3V3_R first; calibrated to the ngspice unplug run
        self.sim.cancel(self.sup_ev)
        self.sup_ev = self.sim.at(t + C["unplug_nreset"], self._force_sup_low, "3V3_R below threshold (unplug)")
        self.uvlo_ev = self.sim.at(t + T_UVLO, self._uvlo)
        self.en1 = False
        self.buck_state = {b: "off" for b in range(4)}
        self._schedule_sup()

    def _uvlo(self):
        self.uvlo_ev = None
        self.t_power = None
        self.t_ready = None
        self.note("PMIC below UVLO: registers will reload from OTP")

    def _update_en1(self):
        t = self.sim.t
        want = self.sys5v and not self.host_disable and self.t_power is not None and \
            t >= self.t_power + C["en1_after_5v"] - 1e-12
        if want and not self.en1:
            self.en1, self.t_en1 = True, t
            self.note("EN1 high")
            base = max(t, self.t_ready or t)
            regs = self.pmic.regs
            for b in range(4):
                if regs[0x02 + 2 * b] & 0x80:
                    delay = (regs[0x12 + b] & 0x0F) * 1e-3
                    self._buck_on(b, base + delay - t)
            self.sim.cancel(self.gpio2_tok)
            self.gpio2_tok = self.sim.at(base + (regs[0x16] & 0x0F) * 1e-3, self._gpio2_release)
        elif not want and self.en1:
            self.en1 = False
            self.note("EN1 low")
            self.gpio2_low = True
            self.sim.cancel(self.gpio2_tok)
            self.sim.after(C["disable_nreset"], self._update_nreset)
            regs = self.pmic.regs
            for b in range(4):
                self._buck_off(b, ((regs[0x12 + b] >> 4) & 0x0F) * 1e-3)

    def _gpio2_release(self):
        if self.en1:
            self.gpio2_low = False
            self.note("PMIC GPIO2 released")
            self._update_nreset()

    # ---------------------------------------------------------------- bucks
    def _buck_on(self, b, delay):
        r = BUCK_RAIL[b]
        target = vset_to_v(self.pmic.regs[0x0A + 2 * b])
        self.buck_state[b] = "on"
        self.rails[r].go(self.sim.t, target, SLEW_V_PER_S, delay=max(delay, 0.0))
        self._schedule_sup()

    def _buck_off(self, b, delay):
        r = BUCK_RAIL[b]
        spec = RAILS[r]
        self.buck_state[b] = "off"
        v = max(self.rails[r].v(self.sim.t), 0.05)
        rate = max(v - spec["vmin"], 0.01) / C["disable_out"][r]
        self.rails[r].go(self.sim.t, 0.0, rate, delay=delay)
        self._schedule_sup()

    def pmic_registers_changed(self, reg):
        t = self.sim.t
        for b in range(4):
            on_bit = bool(self.pmic.regs[0x02 + 2 * b] & 0x80)
            v = vset_to_v(self.pmic.regs[0x0A + 2 * b])
            r = BUCK_RAIL[b]
            if self.en1 and on_bit and self.buck_state[b] == "off":
                self._buck_on(b, 0.0)
            elif not on_bit and self.buck_state[b] == "on":
                self.sim.violation("PMIC", f"Buck{b} ({r}_R) switched off over I2C")
                self._buck_off(b, 0.0)
            elif self.buck_state[b] == "on" and abs(self.rails[r].pts[-1][1] - v) > 1e-6:
                self.rails[r].go(t, v, SLEW_V_PER_S)
                if v < 1.0 and self.sys5v:
                    self.sim.violation("PMIC", f"Buck{b} set to {v*1e3:.0f} mV: SNVSAW2B says not under 1.0 V with VIN > 4 V")
                if not RAILS[r]["vmin"] <= v <= RAILS[r]["vmax"]:
                    self.sim.violation("PMIC", f"{r}_R setpoint {v*1e3:.0f} mV outside the radar's "
                                               f"{RAILS[r]['vmin']}-{RAILS[r]['vmax']} V")
        self._schedule_sup()

    def buck_on(self, b):
        return self.buck_state[b] == "on" and self.sys5v

    def buck_pg(self, b):
        r = BUCK_RAIL[b]
        v = vset_to_v(self.pmic.regs[0x0A + 2 * b])
        return self.buck_on(b) and abs(self.rails[r].v(self.sim.t) - v) < 0.040

    def pmic_powered(self):
        return self.sys5v or self.uvlo_ev is not None

    # ---------------------------------------------------------------- what the radar sees
    def rail_v(self, r, t=None):
        return self.rails[r].v(self.sim.t if t is None else t)

    def raw_v(self, r):
        """Buck output (what the supervisor watches for 1V0 / 1V8): radar-side value plus copper drop."""
        v = self.rail_v(r)
        if r in COPPER_DROP and v > 0.5:
            v += COPPER_DROP[r] * self.board.bss.current(r, self.sim.t)
        return v

    def rails_ok(self):
        return all(RAILS[r]["vmin"] - 1e-9 <= self.rail_v(r) <= RAILS[r]["vmax"] + 1e-9 for r in RAILS)

    def pmic_tj(self):
        return self.board.bss.pmic_tj()

    # ---------------------------------------------------------------- supervisor
    def _rail_thr(self, r, thr):
        """Supervisor threshold referred to the radar side of the rail (the buck output sits higher by
        the copper drop at the present load)."""
        if r in COPPER_DROP:
            thr -= COPPER_DROP[r] * self.board.bss.current(r, self.sim.t)
        return thr

    def _schedule_sup(self):
        """Find the next threshold crossing of any channel and evaluate there."""
        t = self.sim.t
        nxt = None
        for r, spec in RAILS.items():
            thr = self._rail_thr(r, spec["sup"] * (1 + SUP_HYST) if not self.sup_good[r] else spec["sup"])
            tc = self.rails[r].crossing(thr, not self.sup_good[r], t)
            if tc is not None and (nxt is None or tc < nxt):
                nxt = tc
        self._ev = [e for e in self._ev if e > t]
        if nxt is not None and nxt not in self._ev:
            self._ev.append(nxt)
            self.sim.at(nxt + 1e-9, self._evaluate_sup)
        self._evaluate_sup()

    def _evaluate_sup(self):
        t = self.sim.t
        changed = False
        for r, spec in RAILS.items():
            v = self.raw_v(r) if r in COPPER_DROP else self.rail_v(r)
            good = v >= spec["sup"] * (1 + SUP_HYST) if not self.sup_good[r] else v >= spec["sup"]
            if good != self.sup_good[r]:
                self.sup_good[r] = good
                changed = True
                if not good and not self.sup_low and self.sup_ev is None:
                    self.sup_ev = self.sim.after(SUP_PROP, self._force_sup_low, f"{r} under {spec['sup']} V")
        if all(self.sup_good.values()):
            if self.sup_low and self.release_ev is None:
                self.release_ev = self.sim.after(SUP_DELAY, self._sup_release)
        else:
            self.sim.cancel(self.release_ev)
            self.release_ev = None
        if changed:
            self._schedule_sup_later()

    def _schedule_sup_later(self):
        t = self.sim.t
        for r, spec in RAILS.items():
            thr = self._rail_thr(r, spec["sup"] * (1 + SUP_HYST) if not self.sup_good[r] else spec["sup"])
            tc = self.rails[r].crossing(thr, not self.sup_good[r], t + 1e-9)
            if tc is not None and tc not in self._ev:
                self._ev.append(tc)
                self.sim.at(tc + 1e-9, self._evaluate_sup)

    def _force_sup_low(self, why):
        self.sup_ev = None
        self.sim.cancel(self.release_ev)
        self.release_ev = None
        if not self.sup_low:
            self.sup_low = True
            self.note(f"supervisor asserts reset: {why}")
            self._update_nreset()

    def _sup_release(self):
        self.release_ev = None
        if all(self.sup_good.values()) and self.sys5v:
            self.sup_low = False
            self.note("supervisor releases reset")
            self._update_nreset()

    # ---------------------------------------------------------------- AOP_NRESET
    def _update_nreset(self):
        high = (not self.sup_low and not (self.gpio2_low and self.pmic_powered())
                and not any(self.low_sources.values()) and self.rail_v("3V3") > NRESET_VIH)
        if high != self.nreset:
            self.nreset = high
            self.note(f"AOP_NRESET {'high (radar runs)' if high else 'low (radar in reset)'}")
            self.board.nreset_changed(high)

    # ---------------------------------------------------------------- PMIC load measurement
    def start_load_measurement(self, buck):
        t0 = self.sim.t

        def done():
            r = BUCK_RAIL[buck]
            # average over the 50 us measurement window (SNVSAW2B 7.3.1.3)
            i = sum(self.board.bss.current(r, t0 + k * 1e-6) for k in range(50)) / 50 if self.buck_on(buck) else 0.0
            if r == "3V3":
                i += 0.012        # flash, pull-ups, CP2105-side leakage [estimate]
            code = min(1023, int(round(i / 0.020)))
            self.pmic.regs[0x26] = code >> 8
            self.pmic.regs[0x27] = code & 0xFF
            self.pmic.flag(0x1A, 0x01)
        self.sim.after(50e-6, done)
