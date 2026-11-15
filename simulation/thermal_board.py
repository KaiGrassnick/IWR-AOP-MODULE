#!/usr/bin/env python3
"""Steady-state board temperature map from the real copper (first-order thermal model).

The board is two conducting sheets (front half: F.Cu, In1, In2; back half: In3, In4, B.Cu)
on a 0.5 mm grid.  Each cell's in-plane conductance comes from the copper actually there
(rasterised tracks, pads and filled zones of every layer) plus the FR-4; the sheets are
coupled through the core by FR-4 and by every via barrel in the cell; both faces lose heat
to still air with one combined convection + radiation coefficient.  Heat sources are the
radar (datasheet average power), the PMIC and inductors (loss at 85 % efficiency), the
ferrites and the USB bridge.  Junction temperature = board temperature under the part
+ PsiJB x power (SWRS237C 7.11, SNVSAW2B 6.4).

This is a bare board in still air with no carrier, housing or heat sink.  It ignores the
air gap physics of a real enclosure, so it ranks cases and shows where the heat goes; it is
not a substitute for a thermocouple on the finished assembly.

Usage: python3 simulation/thermal_board.py     (KiCad 10 python, numpy, scipy, pillow, matplotlib)
"""
import pathlib

import numpy as np
import pcbnew
from PIL import Image, ImageDraw
from scipy import sparse
from scipy.sparse.linalg import spsolve

from pdn_extract import LAYERS, PCB, RESDIR, polys, stackup

MM = pcbnew.ToMM
FINE, CELL = 0.1, 0.5            # raster and thermal grid, mm
K_CU, K_FR4_XY, K_FR4_Z = 385.0, 0.8, 0.3      # W/m/K
H_AIR = 10.0                      # W/m2/K, natural convection + radiation, each face [ASSUMED]
PLATING = 18e-6
PSI_JB = {"U300": 7.3, "U600": 4.7}
TJ_MAX = {"U300": 105.0, "U600": 125.0}


