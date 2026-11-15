"""Small grid router used for the Rev 1.1 layout edits.

Not a general autorouter: it rasterises the copper of every other net in a
window of the board, takes the Euclidean distance transform, and runs A* on a
0.05 mm grid (45-degree moves, vias allowed) from a start point to a goal
point or to any existing copper of the same net.  Clearances are the board's
0.2 mm netclass value, 0.10 mm inside the U300 courtyard, 0.5 mm to the edge.
The result is always checked afterwards by KiCad's own DRC.
"""
import heapq
import math

import numpy as np
import pcbnew
from PIL import Image, ImageDraw
from scipy import ndimage

MM = pcbnew.ToMM
NM = pcbnew.FromMM
RES = 0.025          # raster pitch, mm
STEP = 2             # A* runs on every 2nd raster cell (0.05 mm)
SLOP = 0.04          # raster safety margin, mm (0.02 for the BGA escapes, checked by DRC)
CLR = 0.2
CLR_BGA = 0.10
EDGE = 0.5
H2H = 0.25
SIG = ("F.Cu", "In2.Cu", "In3.Cu", "B.Cu")
ALL = ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu")


def _polys(sps):
    out = []
    for i in range(sps.OutlineCount()):
        o = sps.Outline(i)
        pts = [(MM(o.CPoint(k).x), MM(o.CPoint(k).y)) for k in range(o.PointCount())]
        holes = []
        for j in range(sps.HoleCount(i)):
            h = sps.Hole(i, j)
            holes.append([(MM(h.CPoint(k).x), MM(h.CPoint(k).y)) for k in range(h.PointCount())])
        out.append((pts, holes))
    return out


