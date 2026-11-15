#!/usr/bin/env python3
"""Rev 1.1 board edits, applied to the Rev 1.0 board (git 38bd5c9).

Usage:  python3 tools/rev11/pcb_rev11.py [--stage N]

Reads the Rev 1.1 netlist exported from the schematic (kicad-cli), adds the new
footprints, re-assigns pad nets, removes the Rev 1.0 sense / pull-up copper and
routes the new connections with tools/rev11/router.py.  KiCad DRC with
schematic parity is the acceptance check, not this script.
"""
import os
import re
import subprocess
import sys
import tempfile

import pcbnew

sys.path.insert(0, os.path.dirname(__file__))
import router as R  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PCB = os.path.join(ROOT, "IWR-AOP-MODULE.kicad_pcb")
SCH = os.path.join(ROOT, "IWR-AOP-MODULE.kicad_sch")
BASE = "38bd5c9"
MM, NM = pcbnew.ToMM, pcbnew.FromMM
FPLIB = "/usr/share/kicad/footprints"


# ------------------------------------------------------------------ netlist
def sexpr(text):
    tok = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    pos = 0

    def parse():
        nonlocal pos
        out = []
        while pos < len(tok):
            t = tok[pos]
            pos += 1
            if t == "(":
                out.append(parse())
            elif t == ")":
                return out
            else:
                out.append(t[1:-1].replace('\\"', '"') if t.startswith('"') else t)
        return out
    return parse()[0]


def find(node, key):
    return [n for n in node if isinstance(n, list) and n and n[0] == key]


def one(node, key, default=None):
    f = find(node, key)
    return f[0][1] if f and len(f[0]) > 1 else default


def read_netlist():
    tmp = tempfile.NamedTemporaryFile(suffix=".net", delete=False).name
    subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", tmp, SCH],
                   check=True, capture_output=True)
    tree = sexpr(open(tmp).read())
    os.unlink(tmp)
    comps = {}
    for c in find(find(tree, "components")[0], "comp"):
        sp = find(c, "sheetpath")[0]
        props = {one(p, "name"): one(p, "value", "") for p in find(c, "property")}
        fields = {}
        for f in find(find(c, "fields")[0], "field") if find(c, "fields") else []:
            fields[one(f, "name")] = f[2] if len(f) > 2 else ""
        comps[one(c, "ref")] = dict(
            value=one(c, "value"), footprint=one(c, "footprint"), datasheet=one(c, "datasheet", ""),
            description=one(c, "description", ""), fields=fields, props=props,
            path=one(sp, "tstamps") + one(c, "tstamps"), sheetname=one(sp, "names"),
            sheetfile=props.get("Sheetfile", ""), dnp="dnp" in props)
    nets = {}
    for n in find(find(tree, "nets")[0], "net"):
        for nd in find(n, "node"):
            nets[(one(nd, "ref"), one(nd, "pin"))] = one(n, "name")
    return comps, nets


# ------------------------------------------------------------------ board helpers
def net(b, name):
    ni = b.FindNet(name)
    if ni is None:
        ni = pcbnew.NETINFO_ITEM(b, name)
        b.Add(ni)
    return ni


def add_fp(b, comps, ref, x, y, rot=0, back=False):
    c = comps[ref]
    lib, name = c["footprint"].split(":")
    f = pcbnew.FootprintLoad(os.path.join(FPLIB, lib + ".pretty"), name)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    f.SetReference(ref)
    f.SetValue(c["value"])
    b.Add(f)
    f.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y)))
    if back:
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    f.SetOrientationDegrees(rot)
    f.SetPath(pcbnew.KIID_PATH(c["path"]))
    f.SetSheetname(c["sheetname"])
    f.SetSheetfile(c["sheetfile"])
    f.GetField(pcbnew.FIELD_T_DATASHEET).SetText(c["datasheet"])
    f.GetField(pcbnew.FIELD_T_DESCRIPTION).SetText(c["description"])
    for k in ("MPN", "Manufacturer", "LCSC", "Rating", "Tolerance"):
        if k in c["fields"]:
            f.SetField(k, c["fields"][k])
    # like every other part on this board: no silk reference, fields hidden on the fab layer
    for fld in f.GetFields():
        fld.SetVisible(False)
        if fld.GetName() != "Reference":
            fld.SetLayer(pcbnew.B_Fab if back else pcbnew.F_Fab)
    return f


