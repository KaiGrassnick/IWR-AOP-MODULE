"""List GND pour islands on the outer layers that have a pad but no via to the planes."""
import sys
import numpy as np
import pcbnew
from PIL import Image, ImageDraw
from scipy import ndimage

MM = pcbnew.ToMM
RES = 0.02


def islands(b, x0=106, y0=94, x1=192, y1=129):
    w, h = int((x1 - x0) / RES), int((y1 - y0) / RES)
    px = lambda x, y: ((x - x0) / RES, (y - y0) / RES)
    out = []
    for lname in ("F.Cu", "B.Cu"):
        lid = b.GetLayerID(lname)
        img = Image.new("1", (w, h), 0)
        d = ImageDraw.Draw(img)

        def draw(sps):
            for i in range(sps.OutlineCount()):
                o = sps.Outline(i)
                tmp = Image.new("1", (w, h), 0)
                dt = ImageDraw.Draw(tmp)
                dt.polygon([px(MM(o.CPoint(k).x), MM(o.CPoint(k).y)) for k in range(o.PointCount())], fill=1)
                for j in range(sps.HoleCount(i)):
                    hh = sps.Hole(i, j)
                    dt.polygon([px(MM(hh.CPoint(k).x), MM(hh.CPoint(k).y)) for k in range(hh.PointCount())], fill=0)
                img.paste(1, mask=tmp)
        for z in b.Zones():
            if z.GetNetname() == "GND" and z.IsOnLayer(lid) and not z.GetIsRuleArea():
                draw(z.GetFilledPolysList(lid))
        for t in b.GetTracks():
            if t.GetNetname() == "GND" and t.GetClass() != "PCB_VIA" and t.GetLayer() == lid:
                s = pcbnew.SHAPE_POLY_SET()
                t.TransformShapeToPolygon(s, lid, 0, 5000, pcbnew.ERROR_INSIDE)
                draw(s)
        pads = []
        for f in b.GetFootprints():
            for p in f.Pads():
                if p.GetNetname() == "GND" and p.IsOnLayer(lid):
                    s = pcbnew.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(s, lid, 0, 5000, pcbnew.ERROR_INSIDE)
                    draw(s)
                    pads.append((f.GetReference() + "." + p.GetNumber(), MM(p.GetPosition().x), MM(p.GetPosition().y),
                                 p.GetDrillSize().x > 0))
        a = np.array(img, dtype=bool)
        lab, n = ndimage.label(a, structure=np.ones((3, 3)))
        good = set()
        for t in b.GetTracks():
            if t.GetNetname() == "GND" and t.GetClass() == "PCB_VIA":
                c, r = px(MM(t.GetPosition().x), MM(t.GetPosition().y))
                if 0 <= int(r) < h and 0 <= int(c) < w:
                    good.add(lab[int(r), int(c)])
        bad = {}
        for name, x, y, th in pads:
            c, r = px(x, y)
            l = lab[int(r), int(c)]
            if th:
                good.add(l)
        for name, x, y, th in pads:
            c, r = px(x, y)
            l = lab[int(r), int(c)]
            if l not in good:
                bad.setdefault(l, []).append((name, round(x, 2), round(y, 2)))
        for l, v in bad.items():
            out.append((lname, v))
    return out


if __name__ == "__main__":
    for l, v in islands(pcbnew.LoadBoard(sys.argv[1])):
        print(l, v)
