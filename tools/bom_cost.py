#!/usr/bin/env python3
"""Rebuild docs/bom-cost.csv from the manufacturing BOM and current LCSC price breaks.

Reads manufacturing/jlcpcb/bom-source.csv (written by manufacturing/build_package.py), merges lines
by LCSC number as the assembly BOM does, and looks each part up on the public jlcsearch mirror of
the JLCPCB parts catalogue.  Prices are LCSC list prices at the quantity one order of 1, 10 or 30
boards needs; JLC's assembly quote adds its own fees.  Do-not-populate parts are not in the source.

Usage: python3 tools/bom_cost.py
"""
import csv
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "manufacturing" / "jlcpcb" / "bom-source.csv"
OUT = ROOT / "docs" / "bom-cost.csv"
API = "https://jlcsearch.tscircuit.com"
BOARDS = (1, 10, 30)


def get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bom-cost"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (attempt + 1))
    sys.exit(f"{url}: {err}")


def lookup(lcsc):
    n = int(lcsc[1:])
    full = [c for c in get(f"{API}/components/list.json?search={lcsc}&full=true")["components"] if c["lcsc"] == n]
    brief = [c for c in get(f"{API}/api/search?q={lcsc}&limit=5")["components"] if c["lcsc"] == n]
    if not full and not brief:
        sys.exit(f"{lcsc}: not found")
    c = (full or brief)[0]
    breaks = []
    if isinstance(c.get("price"), str):
        for part in c["price"].split(","):
            rng, p = part.split(":")
            breaks.append((int(rng.split("-")[0]), float(p)))
    elif c.get("price") is not None:
        breaks.append((1, float(c["price"])))
    if not breaks and brief and brief[0].get("price") is not None:
        breaks.append((1, float(brief[0]["price"])))
    stock = next((x["stock"] for x in full + brief if x.get("stock") is not None), None)
    lib = "basic" if c.get("is_basic") else "preferred" if c.get("is_preferred") else "extended"
    return breaks, stock, lib


def unit(breaks, qty):
    return [p for q, p in breaks if q <= max(qty, breaks[0][0])][-1]


def main():
    lines = {}
    for r in csv.DictReader(SRC.open()):
        if not r["LCSC"]:
            continue
        e = lines.setdefault(r["LCSC"], dict(refs=[], qty=0, mpn=r["MPN"], mfr=r["Manufacturer"]))
        e["refs"] += r["Designator"].split(",")
        e["qty"] += int(r["Qty"])
    rows, total, n = [], [0.0] * len(BOARDS), 0
    for lcsc, e in lines.items():
        breaks, stock, lib = lookup(lcsc)
        cost = [e["qty"] * unit(breaks, e["qty"] * b) for b in BOARDS]
        total = [t + c for t, c in zip(total, cost)]
        n += e["qty"]
        rows.append((cost[0], [",".join(e["refs"]), e["qty"], e["mpn"], e["mfr"], lcsc, lib, "" if stock is None else stock]
                     + [f"{c:.3f}" for c in cost]))
        time.sleep(0.3)
    rows.sort(key=lambda r: -r[0])
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Qty/board", "Part", "Manufacturer", "LCSC", "JLC library", "Stock"]
                   + [f"Line USD @{b} boards" for b in BOARDS])
        for _, r in rows:
            w.writerow(r)
        w.writerow(["TOTAL per board", n, "", "", "", "", ""] + [f"{t:.2f}" for t in total])
    print(f"{len(rows)} lines, {n} parts, per board: " + ", ".join(f"${t:.2f} @{b}" for t, b in zip(total, BOARDS)))


if __name__ == "__main__":
    main()