def sync_nets(b, comps, nets):
    for f in b.GetFootprints():
        ref = f.GetReference()
        if ref not in comps:
            continue
        for p in f.Pads():
            n = nets.get((ref, p.GetNumber()))
            if n is not None and p.GetNetname() != n:
                p.SetNet(net(b, n))
        f.SetDNP(comps[ref]["dnp"])
        d = f.GetField(pcbnew.FIELD_T_DESCRIPTION)
        if d.GetText() != comps[ref]["description"]:
            d.SetText(comps[ref]["description"])
        if f.GetValue() != comps[ref]["value"]:
            f.SetValue(comps[ref]["value"])


def near(a, bpt, tol=0.02):
    return abs(a[0] - bpt[0]) < tol and abs(a[1] - bpt[1]) < tol


def tracks_at(b, netname, pts, layer=None):
    """Tracks / vias of `netname` with an end on any of pts."""
    out = []
    for t in b.GetTracks():
        if t.GetNetname() != netname:
            continue
        if layer and t.GetClass() != "PCB_VIA" and b.GetLayerName(t.GetLayer()) != layer:
            continue
        ends = [(MM(t.GetStart().x), MM(t.GetStart().y)), (MM(t.GetEnd().x), MM(t.GetEnd().y))]
        if any(near(e, p) for e in ends for p in pts):
            out.append(t)
    return out


_removed = []          # keep the wrappers alive; SWIG breaks board iteration otherwise


def remove(b, items):
    for t in items:
        b.Remove(t)
        _removed.append(t)


def pad(b, ref, num):
    for p in b.FindFootprintByReference(ref).Pads():
        if p.GetNumber() == num:
            return p
    raise KeyError((ref, num))


def padpt(b, ref, num):
    p = pad(b, ref, num)
    layer = "B.Cu" if p.IsOnLayer(pcbnew.B_Cu) and not p.IsOnLayer(pcbnew.F_Cu) else "F.Cu"
    return (layer, round(MM(p.GetPosition().x), 4), round(MM(p.GetPosition().y), 4))


