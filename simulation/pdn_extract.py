#!/usr/bin/env python3
"""Post-layout DC extraction of the radar supply paths (IR drop, path resistance, current density).

Reads the routed copper straight from IWR-AOP-MODULE.kicad_pcb with KiCad's pcbnew:
tracks, pads, via pads and the *filled* zone polygons of a net are rasterised per copper
layer on a 25 um grid, every copper cell becomes a node of a resistor mesh (sheet
resistance from the stackup copper thickness), vias become barrel resistors between the
layers they join, and the ferrite beads link the xx_RAW and xx_R nets with their DC
resistance.  The mesh is solved for the datasheet peak currents drawn at the radar balls.

Usage: python3 simulation/pdn_extract.py        (needs KiCad 10 python, numpy, scipy, pillow, matplotlib)
Writes results/pdn_summary.txt, results/pdn.json and results/pdn_<rail>.png.
"""
import json
import pathlib
import re

import numpy as np
import pcbnew
from PIL import Image, ImageDraw
from scipy import sparse
from scipy.sparse.linalg import spsolve

HERE = pathlib.Path(__file__).resolve().parent
PCB = HERE.parent / "IWR-AOP-MODULE.kicad_pcb"
RESDIR = HERE / "results"
MM = pcbnew.ToMM
LAYERS = ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu")
RHO20 = 1.72e-8            # ohm*m, annealed copper at 20 C
ALPHA = 0.0039             # 1/K
PLATING = 0.018e-3         # via barrel plating, m (JLCPCB minimum; filled vias are epoxy, not copper)
BEAD_DCR = 0.007           # BLM18KG260TN1 maximum DC resistance (4 mohm in Murata's model)
IND_DCR = {"1V0": 0.020, "1V8": 0.020}   # only reported, the regulator senses after the inductor


def stackup():
    txt = PCB.read_text()
    cu = {m.group(1): float(m.group(2)) for m in
          re.finditer(r'\(layer "([FB]\.Cu|In\d\.Cu)"\s*\(type "[^"]*"\)\s*\(thickness ([\d.]+)\)', txt)}
    die = [float(x) for x in re.findall(r'\(layer "dielectric \d+"\s*\(type "[^"]*"\)\s*\(thickness ([\d.]+)\)', txt)]
    assert len(cu) == 6 and len(die) == 5, (cu, die)
    return cu, die


def polys(sps):
    out = []
    for i in range(sps.OutlineCount()):
        o = sps.Outline(i)
        pts = [(MM(o.CPoint(k).x), MM(o.CPoint(k).y)) for k in range(o.PointCount())]
        holes = [[(MM(sps.Hole(i, j).CPoint(k).x), MM(sps.Hole(i, j).CPoint(k).y))
                  for k in range(sps.Hole(i, j).PointCount())] for j in range(sps.HoleCount(i))]
        out.append((pts, holes))
    return out


