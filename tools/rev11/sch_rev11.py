#!/usr/bin/env python3
"""Apply the Rev 1.1 schematic changes to the Rev 1.0 sheets.

Reads the Rev 1.0 sheets from git (tag/commit given as argv[1], default 38bd5c9) and
writes the Rev 1.1 sheets in place, so the edit is reproducible and reviewable.
The sheets keep their one-item-per-line layout; new parts are cloned from an
existing part of the same kind so every field (MPN, LCSC, footprint) is explicit.
"""
import re, subprocess, sys, uuid, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = sys.argv[1] if len(sys.argv) > 1 else "38bd5c9"
DATE = "2026-11-15"
_n = [11000]
_u = [0]


def new_uuid():
    """Deterministic UUIDs so a re-run keeps the PCB links."""
    _u[0] += 1
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"iwr-aop-module/rev1.1/{_u[0]}"))


def base(fn):
    return subprocess.run(["git", "show", f"{BASE}:{fn}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")


def uu(line):
    return re.sub(r'\(uuid "[0-9a-f-]+"\)', lambda m: f'(uuid "{new_uuid()}")', line)


def f(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def shift(line, dx, dy):
    def at(m):
        return f"(at {f(float(m.group(1)) + dx)} {f(float(m.group(2)) + dy)}{m.group(3)})"
    line = re.sub(r"\(at (-?[\d.]+) (-?[\d.]+)((?: -?[\d.]+)?)\)", at, line)
    line = re.sub(r"\(xy (-?[\d.]+) (-?[\d.]+)\)", lambda m: f"(xy {f(float(m.group(1)) + dx)} {f(float(m.group(2)) + dy)})", line)
    return line


def prop(line, name, val):
    """Set every occurrence of a property (the Rev 1.0 sheets carry an empty duplicate Datasheet)."""
    if f'(property "{name}"' not in line:
        # add as hidden property before the first (pin
        at = re.search(r"\(at [^)]+\)", line).group(0)
        new = f'(property "{name}" "{val}" {at} (effects (font (size 1.27 1.27)) (hide yes))) '
        i = line.index("(pin ") if "(pin " in line else line.index("(instances")
        return line[:i] + new + line[i:]
    return re.sub(rf'\(property "{name}" "[^"]*"', lambda m: f'(property "{name}" "{val}"', line)


def reref(line, old, new):
    return line.replace(f'"Reference" "{old}"', f'"Reference" "{new}"').replace(f'(reference "{old}")', f'(reference "{new}")')


class Sheet:
    def __init__(self, fn):
        self.fn = fn
        self.L = base(fn)
        self.end = next(i for i, l in enumerate(self.L) if l.startswith("  (sheet_instances"))
        self.new = []

    def find(self, ref):
        return next(l for l in self.L if f'"Reference" "{ref}"' in l)

    def idx(self, ref):
        return next(i for i, l in enumerate(self.L) if f'"Reference" "{ref}"' in l)

    def tmpl(self, start):
        return next(l for l in self.L if l.startswith(start))

    def add(self, line):
        self.new.append(line)

    # primitives -----------------------------------------------------------
    def wire(self, x1, y1, x2, y2):
        self.add(f'  (wire (pts (xy {f(x1)} {f(y1)}) (xy {f(x2)} {f(y2)})) (stroke (width 0) (type default)) (uuid "{new_uuid()}"))')

    def junction(self, x, y):
        self.add(f'  (junction (at {f(x)} {f(y)}) (diameter 0) (color 0 0 0 0) (uuid "{new_uuid()}"))')

    def nc(self, x, y):
        self.add(f'  (no_connect (at {f(x)} {f(y)}) (uuid "{new_uuid()}"))')

    def label(self, name, x, y, rot=0):
        j = "left" if rot in (0, 90) else "right"
        self.add(f'  (label "{name}" (at {f(x)} {f(y)} {rot}) (effects (font (size 1.27 1.27)) (justify {j})) (uuid "{new_uuid()}"))')

    def glabel(self, name, x, y, rot=0, shape="bidirectional"):
        j = "left" if rot in (0, 90) else "right"
        self.add(f'  (global_label "{name}" (shape {shape}) (at {f(x)} {f(y)} {rot}) (effects (font (size 1.27 1.27)) (justify {j})) '
                 f'(uuid "{new_uuid()}") (property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {f(x)} {f(y)} {rot}) (effects (font (size 1.27 1.27)) (hide yes))))')

    def text(self, s, x, y, bold=False, size=1.27):
        b = " (bold yes)" if bold else ""
        self.add(f'  (text "{s}" (at {f(x)} {f(y)} 0) (effects (font (size {size} {size}){b}) (justify left top)) (uuid "{new_uuid()}"))')

    def rect(self, x1, y1, x2, y2):
        self.add(f'  (rectangle (start {f(x1)} {f(y1)}) (end {f(x2)} {f(y2)}) (stroke (width .254) (type default)) (fill (type none)) (uuid "{new_uuid()}"))')

    def power(self, net, x, y):
        """Power symbol cloned from an existing one of the same net on this sheet."""
        lib = "power:GND" if net == "GND" else f"IWR-AOP-MODULE:PWR_{net}"
        t = next(l for l in self.L if f'(symbol (lib_id "{lib}")' in l and re.search(r"\(at [-\d.]+ [-\d.]+ 0\)", l))
        ox, oy = map(float, re.search(r"\(at ([-\d.]+) ([-\d.]+) 0\)", t).groups())
        old = re.search(r'"Reference" "(#PWR\d+)"', t).group(1)
        _n[0] += 1
        self.add(uu(reref(shift(t, x - ox, y - oy), old, f"#PWR{_n[0]}")))

    def clone(self, src, ref, x, y, **props):
        t = self.find(src)
        ox, oy = map(float, re.search(r"\(at ([-\d.]+) ([-\d.]+)", t).groups())
        l = uu(reref(shift(t, x - ox, y - oy), src, ref))
        for k, v in props.items():
            if k == "dnp":
                l = re.sub(r"\(dnp \w+\)", f"(dnp {v})", l)
            else:
                l = prop(l, k, v)
        self.add(l)

    def edit(self, ref, fn):
        i = self.idx(ref)
        self.L[i] = fn(self.L[i])

    def lib_symbol(self, text):
        i = next(i for i, l in enumerate(self.L) if l.startswith("  (lib_symbols"))
        assert self.L[i].endswith(")")
        self.L[i] = self.L[i][:-1] + " " + text + ")"

    # cells ---------------------------------------------------------------
    def cap(self, src, ref, x, y, net, **props):
        """Capacitor between a power net (symbol above) and GND (below)."""
        self.clone(src, ref, x, y, **props)
        self.wire(x, y - 3.81, x, y - 7.62); self.power(net, x, y - 7.62)
        self.wire(x, y + 3.81, x, y + 7.62); self.power("GND", x, y + 7.62)

    def tp(self, src, ref, net, x, y, desc):
        self.clone(src, ref, x, y, Value=net, Description=desc)
        self.wire(x, y, x, y + 7.62); self.glabel(net, x, y + 7.62, 180)

    def save(self):
        out = self.L[: self.end] + self.new + self.L[self.end:]
        txt = "\n".join(out)
        txt = re.sub(r'\(date "[^"]*"\) \(rev "1\.0"\)', f'(date "{DATE}") (rev "1.1")', txt)
        (ROOT / self.fn).write_text(txt)


def pin(kind, name, num, x, y, ang):
    return (f'(pin {kind} line (at {f(x)} {f(y)} {ang}) (length 2.54) (name "{name}" (effects (font (size 1.27 1.27)))) '
            f'(number "{num}" (effects (font (size 1.27 1.27)))))')


def hid(name, val):
    return f'(property "{name}" "{val}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))'


def sym_tps386000(prefix=""):
    n = "TPS386000RGPR"
    pins = [pin("input", f"SENSE{i}", p, -15.24, y, 0) for i, p, y in (("1", 10, 10.16), ("2", 9, 7.62), ("3", 8, 5.08), ("4L", 7, 2.54), ("4H", 6, 0))]
    pins += [pin("input", "~{MR}", 1, -15.24, -5.08, 0), pin("input", "WDI", 20, -15.24, -7.62, 0)]
    pins += [pin("open_collector", f"~{{RESET{i}}}", p, 15.24, y, 180) for i, p, y in ((1, 15, 10.16), (2, 16, 7.62), (3, 17, 5.08), (4, 18, 2.54))]
    pins += [pin("open_collector", "~{WDO}", 19, 15.24, -2.54, 180), pin("output", "VREF", 13, 15.24, -5.08, 180)]
    pins += [pin("passive", f"CT{i}", p, 15.24, y, 180) for i, p, y in ((1, 5, -7.62), (2, 4, -10.16), (3, 3, -12.7), (4, 2, -15.24))]
    pins += [pin("power_in", "VDD", 14, 0, 15.24, 270), pin("power_in", "GND", 12, -2.54, -20.32, 90),
             pin("passive", "NC", 11, 0, -20.32, 90), pin("passive", "EP", 21, 2.54, -20.32, 90)]
    return (f'(symbol "{prefix}{n}" (pin_names (offset 0.508)) (exclude_from_sim no) (in_bom yes) (on_board yes) '
            f'(property "Reference" "U" (at -12.7 13.97 0) (effects (font (size 1.27 1.27)) (justify left))) '
            f'(property "Value" "{n}" (at 2.54 13.97 0) (effects (font (size 1.27 1.27)) (justify left))) '
            f'(property "Footprint" "Package_DFN_QFN:TQFN-20-1EP_4x4mm_P0.5mm_EP2.1x2.1mm" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Datasheet" "https://www.ti.com/lit/ds/symlink/tps386000.pdf" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Description" "Quad supply voltage supervisor, 0.4 V adjustable thresholds (0.25 %), open-drain resets, 20 ms delay with CT open, VDD 1.8 to 6.5 V, 11 uA" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            + hid("MPN", n) + " " + hid("Manufacturer", "Texas Instruments") + " " + hid("LCSC", "C702221") + " "
            + f'(symbol "{n}_0_1" (rectangle (start -12.7 12.7) (end 12.7 -17.78) (stroke (width 0.254) (type default)) (fill (type background)))) '
            + f'(symbol "{n}_1_1" ' + " ".join(pins) + "))")


def sym_tvs(prefix=""):
    n = "SMF5.0A"
    g = ('(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none))) '
         '(polyline (pts (xy -1.27 1.27) (xy -1.778 1.778)) (stroke (width 0.254) (type default)) (fill (type none))) '
         '(polyline (pts (xy -1.27 -1.27) (xy -0.762 -1.778)) (stroke (width 0.254) (type default)) (fill (type none))) '
         '(polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none))) '
         '(polyline (pts (xy -1.27 0) (xy 1.27 0)) (stroke (width 0) (type default)) (fill (type none)))')
    return (f'(symbol "{prefix}{n}" (pin_numbers (hide yes)) (pin_names (offset 1.016) (hide yes)) (exclude_from_sim no) (in_bom yes) (on_board yes) '
            f'(property "Reference" "D" (at 0 2.54 0) (effects (font (size 1.27 1.27)))) '
            f'(property "Value" "{n}" (at 0 -2.54 0) (effects (font (size 1.27 1.27)))) '
            f'(property "Footprint" "Diode_SMD:D_SOD-123F" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Datasheet" "https://www.lcsc.com/product-detail/C193402.html" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(property "Description" "Unidirectional TVS, 5 V stand-off, 6.4 V min breakdown, 9.2 V clamp at 21.7 A, 200 W, SOD-123FL" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
            f'(symbol "{n}_0_1" {g}) '
            f'(symbol "{n}_1_1" {pin("passive", "K", 1, -3.81, 0, 0).replace("(length 2.54)", "(length 2.54)")} {pin("passive", "A", 2, 3.81, 0, 180)}))')


# ---------------------------------------------------------------- rails sheet
def rails():
    s = Sheet("rails.kicad_sch")
    s.L = [l.replace('(paper "A3")', '(paper "A2")') for l in s.L]

    # FB_B2 / FB_B3 sense at the buck output (TI IWR6843AOPEVM topology); ferrites outside the loop
    def swap(a, b):
        def fn(l):
            return l.replace(f'IWR-AOP-MODULE:PWR_{a}"', f'IWR-AOP-MODULE:PWR_{b}"').replace(f'"Value" "{a}"', f'"Value" "{b}"')
        return fn
    # FB_B2 / FB_B3: the power symbol on each feedback pin becomes a net label; the sense network
    # (R628/C631, R629/C632) is drawn in the supervisor block below.
    for pwr, fb in (("#PWR8028", "FB_B2"), ("#PWR8038", "FB_B3")):
        i = s.idx(pwr)
        x, y = re.search(r"\(at ([-\d.]+) ([-\d.]+) 0\)", s.L[i]).groups()
        s.L[i] = (f'  (label "{fb}" (at {x} {y} 90) (effects (font (size 1.27 1.27)) (justify left)) '
                  f'(uuid "{new_uuid()}"))')
    # I2C pull-ups to the radar I/O rail (radar VIH 2.25 V; PMIC SDA/SCL rated 6 V, VIH 1.2 V)
    s.edit("#PWR8045", swap("1V8_R", "3V3_R"))
    s.edit("#PWR8046", swap("1V8_R", "3V3_R"))
    # PGOOD is no longer a reset source by default
    s.edit("R604", lambda l: prop(l.replace("(dnp no)", "(dnp yes)"), "Description",
                                  "DNP option: fitted, PGOOD also asserts reset. Not fitted, reset is U601 + GPIO2."))
    # Ferrites: BLM18KG260TN1D (26 ohm, 6 A, 7 mohm max) in place of the 120 ohm part of the TI EVM. About a
    # quarter of the inductance: the filter still meets the 4 MHz ripple limit with margin, and the dip
    # a fast chirp current edge causes at the radar is roughly halved (simulation/run_loadstep.py).
    for r in ("FB600", "FB601"):
        def bead(l):
            l = l.replace('"Value" "120R@100MHz / 3A"', '"Value" "26R@100MHz / 6A"')
            for k, v in (("MPN", "BLM18KG260TN1D"), ("LCSC", "C88981"),
                         ("Datasheet", "https://www.murata.com/en-us/products/productdetail?partno=BLM18KG260TN1%23"),
                         ("Description", "Second-stage ferrite, 26 ohm / 6 A / 7 mohm. Low inductance keeps the chirp load-step dip small; "
                                         "its DC drop is regulated out by the remote sense (R628/R629) and the loop sees the buck output through C631/C632.")):
                l = prop(l, k, v)
            assert "26R@100MHz" in l
            return l
        s.edit(r, bead)

    def retext(old, new):
        i = next(i for i, l in enumerate(s.L) if l.startswith("  (text") and old in l)
        s.L[i] = re.sub(r'\(text "[^"]*"', lambda m: f'(text "{new}"', s.L[i])
    retext("Kelvin sense after ferrite", "4 MHz; short VIN/PGND loops.\\nFB2/3: DC sense at the radar (R628/R629),\\nAC sense at the buck output (C631/C632).")
    retext("RC fallback", "R604 DNP: PGOOD is observed (TP613), not a reset source.\\nC605 DNP, footprint kept.\\nR600/601: I2C pull-ups to 3V3_R; bus master is the radar (R605/R606).")
    retext("open-drain reset sinks", "Reset sinks on AOP_NRESET (all open-drain):\\nU601 supervisor (all four rails), PMIC GPIO2 (falls with EN1),\\nSW400, J301.10, carrier through R417.")

    # extra bulk at the buck outputs: 2 x 22 uF local on each rail
    d = "Buck output bulk at the PMIC, inside the regulation loop (2 x 22 uF local)."
    s.cap("C624", "C626", 156.21, 167.64, "1V0_RAW", Description=d)
    s.cap("C624", "C628", 186.69, 213.36, "1V8_RAW", Description=d)
    # radar-side bulk after the ferrites: 2 x 22 uF more, as on the TI EVM (22 + 22 + 10 uF behind each bead).
    # They sit on the front on the wide 1V0_R feed, 9 mm and more from the radar package.
    d = "1.0 V bulk on the radar side of the ferrites; lowers the ferrite/capacitor impedance that sets the chirp load-step dip."
    s.cap("C624", "C630", 186.69, 167.64, "1V0_R", Description=d)
    s.cap("C624", "C633", 217.17, 167.64, "1V0_R", Description=d)
    # second 1.0 V ferrite in parallel: halves DC drop and bias per bead
    s.clone("FB600", "FB602", 204.47, 180.34, Description="In parallel with FB600: 2 mohm total, 1.25 A per bead at the 2.5 A 3-TX peak.")
    s.label("1V0_RAW", 200.66, 180.34, 180); s.label("1V0_R", 208.28, 180.34, 0)

    # test pads
    s.tp("TP603", "TP613", "PMIC_PGOOD", 372.11, 77.47, "Bare service pad; PMIC power-good, open drain, pulled up only if R604 is fitted.")
    # ---- supervisor block
    s.rect(96.52, 292.1, 408.94, 381.0)
    s.text("SUPERVISOR / PMIC I2C", 99.06, 294.64, bold=True, size=1.778)
    s.text("U601 holds AOP_NRESET low until all four rails are up, for 20 ms (CT open),\\n"
           "and pulls it low when a rail falls below its threshold (0.400 V at SENSE).\\n"
           "Thresholds: 3V3_R 3.07 V, 1V8_RAW 1.65 V, 1V2_R 1.12 V, 1V0_RAW 0.90 V.\\n"
           "1.0 V and 1.8 V are sensed at the buck output so chirp dips behind the ferrites\\n"
           "cannot reset the radar. U601 runs from SYS_5V and holds reset down to 0.9 V.", 300.99, 345.44)
    div = [("3V3_R", "SUP_3V3", "R620", "100kR", "R621"), ("1V8_RAW", "SUP_1V8", "R622", "47kR", "R623"),
           ("1V2_R", "SUP_1V2", "R624", "27kR", "R625"), ("1V0_RAW", "SUP_1V0", "R626", "15kR", "R627")]
    part = {"100kR": ("0402WGF1003TCE", "C25741"), "47kR": ("0402WGF4702TCE", "C25792"), "27kR": ("0402WGF2702TCE", "C25771"),
            "15kR": ("0402WGF1502TCE", "C25756"), "12kR": ("0402WGF1202TCE", "C25752"), "0R": ("0402WGF0000TCE", "C17168")}

    def res(ref, val, x, y, desc):
        mpn, lcsc = part[val]
        s.clone("R603", ref, x, y, Value=val, MPN=mpn, LCSC=lcsc, Description=desc,
                Datasheet=f"https://www.lcsc.com/product-detail/{lcsc}.html")
    for i, (net, lab, rt, vt, rb) in enumerate(div):
        x = 110.49 + 25.4 * i
        vb = "12kR" if net == "1V0_RAW" else "15kR"
        s.power(net, x, 320.04); s.wire(x, 320.04, x, 323.85)
        res(rt, vt, x, 326.39, f"U601 threshold divider, top, {net}; 1 %")
        s.wire(x, 328.93, x, 334.01); s.junction(x, 331.47); s.wire(x, 331.47, x + 5.08, 331.47); s.label(lab, x + 5.08, 331.47, 0)
        res(rb, vb, x, 336.55, f"U601 threshold divider, bottom, {net}; 1 %")
        s.wire(x, 339.09, x, 342.9); s.power("GND", x, 342.9)

    # ---- remote sense networks: DC from the radar end, AC from the buck output
    part["22R"] = ("0402WGF220JTCE", "C25092")
    s.text("FB_B2/FB_B3 REMOTE SENSE: DC through 22R from the radar balls (IR drop of ferrite and\\n"
           "copper is regulated out), AC through 1 uF from the buck output (loop does not see the\\n"
           "ferrite resonance). Crossover 7 kHz. Fallback to the TI EVM local sense: remove\\n"
           "R628/R629 and fit 0R in place of C631/C632.", 320.04, 296.0)
    for i, (rail, fb, rr, cc) in enumerate((("1V0", "FB_B2", "R628", "C631"), ("1V8", "FB_B3", "R629", "C632"))):
        x = 335.28 + 35.56 * i
        s.power(f"{rail}_R", x, 311.15); s.wire(x, 311.15, x, 313.69)
        res(rr, "22R", x, 316.23, f"{fb} DC sense from the radar end of {rail}_R (Kelvin tap at the radar balls); 1 %")
        s.wire(x, 318.77, x, 323.85); s.junction(x, 321.31); s.wire(x, 321.31, x + 5.08, 321.31); s.label(fb, x + 5.08, 321.31, 0)
        s.clone("C605", cc, x, 327.66, dnp="no", Value="1uF", MPN="CL10A105KB8NNNC", LCSC="C15849", Rating="X5R 50V",
                Description=f"{fb} AC sense from the buck output {rail}_RAW; with {rr}: 22 us")
        s.wire(x, 331.47, x, 334.01); s.wire(x, 334.01, x + 10.16, 334.01); s.wire(x + 10.16, 334.01, x + 10.16, 331.47)
        s.power(f"{rail}_RAW", x + 10.16, 331.47)

    X, Y = 251.46, 340.36
    s.lib_symbol(sym_tps386000("IWR-AOP-MODULE:"))
    path = re.search(r'\(path "([^"]+)"', s.find("R603")).group(1)
    pinu = " ".join(f'(pin "{p}" (uuid "{new_uuid()}"))' for p in range(1, 22))
    s.add(f'  (symbol (lib_id "IWR-AOP-MODULE:TPS386000RGPR") (at {f(X)} {f(Y)} 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid "{new_uuid()}") '
          f'(property "Reference" "U601" (at {f(X - 12.7)} {f(Y - 13.97)} 0) (effects (font (size 1.27 1.27)) (justify left))) '
          f'(property "Value" "TPS386000RGPR" (at {f(X + 2.54)} {f(Y - 13.97)} 0) (effects (font (size 1.27 1.27)) (justify left))) '
          f'(property "Footprint" "Package_DFN_QFN:TQFN-20-1EP_4x4mm_P0.5mm_EP2.1x2.1mm" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Datasheet" "https://www.ti.com/lit/ds/symlink/tps386000.pdf" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Description" "Quad supply voltage supervisor; all four open-drain resets wired to AOP_NRESET" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "MPN" "TPS386000RGPR" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Manufacturer" "Texas Instruments" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "LCSC" "C702221" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'{pinu} (instances (project "IWR-AOP-MODULE" (path "{path}" (reference "U601") (unit 1)))))')
    for lab, dy in (("SUP_3V3", -10.16), ("SUP_1V8", -7.62), ("SUP_1V2", -5.08), ("SUP_1V0", -2.54)):
        s.wire(X - 15.24, Y + dy, X - 20.32, Y + dy); s.label(lab, X - 20.32, Y + dy, 180)
    # SENSE4H unused -> GND
    s.wire(X - 15.24, Y, X - 34.29, Y); s.wire(X - 34.29, Y, X - 34.29, Y + 2.54); s.power("GND", X - 34.29, Y + 2.54)
    # MR -> VDD (no internal pull-up)
    s.wire(X - 15.24, Y + 5.08, X - 17.78, Y + 5.08); s.glabel("SYS_5V", X - 17.78, Y + 5.08, 180)
    # WDI -> GND, watchdog unused
    s.wire(X - 15.24, Y + 7.62, X - 17.78, Y + 7.62); s.wire(X - 17.78, Y + 7.62, X - 17.78, Y + 10.16); s.power("GND", X - 17.78, Y + 10.16)
    for dy in (-10.16, -7.62, -5.08, -2.54):
        s.wire(X + 15.24, Y + dy, X + 20.32, Y + dy)
    s.wire(X + 20.32, Y - 10.16, X + 20.32, Y - 2.54)
    for dy in (-7.62, -5.08):
        s.junction(X + 20.32, Y + dy)
    s.junction(X + 20.32, Y - 10.16)
    s.wire(X + 20.32, Y - 10.16, X + 25.4, Y - 10.16); s.glabel("AOP_NRESET", X + 25.4, Y - 10.16, 0)
    for dy in (2.54, 5.08, 7.62, 10.16, 12.7, 15.24):
        s.nc(X + 15.24, Y + dy)
    s.wire(X, Y - 15.24, X, Y - 20.32); s.glabel("SYS_5V", X, Y - 20.32, 90)
    for dx in (-2.54, 0, 2.54):
        s.wire(X + dx, Y + 20.32, X + dx, Y + 22.86)
    s.wire(X - 2.54, Y + 22.86, X + 2.54, Y + 22.86); s.junction(X, Y + 22.86); s.power("GND", X, Y + 22.86)
    # U601 decoupling
    s.clone("C604", "C606", 290.83, 327.66, Description="U601 VDD decoupling")
    s.wire(290.83, 323.85, 290.83, 320.04); s.glabel("SYS_5V", 290.83, 320.04, 90)
    s.wire(290.83, 331.47, 290.83, 335.28); s.power("GND", 290.83, 335.28)

    # I2C: radar (G3 SCL, G1 SDA) -> PMIC, series 0R links as on the TI EVM
    for ref, a, b, x in (("R605", "AOP_I2C_SCL", "PMIC_SCL", 325.12), ("R606", "AOP_I2C_SDA", "PMIC_SDA", 350.52)):
        res(ref, "0R", x, 313.69, "Radar I2C to PMIC link (TI EVM R28/R32). Remove to isolate the PMIC bus.")
        s.wire(x, 311.15, x, 307.34); s.glabel(a, x, 307.34, 90)
        s.wire(x, 316.23, x, 320.04); s.glabel(b, x, 320.04, 180)
    s.tp("TP603", "TP614", "PMIC_SCL", 374.65, 309.88, "Bare service pad; PMIC I2C clock, 3.3 V logic.")
    s.tp("TP603", "TP615", "PMIC_SDA", 396.24, 309.88, "Bare service pad; PMIC I2C data, 3.3 V logic.")
    s.save()


# ---------------------------------------------------------------- radar sheet
def aop():
    s = Sheet("aop.kicad_sch")
    for y, net in ((33.02, "AOP_I2C_SCL"), (35.56, "AOP_I2C_SDA")):
        i = next(i for i, l in enumerate(s.L) if l.startswith(f"  (no_connect (at 316.23 {f(y)})"))
        del s.L[i]
        s.end -= 1
        s.wire(316.23, y, 311.15, y); s.glabel(net, 311.15, y, 180)
    s.save()


# ---------------------------------------------------------------- interfaces sheet
def interfaces():
    s = Sheet("interfaces.kicad_sch")
    s.lib_symbol(sym_tvs("IWR-AOP-MODULE:"))
    # hot-plug damper: 10 uF in series with 0.47 ohm from USB_VBUS to GND
    x, y = 250.19, 110.49
    s.clone("C202", "C204", x, y, Value="10uF 50V", Footprint="Capacitor_SMD:C_1206_3216Metric", MPN="CL31A106KBHNNNE", LCSC="C13585",
            Manufacturer="Samsung Electro-Mechanics", Datasheet="https://www.lcsc.com/product-detail/C13585.html",
            Description="USB hot-plug damper with R210: damps the cable-inductance / input-capacitor ring. 50 V part keeps about 7 uF at 5 V.")
    s.wire(x, y - 3.81, x, y - 7.62); s.power("USB_VBUS", x, y - 7.62)
    s.wire(x, y + 3.81, x, y + 6.35)
    s.clone("R200", "R210", x, y + 8.89, Value="0.47R", Footprint="Resistor_SMD:R_0603_1608Metric", MPN="0603WAF470LT5E", LCSC="C23411",
            Datasheet="https://www.lcsc.com/product-detail/C23411.html", Description="USB hot-plug damper resistor in series with C204")
    s.wire(x, y + 11.43, x, y + 15.24); s.power("GND", x, y + 15.24)
    # TVS
    x2 = 275.59
    path = re.search(r'\(path "([^"]+)"', s.find("C202")).group(1)
    yd = 110.49
    s.add(f'  (symbol (lib_id "IWR-AOP-MODULE:SMF5.0A") (at {f(x2)} {f(yd)} 270) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid "{new_uuid()}") '
          f'(property "Reference" "D202" (at {f(x2 + 3.81)} {f(yd - 1.27)} 0) (effects (font (size 1.27 1.27)) (justify left))) '
          f'(property "Value" "SMF5.0A" (at {f(x2 + 3.81)} {f(yd + 1.27)} 0) (effects (font (size 1.27 1.27)) (justify left))) '
          f'(property "Footprint" "Diode_SMD:D_SOD-123F" (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Datasheet" "https://www.lcsc.com/product-detail/C193402.html" (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Description" "USB_VBUS surge/ESD clamp. Second line only: the C204/R210 damper is what keeps hot-plug ringing under the 6 V rating of U117." (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "MPN" "SMF5.0A" (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "Manufacturer" "ElecSuper" (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(property "LCSC" "C193402" (at {f(x2)} {f(yd)} 0) (effects (font (size 1.27 1.27)) (hide yes))) '
          f'(pin "1" (uuid "{new_uuid()}")) (pin "2" (uuid "{new_uuid()}")) (instances (project "IWR-AOP-MODULE" (path "{path}" (reference "D202") (unit 1)))))')
    # rotation 270: pin 1 (K, lib x=-3.81) ends up at the top
    s.wire(x2, yd - 3.81, x2, yd - 7.62); s.power("USB_VBUS", x2, yd - 7.62)
    s.wire(x2, yd + 3.81, x2, yd + 7.62); s.power("GND", x2, yd + 7.62)
    s.text("Hot-plug damper (C204 + R210) and TVS on USB_VBUS.\\nSimulated peak 5.4 V or less for 0.3 to 2 m cables\\n(8.0 V without).", 240.03, 132.08)
    s.save()


def root():
    s = Sheet("IWR-AOP-MODULE.kicad_sch")
    s.save()


if __name__ == "__main__":
    rails(); aop(); interfaces(); root()
    # project symbol library
    libf = ROOT / "libraries/IWR-AOP-MODULE.kicad_sym"
    t = subprocess.run(["git", "show", f"{BASE}:libraries/IWR-AOP-MODULE.kicad_sym"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    t = t.rstrip()
    assert t.endswith(")")
    libf.write_text(t[:-1] + "\t" + sym_tps386000() + "\n\t" + sym_tvs() + "\n)\n")
    print("ok")