class Router:
    def __init__(self, b):
        self.b = b
        self.log = []
        self.failed = []

    def route(self, netname, start, goal, window, width=0.2, slop=None, **kw):
        w = R.Window(self.b, *window)
        if slop:
            w.slop = slop
        w.build(netname)
        try:
            segs, vias = w.route(start, goal, width=width, **kw)
        except RuntimeError as e:
            self.failed.append(str(e))
            print("FAILED:", e, flush=True)
            return [], []
        via = kw.get("via", (0.45, 0.3))
        for layer, pts in segs:
            R.add_track(self.b, netname, layer, pts, width)
        for x, y in vias:
            R.add_via(self.b, netname, x, y, *via)
        n = sum(len(p) - 1 for _, p in segs)
        self.log.append(f"{netname}: {n} segments, {len(vias)} vias, layers {[l for l, _ in segs]}")
        print(self.log[-1], flush=True)
        return segs, vias

    def route_bga(self, netname, start, goal, window, **kw):
        """0.1 mm inside the U300 courtyard, 0.15 mm outside: find the path once, split it at the first
        vertex clear of the courtyard, then route the two parts for real."""
        R.RES, R.STEP = 0.0125, 4          # finer raster: the escape gaps are 0.35 mm
        try:
            return self._route_bga(netname, start, goal, window, **kw)
        finally:
            R.RES, R.STEP = 0.025, 2

    def _route_bga(self, netname, start, goal, window, **kw):
        w = R.Window(self.b, *window)
        w.slop = 0.015
        w.build(netname)
        try:
            segs, _ = w.route(start, goal, width=0.1, **kw)
        except RuntimeError as e:
            self.failed.append(str(e))
            print("FAILED:", e, flush=True)
            return
        x0, y0, x1, y1 = w.bga
        split = None
        inside = lambda x, y: x0 - 0.1 < x < x1 + 0.1 and y0 - 0.1 < y < y1 + 0.1
        sgn = lambda v: (v > 1e-9) - (v < -1e-9)
        for layer, pts in segs:
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                if inside(ax, ay) and not inside(bx, by):
                    k = 0.0
                    while inside(ax + sgn(bx - ax) * k, ay + sgn(by - ay) * k):
                        k += 0.05
                    split = (layer, round(ax + sgn(bx - ax) * k, 4), round(ay + sgn(by - ay) * k, 4))
                    break
            if split:
                break
        self.route(netname, start, split, window, width=0.1, slop=0.015, **kw)
        self.route(netname, split, goal, window, width=0.15, **({"edge": kw["edge"]} if "edge" in kw else {}))

    def via_near(self, netname, layer, x, y, window_r=3.0, via=(0.45, 0.3), width=0.3, maxd=2.5, avoid_existing=0.0, avoid_rects=(), optional=False):
        """Drop a via for `netname` as close as possible to (x, y) and join it on `layer`."""
        w = R.Window(self.b, x - window_r, y - window_r, x + window_r, y + window_r)
        w.build(netname)
        ok = w.via_ok(*via)
        import numpy as np
        rr, cc = np.nonzero(ok)
        X = w.x0 + cc * R.RES
        Y = w.y0 + rr * R.RES
        d = np.hypot(X - x, Y - y)
        if avoid_existing:      # keep away from same-net vias already there (they are not obstacles)
            for t in self.b.GetTracks():
                if t.GetClass() == "PCB_VIA" and t.GetNetname() == netname:
                    d = np.where(np.hypot(X - MM(t.GetPosition().x), Y - MM(t.GetPosition().y)) < avoid_existing, 1e9, d)
        for (ax0, ay0, ax1, ay1) in avoid_rects:   # e.g. paste apertures, which the raster does not see
            d = np.where((X > ax0) & (X < ax1) & (Y > ay0) & (Y < ay1), 1e9, d)
        i = int(np.argmin(d))
        if d[i] > maxd:
            if optional:
                print(f"{netname}: no stitching via site near {x},{y}, skipped", flush=True)
                return None
            self.failed.append(f"no via site for {netname} near {x},{y}")
            print("FAILED:", self.failed[-1], flush=True)
            return None
        vx, vy = round(float(X[i]), 3), round(float(Y[i]), 3)
        R.add_via(self.b, netname, vx, vy, *via)
        if layer and (vx, vy) != (x, y):
            R.add_track(self.b, netname, layer, [(x, y), (vx, vy)], width)
        print(f"{netname}: via at {vx},{vy} ({d[i]:.2f} mm from pad)", flush=True)
        return vx, vy