class Mesh:
    """Resistor mesh of one or more nets, with lumped links between them."""

    def __init__(self, board, nets, res=0.025, temp=20.0, window=None):
        self.b, self.nets, self.res = board, nets, res
        self.cu, self.die = stackup()
        self.rho = RHO20 * (1 + ALPHA * (temp - 20.0))
        self.lid = {l: board.GetLayerID(l) for l in LAYERS}
        self._bbox(window)
        self.mask = {}                       # (net, layer) -> bool array
        for n in nets:
            self._raster(n)
        self.idx = {}
        k = 0
        for key, m in self.mask.items():
            ix = -np.ones(m.shape, np.int64)
            ix[m] = np.arange(k, k + m.sum())
            k += int(m.sum())
            self.idx[key] = ix
        self.n = k
        self.rows, self.cols, self.vals = [], [], []
        self.extra = {}                      # name -> node
        self._sheets()
        self._vias()

    # ------------------------------------------------------------ geometry
    def _bbox(self, window):
        if window:
            self.x0, self.y0, x1, y1 = window
        else:
            xs, ys = [], []
            for t in self.b.GetTracks():
                if t.GetNetname() in self.nets:
                    bb = t.GetBoundingBox()
                    xs += [MM(bb.GetLeft()), MM(bb.GetRight())]
                    ys += [MM(bb.GetTop()), MM(bb.GetBottom())]
            for f in self.b.GetFootprints():
                for p in f.Pads():
                    if p.GetNetname() in self.nets:
                        bb = p.GetBoundingBox()
                        xs += [MM(bb.GetLeft()), MM(bb.GetRight())]
                        ys += [MM(bb.GetTop()), MM(bb.GetBottom())]
            self.x0, self.y0, x1, y1 = min(xs) - 0.5, min(ys) - 0.5, max(xs) + 0.5, max(ys) + 0.5
        self.w = int(round((x1 - self.x0) / self.res)) + 1
        self.h = int(round((y1 - self.y0) / self.res)) + 1

    def px(self, x, y):
        return ((x - self.x0) / self.res, (y - self.y0) / self.res)

    def _draw(self, img, pl):
        for pts, holes in pl:
            if len(pts) < 3:
                continue
            tmp = Image.new("1", (self.w, self.h), 0)
            d = ImageDraw.Draw(tmp)
            d.polygon([self.px(*p) for p in pts], fill=1)
            for hp in holes:
                d.polygon([self.px(*p) for p in hp], fill=0)
            img.paste(1, mask=tmp)

    def _shape(self, item, lid):
        s = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(s, lid, 0, pcbnew.FromMM(0.003), pcbnew.ERROR_INSIDE)
        return polys(s)

    def _raster(self, net):
        img = {l: Image.new("1", (self.w, self.h), 0) for l in LAYERS}
        for t in self.b.GetTracks():
            if t.GetNetname() != net:
                continue
            if t.GetClass() == "PCB_VIA":
                for l in LAYERS:
                    self._draw(img[l], self._shape(t, self.lid[l]))
            else:
                self._draw(img[self.b.GetLayerName(t.GetLayer())], self._shape(t, t.GetLayer()))
        for f in self.b.GetFootprints():
            for p in f.Pads():
                if p.GetNetname() != net:
                    continue
                for l in LAYERS:
                    if p.IsOnLayer(self.lid[l]):
                        self._draw(img[l], self._shape(p, self.lid[l]))
        for z in self.b.Zones():
            if z.GetIsRuleArea() or z.GetNetname() != net:
                continue
            for l in LAYERS:
                if z.IsOnLayer(self.lid[l]):
                    self._draw(img[l], polys(z.GetFilledPolysList(self.lid[l])))
        for l in LAYERS:
            m = np.array(img[l], bool)
            if m.any():
                self.mask[(net, l)] = m

    # ------------------------------------------------------------ mesh
    def _add(self, a, b, g):
        self.rows += [a, b, a, b]
        self.cols += [a, b, b, a]
        self.vals += [g, g, -g, -g]

    def _sheets(self):
        for (net, l), m in self.mask.items():
            g = self.cu[l] * 1e-3 / self.rho          # 1 / sheet resistance (square cells)
            ix = self.idx[(net, l)]
            for a, b in ((ix[:, :-1], ix[:, 1:]), (ix[:-1, :], ix[1:, :])):
                ok = (a >= 0) & (b >= 0)
                a, b = a[ok], b[ok]
                self.rows += [a, b, a, b]
                self.cols += [a, b, b, a]
                self.vals += [np.full(a.size, g), np.full(a.size, g), np.full(a.size, -g), np.full(a.size, -g)]

    def _disc(self, x, y, r):
        cx, cy = self.px(x, y)
        rr = max(r / self.res, 0.8)
        i0, i1 = int(max(0, cy - rr - 1)), int(min(self.h, cy + rr + 2))
        j0, j1 = int(max(0, cx - rr - 1)), int(min(self.w, cx + rr + 2))
        if i1 <= i0 or j1 <= j0:
            return np.zeros(0, int), np.zeros(0, int)
        ii, jj = np.mgrid[i0:i1, j0:j1]
        ok = (ii - cy) ** 2 + (jj - cx) ** 2 <= rr * rr
        return ii[ok], jj[ok]

    def node(self, name):
        if name not in self.extra:
            self.extra[name] = self.n
            self.n += 1
        return self.extra[name]

    def _vias(self):
        """Barrel resistors between the layers a via (or a plated through pad) joins."""
        z = {}
        acc = 0.0
        for i, l in enumerate(LAYERS):
            z[l] = acc + self.cu[l] / 2
            acc += self.cu[l] + (self.die[i] if i < 5 else 0)
        self.nvia = 0
        items = [(t.GetNetname(), MM(t.GetPosition().x), MM(t.GetPosition().y), MM(t.GetDrillValue()))
                 for t in self.b.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() in self.nets]
        for f in self.b.GetFootprints():
            for p in f.Pads():
                if p.GetNetname() in self.nets and p.GetDrillSize().x > 0:
                    items.append((p.GetNetname(), MM(p.GetPosition().x), MM(p.GetPosition().y), MM(p.GetDrillSize().x)))
        for net, x, y, d in items:
            prev = None
            for l in LAYERS:
                if (net, l) not in self.mask:
                    continue
                ii, jj = self._disc(x, y, d / 2 + 0.05)
                c = self.idx[(net, l)][ii, jj]
                c = c[c >= 0]
                if not c.size:
                    continue
                v = self.node(f"via{self.nvia}_{l}")
                for cc in c:
                    self._add(v, int(cc), 1e4)
                if prev is not None:
                    length = (z[l] - z[prev[1]]) * 1e-3
                    area = np.pi * (d * 1e-3 - PLATING) * PLATING
                    self._add(prev[0], v, area / (self.rho * length))
                prev = (v, l)
            self.nvia += 1

    def pad(self, ref, num):
        """Super-node of a pad (solder joint / ball taken as equipotential)."""
        name = f"{ref}.{num}"
        if name in self.extra:
            return self.extra[name]
        v = self.node(name)
        f = self.b.FindFootprintByReference(ref)
        hit = 0
        for p in f.Pads():
            if p.GetNumber() != num or p.GetNetname() not in self.nets:
                continue
            for l in LAYERS:
                key = (p.GetNetname(), l)
                if not p.IsOnLayer(self.lid[l]) or key not in self.mask:
                    continue
                tmp = Image.new("1", (self.w, self.h), 0)
                self._draw(tmp, self._shape(p, self.lid[l]))
                c = self.idx[key][np.array(tmp, bool)]
                for cc in c[c >= 0]:
                    self._add(v, int(cc), 1e4)
                    hit += 1
        assert hit, f"pad {name} has no copper in the mesh"
        return v

    def link(self, a, b, r):
        self._add(a, b, 1.0 / r)

    def solve(self, ref_node, currents):
        """ref_node is held at 0 V; currents = {node: A drawn out of the node}. Returns node voltages."""
        G = sparse.coo_matrix((np.concatenate([np.atleast_1d(v) for v in self.vals]),
                               (np.concatenate([np.atleast_1d(v) for v in self.rows]),
                                np.concatenate([np.atleast_1d(v) for v in self.cols]))),
                              shape=(self.n, self.n)).tocsr()
        I = np.zeros(self.n)
        for nd, a in currents.items():
            I[nd] -= a
        keep = np.ones(self.n, bool)
        keep[ref_node] = False
        # drop floating copper (not connected to the reference) so the system is non-singular
        ncomp, lab = sparse.csgraph.connected_components(G, directed=False)
        keep &= lab == lab[ref_node]
        V = np.full(self.n, np.nan)
        V[ref_node] = 0.0
        V[keep] = spsolve(G[keep][:, keep].tocsc(), I[keep])
        self.V = V
        return V

    # ------------------------------------------------------------ reporting
    def density(self, net, layer):
        """|J| per cell as A per mm of copper width."""
        ix = self.idx[(net, layer)]
        v = np.where(ix >= 0, self.V[np.clip(ix, 0, None)], np.nan)
        g = self.cu[layer] * 1e-3 / self.rho
        jx = np.zeros(ix.shape)
        jy = np.zeros(ix.shape)
        dx = (v[:, 1:] - v[:, :-1]) * g
        dy = (v[1:, :] - v[:-1, :]) * g
        jx[:, :-1] = np.nan_to_num(dx)
        jy[:-1, :] = np.nan_to_num(dy)
        return np.hypot(jx, jy) / self.res, v

    def plot(self, title, out, zoom=None):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        keys = [k for k in self.mask]
        vmin = np.nanmin(self.V[: self.n])
        fig, axs = plt.subplots(1, len(keys), figsize=(5.2 * len(keys), 4.6), squeeze=False)
        ext = (self.x0, self.x0 + self.w * self.res, self.y0 + self.h * self.res, self.y0)
        for ax, key in zip(axs[0], keys):
            ix = self.idx[key]
            v = np.where(ix >= 0, self.V[np.clip(ix, 0, None)], np.nan) * 1e3
            im = ax.imshow(v, extent=ext, cmap="viridis", vmin=vmin * 1e3, vmax=0, interpolation="nearest")
            ax.set_title(f"{key[0]}  {key[1]}", fontsize=9)
            ax.tick_params(labelsize=7)
            if zoom:
                ax.set_xlim(zoom[0], zoom[2]); ax.set_ylim(zoom[3], zoom[1])
            fig.colorbar(im, ax=ax, shrink=0.8, label="mV from source")
        fig.suptitle(title, fontsize=10)
        fig.savefig(out, dpi=140, bbox_inches="tight")
        plt.close(fig)