def raster(board):
    outl = pcbnew.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outl, True)
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0 = MM(bb.GetLeft()) - 1, MM(bb.GetTop()) - 1
    w = int((MM(bb.GetWidth()) + 2) / FINE)
    h = int((MM(bb.GetHeight()) + 2) / FINE)
    w -= w % 5
    h -= h % 5

    def draw(img, pl):
        for pts, holes in pl:
            if len(pts) < 3:
                continue
            tmp = Image.new("1", (w, h), 0)
            d = ImageDraw.Draw(tmp)
            d.polygon([((x - x0) / FINE, (y - y0) / FINE) for x, y in pts], fill=1)
            for hp in holes:
                d.polygon([((x - x0) / FINE, (y - y0) / FINE) for x, y in hp], fill=0)
            img.paste(1, mask=tmp)

    def shape(item, lid):
        s = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(s, lid, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
        return polys(s)

    brd = Image.new("1", (w, h), 0)
    draw(brd, polys(outl))
    cu = {l: Image.new("1", (w, h), 0) for l in LAYERS}
    lid = {l: board.GetLayerID(l) for l in LAYERS}
    vias = []
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA":
            vias.append((MM(t.GetPosition().x), MM(t.GetPosition().y), MM(t.GetDrillValue())))
            for l in LAYERS:
                draw(cu[l], shape(t, lid[l]))
        else:
            draw(cu[board.GetLayerName(t.GetLayer())], shape(t, t.GetLayer()))
    for f in board.GetFootprints():
        for p in f.Pads():
            for l in LAYERS:
                if p.IsOnLayer(lid[l]):
                    draw(cu[l], shape(p, lid[l]))
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for l in LAYERS:
            if z.IsOnLayer(lid[l]):
                draw(cu[l], polys(z.GetFilledPolysList(lid[l])))
    n = int(CELL / FINE)

    def coarse(img):
        a = np.array(img, float)
        return a.reshape(h // n, n, w // n, n).mean(axis=(1, 3))
    return x0, y0, coarse(brd), {l: coarse(cu[l]) for l in LAYERS}, vias


def solve(board, sources, geo):
    x0, y0, brd, cov, vias = geo
    cuth, die = stackup()
    ny, nx = brd.shape
    inside = brd > 0.5
    idx = -np.ones((2, ny, nx), int)
    k = int(inside.sum())
    idx[0][inside] = np.arange(k)
    idx[1][inside] = np.arange(k, 2 * k)
    half = sum(die) * 1e-3 / 2
    # sheet conductance (W/K per square)
    gs = [sum(cov[l] * K_CU * cuth[l] * 1e-3 for l in LAYERS[:3]) + K_FR4_XY * half,
          sum(cov[l] * K_CU * cuth[l] * 1e-3 for l in LAYERS[3:]) + K_FR4_XY * half]
    rows, cols, vals = [], [], []

    def add(a, b, g):
        a, b, g = np.asarray(a), np.asarray(b), np.asarray(g, float)
        rows.extend([a, b, a, b]); cols.extend([a, b, b, a]); vals.extend([g, g, -g, -g])
    for s in (0, 1):
        ix, g = idx[s], gs[s]
        for (a, b, ga, gb) in ((ix[:, :-1], ix[:, 1:], g[:, :-1], g[:, 1:]), (ix[:-1, :], ix[1:, :], g[:-1, :], g[1:, :])):
            ok = (a >= 0) & (b >= 0)
            gg = 2 * ga[ok] * gb[ok] / (ga[ok] + gb[ok])
            add(a[ok], b[ok], gg)
    area = (CELL * 1e-3) ** 2
    gz = np.full((ny, nx), K_FR4_Z * area / (sum(die) * 1e-3 / 2 + 0.2e-3))
    length = sum(die) * 1e-3 / 2
    for x, y, d in vias:
        i, j = int((y - y0) / CELL), int((x - x0) / CELL)
        if 0 <= i < ny and 0 <= j < nx:
            gz[i, j] += K_CU * np.pi * (d * 1e-3 - PLATING) * PLATING / length
    a, b = idx[0][inside], idx[1][inside]
    add(a, b, gz[inside])
    G = sparse.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(2 * k, 2 * k)).tocsr()
    G = G + sparse.diags(np.full(2 * k, H_AIR * area))          # each sheet loses heat from its outer face
    q = np.zeros(2 * k)
    for (side, xa, ya, xb, yb, watts) in sources:
        i0, i1 = int((ya - y0) / CELL), int(np.ceil((yb - y0) / CELL))
        j0, j1 = int((xa - x0) / CELL), int(np.ceil((xb - x0) / CELL))
        cells = idx[side][i0:i1, j0:j1]
        cells = cells[cells >= 0]
        q[cells] += watts / len(cells)
    T = spsolve(G.tocsc(), q)
    out = np.full((2, ny, nx), np.nan)
    out[0][inside] = T[:k]
    out[1][inside] = T[k:]
    return out


def box(board, ref, grow=0.0):
    f = board.FindFootprintByReference(ref)
    bb = f.GetBoundingBox(False)
    return MM(bb.GetLeft()) - grow, MM(bb.GetTop()) - grow, MM(bb.GetRight()) + grow, MM(bb.GetBottom()) + grow


def main():
    board = pcbnew.LoadBoard(str(PCB))
    geo = raster(board)
    x0, y0, brd, cov, vias = geo
    side = lambda ref: 1 if board.FindFootprintByReference(ref).IsFlipped() else 0
    L = ["First-order board thermal model: bare board in still air, both faces h = %.0f W/m2/K, no housing or heat sink" % H_AIR,
         "Copper coverage: " + ", ".join(f"{l} {100*cov[l][brd > 0.5].mean():.0f} %" for l in LAYERS)
         + f"; {len(vias)} vias; board area {brd.sum()*CELL*CELL/100:.1f} cm2", ""]
    # radar average power: SWRS237C Table 7-4 (1-V LDO bypass); 3-TX figure is an estimate
    cases = [("radar idle / low duty (0.6 W)", 0.6), ("2 TX, 24 % duty (1.25 W, Table 7-4)", 1.25),
             ("2 TX, 48 % duty (1.75 W, Table 7-4)", 1.75), ("3 TX, high duty (2.3 W, estimate)", 2.3)]
    maps = {}
    L.append(f"{'case':38} {'board under radar':>18} {'radar Tj rise':>14} {'PMIC Tj rise':>13} {'hottest board':>14} {'max ambient':>12}")
    for name, pr in cases:
        ppm = pr * (1 / 0.85 - 1)                    # converter loss
        src = [(side("U300"), *box(board, "U300"), pr),
               (side("U600"), *box(board, "U600"), ppm * 0.7)]
        for ind in ("L600", "L601", "L602", "L603"):
            src.append((side(ind), *box(board, ind), ppm * 0.3 / 4))
        src.append((side("FB600"), *box(board, "FB600", 0.5), 0.03 * pr))     # ferrites + feed copper
        src.append((side("U200"), *box(board, "U200"), 0.10))                 # CP2105 with USB attached
        T = solve(board, src, geo)
        maps[name] = T

        def under(ref):
            xa, ya, xb, yb = box(board, ref)
            s = side(ref)
            return np.nanmean(T[s][int((ya - y0) / CELL):int((yb - y0) / CELL), int((xa - x0) / CELL):int((xb - x0) / CELL)])
        tr, tp = under("U300"), under("U600")
        jr, jp = tr + PSI_JB["U300"] * pr, tp + PSI_JB["U600"] * ppm * 0.7
        amb = min(TJ_MAX["U300"] - jr, TJ_MAX["U600"] - jp)
        L.append(f"{name:38} {tr:15.1f} K {jr:11.1f} K {jp:10.1f} K {np.nanmax(T):11.1f} K {amb:9.0f} C")
    L += ["", "Rises are above ambient. 'max ambient' keeps the radar junction at or under 105 C and the PMIC under 125 C.",
          "The radar dominates: a bare module in still air is limited by the board's own surface area.",
          "Mounting on a carrier or against a housing wall lowers these numbers; the backside pad TH300 is there for that."]
    (RESDIR / "thermal_summary.txt").write_text("\n".join(L) + "\n")
    print("\n".join(L))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    name = cases[2][0]
    T = maps[name]
    ny, nx = brd.shape
    ext = (x0, x0 + nx * CELL, y0 + ny * CELL, y0)
    fig, axs = plt.subplots(2, 1, figsize=(11, 9))
    for ax, s, lab in ((axs[0], 0, "front half (F.Cu, In1, In2)"), (axs[1], 1, "back half (In3, In4, B.Cu)")):
        im = ax.imshow(T[s], extent=ext, cmap="inferno", vmin=np.nanmin(T), vmax=np.nanmax(T))
        ax.set_title(f"{name}: temperature rise above ambient, {lab}", fontsize=10)
        for ref in ("U300", "U600", "U200"):
            xa, ya, xb, yb = box(board, ref)
            ax.plot([xa, xb, xb, xa, xa], [ya, ya, yb, yb, ya], color="white", lw=0.8)
            ax.text((xa + xb) / 2, ya - 0.6, ref, color="white", ha="center", fontsize=8)
        fig.colorbar(im, ax=ax, shrink=0.8, label="K above ambient")
    fig.tight_layout()
    fig.savefig(RESDIR / "thermal_map.png", dpi=120)


if __name__ == "__main__":
    main()