# ------------------------------------------------------------------ the edits
def stage1(b, comps, nets, rt):
    """Net re-assignment and the two feedback sense lines + pull-up supply."""
    # old FB_B2 sense: 1V0_R trunk -> In3 -> via -> U600.1
    remove(b, tracks_at(b, "1V0_R", [(174.675, 115.225), (174.345, 115.72), (174.345, 115.58), (174.345, 115.555),
                                     (174.275, 115.65)]))
    remove(b, tracks_at(b, "1V0_R", [(174.675, 113.5)], "In3.Cu"))
    # old FB_B3 sense: 1V8_R at FB601 -> In3 -> via (170.72,115.57) -> U600.21, and the F.Cu feed to the I2C pull-ups
    remove(b, tracks_at(b, "1V8_R", [(170.72, 115.57), (170.72, 115.6), (168.925, 116.725), (165.615, 116.725)]))
    remove(b, [t for t in tracks_at(b, "1V8_R", [(164.765, 117.575)], "In3.Cu")
               if abs(MM(t.GetEnd().x) - 165.615) < 0.02 or abs(MM(t.GetStart().x) - 165.615) < 0.02])
    remove(b, tracks_at(b, "1V8_R", [(174.325, 116.25), (175.625, 117.55), (177.7, 117.55), (177.725, 117.835),
                                     (177.725, 117.575)]))
    # R601 (SDA pull-up) had the only access to PMIC pin 6 walled in; it moves to the front beside the
    # bus pads, where a 3V3_R via already exists.
    remove(b, tracks_at(b, "PMIC_SDA", [(175.873, 117.835), (175.383, 118.325)]))
    f = b.FindFootprintByReference("R601")
    f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    f.SetPosition(pcbnew.VECTOR2I(NM(160.45), NM(126.25)))
    f.SetOrientationDegrees(90)
    if MM(pad(b, "R601", "1").GetPosition().y) < 126.25:      # pad 1 (3V3_R) sits on the existing 3V3_R via
        f.SetOrientationDegrees(270)
    sync_nets(b, comps, nets)
    win = (160, 100, 186, 124)
    rt.route("3V3_R", padpt(b, "R600", "1"), "net", win, width=0.3)


# ref: (x, y, rotation).  Everything new sits on the front, at least 9 mm from the radar package
# (TP614/TP615/R605/R606 and the VBUS parts excepted: flat pads and 0402s 3.5 mm away, like TP605-607).
PLACE = {
    # second 1.0 V ferrite, mirror image of FB600 on the other side of the board
    "FB602": (172.325, 105.7, 90),
    # buck output bulk on the front, over the inductor output pads
    "C626": (175.45, 107.675, 90), "C628": (168.25, 107.675, 90),
    # radar-side 1.0 V bulk on the wide feed, 12 mm from the radar package
    "C630": (172.4, 109.2, 90), "C633": (170.3, 109.2, 90),
    # supervisor, VDD cap and the four dividers
    "U601": (167.6, 125.35, 90), "C606": (167.6, 122.25, 0),
    "R621": (170.75, 123.2, 90), "R620": (171.7, 123.2, 90),      # 3V3_R  -> SENSE1 (pin 10)
    "R623": (172.75, 123.2, 90), "R622": (173.7, 123.2, 90),      # 1V8_RAW -> SENSE2 (pin 9)
    "R625": (173.76, 126.45, 0), "R624": (173.76, 127.4, 0),      # 1V2_R  -> SENSE3 (pin 8)
    "R626": (171.2, 126.45, 0), "R627": (171.2, 127.4, 0),      # 1V0_RAW -> SENSE4L (pin 7)
    # service pads
    "TP613": (170.6, 116.9, 0),
    # remote sense: AC coupling caps on the front beside the buck output pads, DC sense resistors at the
    # radar end (R628 on the back between the radar decoupling caps, R629 beside the 1V8_R test pad)
    "C631": (174.2, 111.7, 0), "C632": (168.5, 110.9, 90),
    "R628": (153.58, 113.9, 0, "B"), "R629": (161.3, 117.5, 0),
    # radar I2C to the PMIC: 0R links and bus pads
    "R605": (160.6, 124.75, 0), "R606": (159.4, 126.6, 90),
    "TP614": (162.9, 124.75, 0), "TP615": (162.75, 127.15, 0),
    # USB VBUS hot-plug damper and TVS
    "C204": (127.0, 109.2, 0), "R210": (130.9, 109.2, 0), "D202": (127.4, 111.8, 0),
}
# pad that has to face a given point (picks between rot and rot+180)
FACE = {"FB602": ("1", (172.325, 104.9)), "C626": ("1", (175.45, 108.6)),
        "C628": ("1", (168.25, 108.6)), "C630": ("1", (172.4, 108.2)), "C633": ("1", (170.3, 110.2)), "C606": ("1", (167.1, 122.25)),
        "R621": ("1", (170.75, 123.7)), "R620": ("2", (171.7, 123.7)), "R623": ("1", (172.75, 123.7)),
        "R622": ("2", (173.7, 123.7)), "R625": ("1", (173.25, 126.45)), "R624": ("2", (173.25, 127.4)),
        "R627": ("1", (170.7, 127.4)), "R626": ("2", (170.7, 126.45)),
        "C631": ("1", (173.4, 111.7)), "C632": ("1", (168.5, 111.7)),
        "R628": ("1", (153.1, 113.9)), "R629": ("1", (160.8, 117.5)),
        "R605": ("2", (161.2, 124.75)), "R606": ("1", (159.4, 127.1)),
        "C204": ("1", (125.5, 109.2)), "R210": ("1", (130.1, 109.2)), "D202": ("1", (126.0, 111.8))}