def pads_on(board, ref, net):
    return sorted({p.GetNumber() for p in board.FindFootprintByReference(ref).Pads() if p.GetNetname() == net})


def refs_on(board, net, prefix):
    out = []
    for f in board.GetFootprints():
        if f.GetReference().startswith(prefix):
            for p in f.Pads():
                if p.GetNetname() == net:
                    out.append((f.GetReference(), p.GetNumber()))
    return sorted(set(out))


def rail(board, name, raw, out, amps, sense, lines, js, temp):
    """One supply: inductor pad -> (beads) -> radar balls.

    `sense` is the pad whose voltage the PMIC regulates in DC: its own feedback pin on 3V3/1V2, the
    Kelvin tap of the remote-sense resistor (R628 / R629) on 1V0/1V8."""
    nets = [n for n in (raw, out) if n]
    m = Mesh(board, nets, temp=temp)
    src_net = raw or out
    ind = [(r, p) for r, p in refs_on(board, src_net, "L")]
    assert len(ind) == 1, ind
    src = m.pad(*ind[0])
    beads = []
    if raw:
        for ref in sorted({r for r, _ in refs_on(board, raw, "FB")}):
            a = m.pad(ref, pads_on(board, ref, raw)[0])
            b = m.pad(ref, pads_on(board, ref, out)[0])
            m.link(a, b, BEAD_DCR * (1 + ALPHA * (temp - 20)))
            beads.append((ref, a, b))
    balls = pads_on(board, "U300", out)
    sink = {m.pad("U300", b): amps / len(balls) for b in balls}
    fb = m.pad(*sense)
    V = m.solve(src, sink)
    vb = np.array([V[n] for n in sink])
    lines.append(f"{name}: {amps:.2f} A peak from {ind[0][0]}.{ind[0][1]} into {len(balls)} radar balls "
                 f"({', '.join(balls)}), copper at {temp:.0f} C")
    lines.append(f"  inductor pad -> DC sense point {sense[0]}.{sense[1]}: {-V[fb]*1e3:6.2f} mV   (regulation point; the buck "
                 f"output sits this much above nominal)")
    if beads:
        for ref, a, b in beads:
            i = (V[a] - V[b]) / (BEAD_DCR * (1 + ALPHA * (temp - 20)))
            lines.append(f"  {ref}: {i:5.2f} A, {(V[a]-V[b])*1e3:5.1f} mV across the bead; bead output at {-V[b]*1e3:6.2f} mV")
        vbo = np.mean([V[b] for _, a, b in beads])
        lines.append(f"  bead outputs -> radar balls (copper only): {(vbo - vb.mean())*1e3:6.2f} mV mean, "
                     f"{(vbo - vb.min())*1e3:6.2f} mV worst ball = {(vbo - vb.mean())/amps*1e3:5.2f} mohm")
    drop = V[fb] - vb
    lines.append(f"  sense point -> radar balls: {drop.mean()*1e3:6.2f} mV mean, {drop.max()*1e3:6.2f} mV worst ball "
                 f"({balls[int(np.argmax(drop))]}), spread between balls {np.ptp(vb)*1e3:.2f} mV")
    lines.append(f"  effective resistance sense -> radar: {drop.mean()/amps*1e3:6.2f} mohm; "
                 f"inductor pad -> radar: {-vb.mean()/amps*1e3:6.2f} mohm")
    # current density / self heating of the narrowest loaded copper
    worst = (0, None)
    for (net, l) in m.mask:
        j, _ = m.density(net, l)
        # ignore the cells right at pads and vias (current crowding into a lumped node)
        jj = np.nanpercentile(j[m.mask[(net, l)]], 99.5)
        if jj > worst[0]:
            worst = (jj, f"{net} {l}")
        a_mm2 = jj / (m.cu[l])               # A/mm2
        lines.append(f"  {net:8} {l:6}: 99.5th percentile current density {jj:5.2f} A/mm width = {a_mm2:5.0f} A/mm2")
    p = float(sum(a * (-V[n]) for n, a in sink.items()))
    lines.append(f"  copper + bead loss at peak current: {p*1e3:.0f} mW")
    lines.append("")
    js[name] = dict(amps=amps, temp=temp, sense_to_radar_mohm=float(drop.mean() / amps * 1e3),
                    sense_to_radar_worst_mv=float(drop.max() * 1e3),
                    source_to_radar_mohm=float(-vb.mean() / amps * 1e3), raw_rise_mv=float(-V[fb] * 1e3),
                    best_ball_above_sense_mv=float(-(V[fb] - vb).min() * 1e3),
                    bead_to_radar_mohm=float((vbo - vb.mean()) / amps * 1e3) if beads else None,
                    bead_split=[float((V[a] - V[b]) / (BEAD_DCR * (1 + ALPHA * (temp - 20)))) for _, a, b in beads])
    return m