class Window:
    def __init__(self, board, x0, y0, x1, y1):
        self.b = board
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.w = int(round((x1 - x0) / RES)) + 1
        self.h = int(round((y1 - y0) / RES)) + 1
        self.slop = SLOP
        u = board.FindFootprintByReference("U300")
        c = u.GetCourtyard(pcbnew.F_CrtYd).BBox()
        self.bga = (MM(c.GetLeft()), MM(c.GetTop()), MM(c.GetRight()), MM(c.GetBottom()))

    # ---- raster helpers
    def px(self, x, y):
        return ((x - self.x0) / RES, (y - self.y0) / RES)

    def _draw(self, img, polys):
        for pts, holes in polys:
            if len(pts) < 3:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if max(xs) < self.x0 or min(xs) > self.x1 or max(ys) < self.y0 or min(ys) > self.y1:
                continue
            if holes:
                tmp = Image.new("1", (self.w, self.h), 0)
                d = ImageDraw.Draw(tmp)
                d.polygon([self.px(*p) for p in pts], fill=1, outline=1)
                for hpts in holes:
                    d.polygon([self.px(*p) for p in hpts], fill=0)
                img.paste(1, mask=tmp)
            else:
                ImageDraw.Draw(img).polygon([self.px(*p) for p in pts], fill=1, outline=1)

    def _item_poly(self, item, lid):
        s = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(s, lid, 0, NM(0.005), pcbnew.ERROR_OUTSIDE)
        return _polys(s)

    def build(self, net):
        """Rasters for routing `net`: per-layer other-net copper distance, hole distance, edge distance."""
        b = self.b
        imgs = {l: Image.new("1", (self.w, self.h), 0) for l in ALL}
        own = {l: Image.new("1", (self.w, self.h), 0) for l in ALL}
        holes = Image.new("1", (self.w, self.h), 0)
        keep = Image.new("1", (self.w, self.h), 0)
        lids = {l: b.GetLayerID(l) for l in ALL}

        def hole(x, y, r):
            cx, cy = self.px(x, y)
            ImageDraw.Draw(holes).ellipse([cx - r / RES, cy - r / RES, cx + r / RES, cy + r / RES], fill=1)

        for t in b.GetTracks():
            same = t.GetNetname() == net
            if t.GetClass() == "PCB_VIA":
                p = t.GetPosition()
                x, y = MM(p.x), MM(p.y)
                if not (self.x0 - 1 < x < self.x1 + 1 and self.y0 - 1 < y < self.y1 + 1):
                    continue
                hole(x, y, MM(t.GetDrillValue()) / 2)
                for l in ALL:
                    self._draw((own if same else imgs)[l], self._item_poly(t, lids[l]))
            else:
                l = b.GetLayerName(t.GetLayer())
                self._draw((own if same else imgs)[l], self._item_poly(t, t.GetLayer()))
        for f in b.GetFootprints():
            for p in f.Pads():
                same = p.GetNetname() == net
                c = p.GetPosition()
                x, y = MM(c.x), MM(c.y)
                if not (self.x0 - 3 < x < self.x1 + 3 and self.y0 - 3 < y < self.y1 + 3):
                    continue
                if p.GetDrillSize().x > 0:
                    hole(x, y, MM(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2)
                for l in ALL:
                    if p.IsOnLayer(lids[l]):
                        self._draw((own if same else imgs)[l], self._item_poly(p, lids[l]))
            for z in f.Zones():
                if z.GetIsRuleArea() and z.GetDoNotAllowTracks():
                    self._draw(keep, _polys(z.Outline()))
        for z in b.Zones():
            if z.GetIsRuleArea():
                if z.GetDoNotAllowTracks():
                    self._draw(keep, _polys(z.Outline()))
                continue
            if z.GetNetname() == "GND" and net != "GND":
                continue          # pours refill around new copper
            same = z.GetNetname() == net
            for l in ALL:
                if z.IsOnLayer(lids[l]):
                    self._draw((own if same else imgs)[l], _polys(z.GetFilledPolysList(lids[l])))
        # board outline: everything outside is an obstacle
        edge = Image.new("1", (self.w, self.h), 1)
        outl = pcbnew.SHAPE_POLY_SET()
        b.GetBoardPolygonOutlines(outl, True)
        tmp = Image.new("1", (self.w, self.h), 0)
        self._draw(tmp, _polys(outl))
        edge.paste(0, mask=tmp)

        def dist(img):
            a = np.array(img, dtype=bool)
            if not a.any():
                return np.full(a.shape, 1e3)
            return ndimage.distance_transform_edt(~a) * RES

        self.net = net
        self.d = {l: dist(imgs[l]) for l in ALL}
        self.own = {l: np.array(own[l], dtype=bool) for l in ALL}
        self.dh = dist(holes)
        self.de = dist(edge)
        self.keep = np.array(keep, dtype=bool)
        ys, xs = np.mgrid[0:self.h, 0:self.w]
        X = self.x0 + xs * RES
        Y = self.y0 + ys * RES
        # relaxed clearance only well inside the courtyard: the rule needs both items to touch it
        m = 0.6
        inb = (X > self.bga[0] + m) & (X < self.bga[2] - m) & (Y > self.bga[1] + m) & (Y < self.bga[3] - m)
        self.clr = np.where(inb, CLR_BGA, CLR)
        self.dvia = np.minimum.reduce([self.d[l] for l in ALL])

    def track_ok(self, layer, width, edge=EDGE):
        # 0.10 mm to U300 pads (front only); 0.09 mm copper to copper on the other layers of the escape
        clr = self.clr if layer == "F.Cu" else np.where(self.clr < CLR, 0.09, CLR)
        return (self.d[layer] >= width / 2 + clr + self.slop) & (self.de >= width / 2 + edge + self.slop) & ~self.keep

    def via_ok(self, dia, drill):
        return ((self.dvia >= dia / 2 + self.clr + self.slop) & (self.dh >= drill / 2 + H2H + self.slop)
                & (self.de >= dia / 2 + EDGE + self.slop) & ~self.keep)

    def cell(self, x, y):
        return (int(round((y - self.y0) / RES / STEP)) * STEP, int(round((x - self.x0) / RES / STEP)) * STEP)

    def xy(self, r, c):
        return (round(self.x0 + c * RES, 4), round(self.y0 + r * RES, 4))

    def route(self, start, goal, width=0.2, layers=SIG, via=(0.45, 0.3), via_cost=3.0, layer_cost=None,
              start_free=0.0, goal_free=0.0, max_nodes=4_000_000, exclude=(), edge=EDGE):
        """start: (layer, x, y).  goal: (layer, x, y) or 'net' (any same-net copper not touching the start).
        Returns [(layer, [(x, y), ...]), ...] and via positions."""
        layer_cost = layer_cost or {}
        ok = {l: self.track_ok(l, width, edge) for l in layers}
        vok = self.via_ok(*via) if via else None
        sl, sx, sy = start
        s = (sl,) + self.cell(sx, sy)

        def free(pt, rad):
            # allow leaving / entering a pad: cells within rad of the terminal are passable on that layer
            if rad <= 0:
                return
            l, x, y = pt
            r0, c0 = self.cell(x, y)
            n = int(rad / RES)
            yy, xx = np.ogrid[-n:n + 1, -n:n + 1]
            m = (yy * yy + xx * xx) <= n * n
            sub = ok[l][max(r0 - n, 0):r0 + n + 1, max(c0 - n, 0):c0 + n + 1]
            mm_ = m[max(n - r0, 0):, max(n - c0, 0):][:sub.shape[0], :sub.shape[1]]
            sub |= mm_ & ~self.keep[max(r0 - n, 0):r0 + n + 1, max(c0 - n, 0):c0 + n + 1]

        free(start, start_free)
        if goal != "net":
            free(goal, goal_free)
            gl, gx, gy = goal
            g = (gl,) + self.cell(gx, gy)
            is_goal = lambda n: n == g
            hx = lambda n: math.hypot(n[1] - g[1], n[2] - g[2]) * RES
        else:
            # same-net copper that is not connected to the start blob
            tgt = {}
            for l in layers:
                lab, _ = ndimage.label(self.own[l])
                sl_lab = lab[s[1], s[2]] if l == sl else 0
                tgt[l] = self.own[l] & (lab != sl_lab) if sl_lab else self.own[l].copy()
            for ex, ey, er in exclude:       # copper that already belongs to the start (e.g. its via barrel)
                r0, c0 = self.cell(ex, ey)
                n = int(er / RES)
                for l in layers:
                    tgt[l][max(r0 - n, 0):r0 + n + 1, max(c0 - n, 0):c0 + n + 1] = False
            is_goal = lambda n: tgt[n[0]][n[1], n[2]]
            hx = lambda n: 0.0
        ok[sl][s[1], s[2]] = True
        moves = [(-STEP, 0, 1), (STEP, 0, 1), (0, -STEP, 1), (0, STEP, 1),
                 (-STEP, -STEP, 1.4142), (-STEP, STEP, 1.4142), (STEP, -STEP, 1.4142), (STEP, STEP, 1.4142)]
        pq = [(hx(s), 0.0, s, None)]
        came = {}
        best = {s: 0.0}
        nodes = 0
        end = None
        while pq:
            f, gc, n, par = heapq.heappop(pq)
            if n in came:
                continue
            came[n] = par
            if is_goal(n):
                end = n
                break
            nodes += 1
            if nodes > max_nodes:
                break
            l, r, c = n
            pd = None
            if par is not None and par[0] == l:
                pd = (r - par[1], c - par[2])
            for dr, dc, w in moves:
                r2, c2 = r + dr, c + dc
                if r2 < 0 or c2 < 0 or r2 >= self.h or c2 >= self.w:
                    continue
                if not ok[l][r2, c2] or not ok[l][r + dr // 2, c + dc // 2]:
                    continue
                n2 = (l, r2, c2)
                cost = gc + w * RES * STEP * layer_cost.get(l, 1.0) + (0.02 if pd is not None and pd != (dr, dc) else 0)
                if cost < best.get(n2, 1e9):
                    best[n2] = cost
                    heapq.heappush(pq, (cost + hx(n2), cost, n2, n))
            if vok is not None and vok[r, c]:
                for l2 in layers:
                    if l2 != l and ok[l2][r, c]:
                        n2 = (l2, r, c)
                        cost = gc + via_cost
                        if cost < best.get(n2, 1e9):
                            best[n2] = cost
                            heapq.heappush(pq, (cost + hx(n2), cost, n2, n))
        if end is None:
            raise RuntimeError(f"no route for {self.net} from {start} to {goal} ({nodes} nodes)")
        path = []
        n = end
        while n is not None:
            path.append(n)
            n = came[n]
        path.reverse()
        segs, vias = [], []
        cur = [path[0]]
        for a, b_ in zip(path, path[1:]):
            if a[0] != b_[0]:
                segs.append(cur)
                vias.append(self.xy(a[1], a[2]))
                cur = [b_]
            else:
                cur.append(b_)
        segs.append(cur)
        out = []
        for seg in segs:
            pts = [self.xy(n[1], n[2]) for n in seg]
            simp = [pts[0]]
            for i in range(1, len(pts) - 1):
                ax, ay = simp[-1]
                bx, by = pts[i]
                cx, cy = pts[i + 1]
                if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) > 1e-9:
                    simp.append(pts[i])
            if len(pts) > 1:
                simp.append(pts[-1])
            out.append((seg[0][0], simp))
        # exact terminals
        if out[0][1][0] != (sx, sy):
            out[0][1].insert(0, (sx, sy))
        if goal != "net" and out[-1][1][-1] != (goal[1], goal[2]):
            out[-1][1].append((goal[1], goal[2]))
        return out, vias


def add_track(board, net, layer, pts, width):
    ni = board.FindNet(net)
    made = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if (ax, ay) == (bx, by):
            continue
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(NM(ax), NM(ay)))
        t.SetEnd(pcbnew.VECTOR2I(NM(bx), NM(by)))
        t.SetWidth(NM(width))
        t.SetLayer(board.GetLayerID(layer))
        t.SetNet(ni)
        board.Add(t)
        made.append(t)
    return made


def add_via(board, net, x, y, dia=0.45, drill=0.3):
    v = pcbnew.PCB_VIA(board)
    v.SetPosition(pcbnew.VECTOR2I(NM(x), NM(y)))
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    v.SetWidth(pcbnew.F_Cu, NM(dia))
    v.SetDrill(NM(drill))
    v.SetNet(board.FindNet(net))
    board.Add(v)
    return v