def stage2(b, comps, nets, rt):
    """Place the new footprints."""
    tp_attr = b.FindFootprintByReference("TP603").GetAttributes()
    for ref, (x, y, rot, *side) in PLACE.items():
        f = add_fp(b, comps, ref, x, y, rot, back=bool(side))
        if ref in FACE:
            num, tgt = FACE[ref]
            p = pad(b, ref, num)
            d0 = (MM(p.GetPosition().x) - tgt[0]) ** 2 + (MM(p.GetPosition().y) - tgt[1]) ** 2
            f.SetOrientationDegrees(rot + 180)
            p = pad(b, ref, num)
            if (MM(p.GetPosition().x) - tgt[0]) ** 2 + (MM(p.GetPosition().y) - tgt[1]) ** 2 > d0:
                f.SetOrientationDegrees(rot)
        if ref.startswith("TP"):
            f.SetAttributes(tp_attr)
    sync_nets(b, comps, nets)


def refill(b):
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())


def straight(b, netname, layer, pts, width):
    R.add_track(b, netname, layer, pts, width)


def xy(b, ref, num):
    return padpt(b, ref, num)[1:]


def stage3(b, comps, nets, rt):
    """Power parts: second ferrite, buck output bulk, VBUS damper."""
    north = (162, 98, 182, 114)
    rt.route("1V0_RAW", padpt(b, "FB602", "1"), "net", north, width=0.5)
    rt.route("1V0_RAW", padpt(b, "C626", "1"), "net", north, width=0.5)
    rt.route("1V8_RAW", padpt(b, "C628", "1"), "net", north, width=0.5)
    # each bulk cap and each ferrite pad gets its own via to the back-side copper
    for ref, num, n in (("C626", "1", "1V0_RAW"), ("C628", "1", "1V8_RAW"),
                        ("FB602", "1", "1V0_RAW"), ("FB602", "2", "1V0_R")):
        _, x, y = padpt(b, ref, num)
        rt.via_near(n, "F.Cu", x, y, width=0.5, maxd=1.2, avoid_existing=0.6)
    usb = (120, 104, 136, 116)
    straight(b, "USB_VBUS", "F.Cu", [xy(b, "C204", "1"), (124.7, 110.35)], 0.6)
    straight(b, "USB_VBUS", "F.Cu", [xy(b, "C204", "1"), (125.525, 111.3), xy(b, "D202", "1")], 0.6)
    straight(b, "Net-(C204-Pad2)", "F.Cu", [xy(b, "C204", "2"), xy(b, "R210", "1")], 0.6)
    refill(b)
    for ref, num in (("C626", "2"), ("C628", "2"), ("C630", "2"), ("C633", "2"), ("R210", "2"), ("D202", "2")):
        _, x, y = padpt(b, ref, num)
        rt.via_near("GND", "F.Cu", x, y, width=0.4)


