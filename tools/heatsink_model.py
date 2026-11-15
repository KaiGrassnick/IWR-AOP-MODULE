#!/usr/bin/env python3
"""Generate the STEP model of heat sink HS300.

HS300 is a 14 x 14 x 6 mm aluminium pin-fin BGA heat sink (Same Sky HSB03-141406 or Fischer
ICK BGA 14 x 14 x 6), bonded to the back under the radar (spec section 6).  The model is a
representative shape for renders and carrier fit checks, not a vendor drawing: a 1.5 mm base with
a 7 x 7 grid of 1.3 mm square pins, black anodised.

Origin is the footprint centre with z = 0 on the board surface; KiCad flips the model with the
footprint onto the back.  The base sits 0.6 mm up, the adhesive bond line over the 0402 parts
(0.55 mm tall) under its outline.

Needs CadQuery (pip install cadquery, e.g. in a virtual environment).

Usage: python3 tools/heatsink_model.py [OUTPUT.step]
"""
import pathlib
import sys

import cadquery as cq

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / 'libraries' / 'IWR-AOP-MODULE.3dshapes' / 'HeatSink_PinFin_14x14x6mm.step'

WIDTH = 14.0      # mm, square outline (matches the B.Fab rectangle of HS300)
HEIGHT = 6.0      # mm, base plus pins
BOND = 0.6        # mm, adhesive bond line between board and base
BASE = 1.5        # mm, base plate thickness
PINS = 7          # pins per side
PIN = 1.3         # mm, square pin size
COLOUR = (0.05, 0.05, 0.055)  # black anodised; dark colours map to a satin finish in the renders

out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
pitch = (WIDTH - PIN) / (PINS - 1)
base = cq.Workplane('XY').workplane(offset=BOND).rect(WIDTH, WIDTH).extrude(BASE).edges('|Z').fillet(0.3)
centres = [(-WIDTH / 2 + PIN / 2 + i * pitch, -WIDTH / 2 + PIN / 2 + j * pitch)
           for i in range(PINS) for j in range(PINS)]
pins = (cq.Workplane('XY').workplane(offset=BOND + BASE).pushPoints(centres)
        .rect(PIN, PIN).extrude(HEIGHT - BASE).edges('|Z').fillet(0.15))

assembly = cq.Assembly(name='HS_PinFin_14x14x6')
assembly.add(base.union(pins), name='heatsink', color=cq.Color(*COLOUR))
assembly.export(str(out), exportType='STEP')
print(f'wrote {out}')