def ground(board, lines, js, temp):
    """Return path of the 1.0 V peak current: radar GND balls back to the Buck2 output capacitors."""
    win = (138.0, 100.0, 182.0, 128.4)
    m = Mesh(board, ["GND"], res=0.05, temp=temp, window=win)
    caps = [r for r in ("C612", "C626") if board.FindFootprintByReference(r)]
    ref = m.node("ret")
    for c in caps:
        m.link(ref, m.pad(c, pads_on(board, c, "GND")[0]), 1e-4)
    balls = pads_on(board, "U300", "GND")
    # the 1.0 V current returns through the balls next to the 1V0 group (rows G-J, columns 13-18)
    near = [b for b in balls if b[0] in "FGHJK" and int(b[1:]) >= 12] or balls
    sink = {m.pad("U300", b): -2.5 / len(near) for b in near}
    V = m.solve(ref, sink)
    vb = np.array([V[n] for n in sink])
    lines.append(f"GND return of the 1.0 V peak current: 2.50 A from {len(near)} radar GND balls back to "
                 f"{'/'.join(caps)} on all GND layers, copper at {temp:.0f} C")
    lines.append(f"  ground rise at the radar: {vb.mean()*1e3:5.2f} mV mean, {vb.max()*1e3:5.2f} mV worst = "
                 f"{vb.mean()/2.5*1e3:4.2f} mohm")
    lines.append("")
    js["GND_1V0_return"] = dict(mohm=float(vb.mean() / 2.5 * 1e3))
    return m