def stage4(b, comps, nets, rt):
    """Supervisor."""
    win = (158, 104, 182, 128.4)
    A = lambda n: xy(b, "U601", n)
    # reset outputs 16-18 share a strap along the pad tips, pin 15 joins around the corner
    straight(b, "AOP_NRESET", "F.Cu", [A("16"), A("17"), A("18")], 0.2)
    rt.route("AOP_NRESET", padpt(b, "U601", "15"), padpt(b, "U601", "16"), win, width=0.2)
    rt.route("AOP_NRESET", padpt(b, "U601", "16"), "net", win, width=0.2)
    # VDD, its capacitor and MR
    straight(b, "SYS_5V", "F.Cu", [A("14"), xy(b, "C606", "1")], 0.2)
    rt.route("SYS_5V", padpt(b, "C606", "1"), "net", win, width=0.2)
    rt.route("SYS_5V", padpt(b, "U601", "1"), "net", win, width=0.2)
    # PMIC pin 6 (SDA) has one way out on the back; it goes before the supervisor sense lines
    rt.route("PMIC_SDA", padpt(b, "U600", "6"), padpt(b, "TP615", "1"), (158, 112, 184, 128.4), width=0.15)
    # ground pins into the exposed pad
    ex, ey = A("21")
    for n in ("6", "11", "12", "20"):
        x, y = A(n)
        inner = (x + (0.9 if x < ex - 1.5 else -0.9 if x > ex + 1.5 else 0), y + (0.9 if y < ey - 1.5 else -0.9 if y > ey + 1.5 else 0))
        straight(b, "GND", "F.Cu", [(x, y), inner], 0.2)
    straight(b, "GND", "F.Cu", [A("12"), xy(b, "C606", "2")], 0.2)
    # dividers.  The only via sites next to them lie between the two In3 trunks at y = 125.4;
    # the two buck-output sense lines come up there, so those vias go in first.
    inner = ("In2.Cu", "In3.Cu", "B.Cu")
    risers = {}
    for top, rail, (sx, sy) in (("R626", "1V0_RAW", (172.55, 125.4)), ("R622", "1V8_RAW", (174.5, 125.5))):
        v = rt.via_near(rail, None, sx, sy, maxd=0.5)
        risers[rail] = v
        rt.route(rail, padpt(b, top, "1"), ("F.Cu",) + v, win, width=0.15, layers=("F.Cu",), via=None)
    for pin, sup, top, bot in (("10", "SUP_3V3", "R620", "R621"), ("9", "SUP_1V8", "R622", "R623"),
                               ("8", "SUP_1V2", "R624", "R625"), ("7", "SUP_1V0", "R626", "R627")):
        n = "/Radar PMIC/" + sup
        straight(b, n, "F.Cu", [xy(b, top, "2"), xy(b, bot, "1")], 0.2)
        rt.route(n, padpt(b, "U601", pin), padpt(b, bot, "1"), win, width=0.15, layers=("F.Cu",), via=None)
    for rail, v in risers.items():
        rt.route(rail, ("In2.Cu",) + v, "net", win, width=0.15, layers=inner, exclude=[v + (0.4,)])
    rt.route("3V3_R", padpt(b, "R620", "1"), "net", win, width=0.2)
    rt.route("1V2_R", padpt(b, "R624", "1"), "net", win, width=0.2)
    rt.route("PMIC_PGOOD", padpt(b, "TP613", "1"), "net", win, width=0.2)
    refill(b)
    # GND via for the supervisor pad. Every free via site here lies under a paste aperture of the
    # exposed pad; vias are filled and capped, so this is the same accepted case as under U600.
    rt.via_near("GND", "F.Cu", 166.45, 125.65, width=0.3)


