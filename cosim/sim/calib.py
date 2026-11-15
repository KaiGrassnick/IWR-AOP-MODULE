"""Board numbers the co-simulation takes from the existing ngspice and thermal results
(simulation/results), so the firmware sees the timing those models computed. If a summary is
missing the fallback values below (copied from the Rev 1.1 results) are used and reported."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
RES = ROOT / "simulation" / "results"

FALLBACK = {
    "en1_after_5v": 2.416e-3,       # [A usb] EN1 high
    "ready_offset": 1.439e-3,       # first-start OTP read + EN debounce (fit below)
    "rail_90": {"1V8": 4.281e-3, "1V0": 6.091e-3, "1V2": 9.138e-3, "3V3": 9.637e-3},
    "settle_after_last90": 0.069e-3,
    "sup_delay": 19.97e-3,
    "unplug_nreset": 166.8e-6,
    "unplug_out": {"3V3": 150e-6, "1V2": 394e-6, "1V0": 466e-6, "1V8": 452e-6},
    "disable_nreset": 12.2e-6,
    "disable_out": {"3V3": 352e-6, "1V2": 31e-6, "1V0": 105e-6, "1V8": 1089e-6},
    "rth_hs": 27.0,                 # K/W radar junction rise per watt, with HS300
    "kp": 37.9,
}


def _f(pattern, text, scale=1e-3):
    m = re.search(pattern, text)
    return float(m.group(1)) * scale if m else None


def load():
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in FALLBACK.items()}
    c["source"] = "fallback (simulation/results not found)"
    p = RES / "power_summary.txt"
    if p.exists():
        s = p.read_text()
        en1 = _f(r"\[A usb\] EN1 high at\s+([\d.]+) ms", s)
        r90 = {r: _f(rf"\[A usb\]\s+{r}_R\s+reaches 90 % at\s+([\d.]+) ms", s) for r in ("1V8", "1V0", "1V2", "3V3")}
        inside = _f(r"\[A usb\] all rails inside window at\s+([\d.]+) ms", s)
        rel = _f(r"\[A usb\] AOP_NRESET released \(>1.57 V\) at\s+[\d.]+ ms\s+=\s+([\d.]+) ms after rails settled", s)
        unp = _f(r"\[A usb unplug\] AOP_NRESET below 0.65 V at\s+[\d.]+ ms \(([\d.]+) us after event\)", s, 1e-6)
        dis = _f(r"\[B HOST_DISABLE\] AOP_NRESET below 0.65 V at\s+[\d.]+ ms \(([\d.]+) us after event\)", s, 1e-6)
        if None not in (en1, inside, rel, unp, dis) and None not in r90.values():
            c["en1_after_5v"] = en1
            c["rail_90"] = r90
            c["settle_after_last90"] = inside - max(r90.values())
            c["sup_delay"] = rel
            c["unplug_nreset"] = unp
            c["disable_nreset"] = dis
            # 1V8 starts with no OTP delay and ramps at 3.8 mV/us: what is left is the start offset
            c["ready_offset"] = (r90["1V8"] - en1) - 0.9 * 1.8 / 3.8e3
            for tag, key in (("A usb unplug", "unplug_out"), ("B HOST_DISABLE", "disable_out")):
                t0 = 80e-3 if tag.startswith("A") else 40e-3
                for r in ("3V3", "1V2", "1V0", "1V8"):
                    v = _f(rf"\[{tag}\]\s+{r}_R\s+below datasheet min \([\d.]+ V\) at\s+([\d.]+) ms", s)
                    if v is not None:
                        c[key][r] = v - t0
            c["source"] = "simulation/results/power_summary.txt"
    p = RES / "thermal_summary.txt"
    if p.exists():
        s = p.read_text().split("With heat sink")[-1]
        pts = [(float(w), float(k)) for w, k in re.findall(r"\(([\d.]+) W[^)]*\)\s+[\d.]+ K\s+([\d.]+) K", s)]
        if pts:
            c["rth_hs"] = sum(k for w, k in pts) / sum(w for w, k in pts)
    p = RES / "loadstep_summary.txt"
    if p.exists():
        kp = _f(r"kp = ([\d.]+) A/V", p.read_text(), 1)
        if kp:
            c["kp"] = kp
    return c


if __name__ == "__main__":
    import json
    print(json.dumps(load(), indent=1))