def main():
    RESDIR.mkdir(exist_ok=True)
    board = pcbnew.LoadBoard(str(PCB))
    lines, js = [], {}
    cu, die = stackup()
    lines.append("Post-layout DC extraction from the routed board (see simulation/pdn_extract.py)")
    lines.append(f"Copper: {', '.join(f'{l} {cu[l]*1e3:.1f} um' for l in LAYERS)}; via barrel plating {PLATING*1e6:.0f} um; "
                 f"bead DCR {BEAD_DCR*1e3:.0f} mohm each (max)")
    lines.append("")
    plots = {}
    for temp in (20.0, 85.0):
        lines.append(f"================ copper at {temp:.0f} C ================")
        sub = {}
        plots["1V0"] = rail(board, "1V0", "1V0_RAW", "1V0_R", 2.5, ("R628", "1"), lines, sub, temp)
        plots["1V8"] = rail(board, "1V8", "1V8_RAW", "1V8_R", 0.85, ("R629", "1"), lines, sub, temp)
        plots["1V2"] = rail(board, "1V2", None, "1V2_R", 1.0, ("U600", "14"), lines, sub, temp)
        plots["3V3"] = rail(board, "3V3", None, "3V3_R", 0.07, ("U600", "8"), lines, sub, temp)
        plots["GND"] = ground(board, lines, sub, temp)
        js[f"{temp:.0f}C"] = sub
    plots["1V0"].plot("1.0 V path at 2.5 A, 85 C: voltage relative to the Buck2 inductor pad", RESDIR / "pdn_1v0.png")
    plots["1V8"].plot("1.8 V path at 0.85 A, 85 C", RESDIR / "pdn_1v8.png")
    plots["1V2"].plot("1.2 V path at 1.0 A, 85 C", RESDIR / "pdn_1v2.png")
    b = js["85C"]
    lines.append("Budget at the radar balls, worst case (85 C copper, peak current, ground return included):")
    for name, vnom, vmin, tol in (("1V0", 1.0, 0.95, 0.02), ("1V8", 1.8, 1.71, 0.02), ("1V2", 1.2, 1.14, 0.02), ("3V3", 3.3, 3.135, 0.02)):
        d = b[name]["sense_to_radar_worst_mv"] * 1e-3
        g = b["GND_1V0_return"]["mohm"] * b[name]["amps"] * 1e-3
        acc = max(0.02, tol * vnom)        # LP87524 DC accuracy: +-2 % or +-20 mV
        v = vnom - acc - d - g
        lines.append(f"  {name}: {vnom:.2f} V - {acc*1e3:.0f} mV (PMIC accuracy) - {d*1e3:.1f} mV (sense point to worst ball) - {g*1e3:.1f} mV (return) "
                     f"= {v:.3f} V vs radar minimum {vmin:.3f} V -> {'OK' if v >= vmin else 'BELOW MINIMUM'} "
                     f"(margin {1e3*(v-vmin):+.0f} mV)")
        b[name]["dc_at_radar_worst"] = v
    (RESDIR / "pdn_summary.txt").write_text("\n".join(lines) + "\n")
    (RESDIR / "pdn.json").write_text(json.dumps(js, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