def stage5(b, comps, nets, rt):
    """Radar I2C (balls G3 = SCL, G1 = SDA) through R605/R606 to the PMIC bus."""
    west = (139, 111, 166, 128.4)
    rt.route("PMIC_SCL", padpt(b, "R605", "2"), padpt(b, "TP614", "1"), west, width=0.2, layers=("F.Cu",), via=None)
    # G3 is enclosed on all four signal layers.  Its way out is In3 eastwards between rows 2 and 3, which
    # the 3V3_R feed of ball H1 crossed at x = 148.275; that feed now reaches the same In3 trunk one column
    # to the west, around the far side of G3.
    remove(b, tracks_at(b, "3V3_R", [(148.275, 126.95)], "In3.Cu"))
    straight(b, "3V3_R", "In3.Cu", [(147.8787, 127.3458), (147.479, 126.946), (146.679, 126.946), (146.679, 125.275)], 0.15)
    g3 = xy(b, "U300", "G3")
    R.add_via(b, "AOP_I2C_SCL", g3[0], g3[1], 0.4, 0.25)        # via in pad, 0.12 mm to the QSPI tracks on In2
    rt.route_bga("AOP_I2C_SCL", ("In3.Cu",) + g3, padpt(b, "R605", "1"), west)
    # G1 (outer row) is boxed in on every layer: front strap of 1V8_R above it, C336 under it.  Its only way
    # out is a 0.1 mm track along the board edge side of row 1, still under the package, 0.35 mm from the
    # routed edge (rule "Radar I2C SDA edge escape" in the .kicad_dru).
    # G3 leaves through a 0.4/0.25 mm via in its pad.
    rt.route_bga("AOP_I2C_SDA", padpt(b, "U300", "G1"), padpt(b, "R606", "1"), west, edge=0.3)
    straight(b, "3V3_R", "F.Cu", [xy(b, "R601", "1"), (160.45, 127.25)], 0.3)
    rt.route("PMIC_SDA", padpt(b, "R606", "2"), padpt(b, "R601", "2"), west, width=0.2, layers=("F.Cu",), via=None)
    rt.route("PMIC_SDA", padpt(b, "R601", "2"), padpt(b, "TP615", "1"), west, width=0.2, layers=("F.Cu",), via=None)
    east = (158, 112, 184, 128.4)
    rt.route("PMIC_SCL", padpt(b, "TP614", "1"), "net", east, width=0.15, exclude=[(162.0, 124.75, 1.6)])


def stage5b(b, comps, nets, rt):
    """Remote sense of the 1.0 V and 1.8 V rails (FB_B2 / FB_B3)."""
    fb2, fb3 = "/Radar PMIC/FB_B2", "/Radar PMIC/FB_B3"
    pm = (164, 106, 182, 122)
    # PMIC feedback pins (back) up to the coupling caps (front), caps to the buck outputs
    rt.route(fb2, padpt(b, "U600", "1"), padpt(b, "C631", "1"), pm, width=0.2)
    rt.route(fb3, padpt(b, "U600", "21"), padpt(b, "C632", "1"), pm, width=0.2)
    straight(b, "1V0_RAW", "F.Cu", [xy(b, "C631", "2"), xy(b, "TP608", "1")], 0.3)
    straight(b, "1V8_RAW", "F.Cu", [xy(b, "C632", "2"), xy(b, "C628", "1")], 0.3)
    # 1.8 V Kelvin tap: the 1V8_R test pad, which hangs on the bulk capacitors at the radar
    straight(b, "1V8_R", "F.Cu", [xy(b, "R629", "1"), xy(b, "TP606", "1")], 0.2)
    rt.route(fb3, padpt(b, "R629", "2"), padpt(b, "C632", "1"), (156, 108, 180, 122), width=0.15)
    # 1.0 V Kelvin tap: a via inside R628 pad 1, between four ball pads, down to the In3 feed 3 mm
    # from the VIN_13RF balls
    R.add_via(b, "1V0_R", 153.1, 114.15, 0.35, 0.2)
    rt.route(fb2, padpt(b, "R628", "2"), padpt(b, "C631", "1"), (150, 103, 180, 122), width=0.15)


def add_zone(b, netname, layer, pts, name, clearance=0.2, min_w=0.25, priority=10):
    z = pcbnew.ZONE(b)
    z.SetLayer(b.GetLayerID(layer))
    z.SetNetCode(b.GetNetcodeFromNetname(netname))
    z.SetZoneName(name)
    z.SetAssignedPriority(priority)
    z.SetLocalClearance(NM(clearance))
    z.SetMinThickness(NM(min_w))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    o = z.Outline()
    o.NewOutline()
    for x, y in pts:
        o.Append(NM(x), NM(y))
    b.Add(z)
    return z


def stage6(b, comps, nets, rt):
    """Wide supply feeds to the radar.

    The post-layout extraction (simulation/pdn_extract.py) showed the Rev 1.0 1.0 V feed, a 0.75 mm
    track on 17.5 um In3 copper, dropping well over 100 mV at the 2.5 A peak.  Copper areas now run
    in parallel with the old tracks; the fill keeps its own clearance to everything else.
    """
    # 1.0 V: beads (172.3,106.5) -> west along the antenna notch -> radar balls G/H/J 16-18
    add_zone(b, "1V0_R", "In3.Cu", [(157.0, 113.2), (163.2, 106.6), (173.4, 105.4), (173.4, 111.6), (163.4, 111.6),
                                    (159.4, 115.6), (157.0, 115.6)], "1V0_R feed In3")
    add_zone(b, "1V0_R", "In3.Cu", [(146.5, 113.2), (157.3, 113.2), (157.3, 115.6), (149.3, 115.6), (149.3, 115.75),
                                    (146.5, 115.75)], "1V0_R feed In3, under the radar", clearance=0.1, min_w=0.15,
             priority=11)
    # the same path again on the 35 um front copper, stitched to In3
    add_zone(b, "1V0_R", "F.Cu", [(156.7, 113.25), (160.4, 109.5), (169.9, 109.5), (169.9, 107.6), (171.2, 107.6),
                                  (171.2, 105.9), (173.4, 105.9), (173.4, 113.25), (164.2, 113.25), (163.4, 115.45),
                                  (156.7, 115.45)], "1V0_R feed F.Cu")
    for x, y in ((157.4, 114.4), (158.3, 114.6), (157.3, 113.7), (165.0, 111.5), (168.5, 112.0), (171.5, 110.5),
                 (172.5, 108.5)):
        rt.via_near("1V0_R", None, x, y, maxd=0.9, avoid_existing=0.8, optional=True)
    refill(b)


def fix_islands(b, rt):
    """Outer-layer GND pour fragments that hold a pad but no via get one."""
    import islands
    for _ in range(3):
        refill(b)
        isl = islands.islands(b)
        if not isl:
            return
        for layer, pads in isl:
            name, x, y = pads[0]
            print("island:", layer, pads, flush=True)
            rt.via_near("GND", layer, x, y, width=0.25, maxd=2.0)
    refill(b)


STAGES = [stage1, stage2, stage3, stage4, stage5, stage5b, stage6]


def main():
    upto = int(sys.argv[sys.argv.index("--stage") + 1]) if "--stage" in sys.argv else len(STAGES)
    # start from the Rev 1.0 board, loaded in place so the project's rules and netclasses apply
    open(PCB, "wb").write(subprocess.run(["git", "-C", ROOT, "show", f"{BASE}:IWR-AOP-MODULE.kicad_pcb"],
                                         check=True, capture_output=True).stdout)
    b = pcbnew.LoadBoard(PCB)
    comps, nets = read_netlist()
    rt = Router(b)
    for st in STAGES[:upto]:
        st(b, comps, nets, rt)
    if upto:
        fix_islands(b, rt)
    tb = b.GetTitleBlock()
    tb.SetRevision("1.1")
    tb.SetDate("2026-11-15")
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print("saved;", len(rt.failed), "routes failed")


if __name__ == "__main__":
    main()
