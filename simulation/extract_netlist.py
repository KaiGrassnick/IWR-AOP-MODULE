#!/usr/bin/env python3
"""Extract the as-routed netlist (ref, value, LCSC, pad -> net) from the KiCad 10 board.
KiCad 10 is not needed; DRC schematic parity is 0, so board nets equal schematic nets."""
import sys,json,collections
import pathlib
HERE=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from sexp import *
pcb=parse(open(HERE.parent/'IWR-AOP-MODULE.kicad_pcb').read())
parts={}; nets=collections.defaultdict(list)
for fp in find(pcb,'footprint'):
    props={p[1]:p[2] for p in find(fp,'property')}
    ref=props.get('Reference','?')
    attrs=get(fp,'attr')
    pads={}
    for pad in find(fp,'pad'):
        n=get(pad,'net'); num=pad[1]
        if n: pads.setdefault(num,n[1] if len(n)==2 else n[2])
        if n: nets[n[1] if len(n)==2 else n[2]].append(f"{ref}.{num}")
    parts[ref]={'value':props.get('Value'),'fp':fp[1],'props':{k:v for k,v in props.items() if k not in('Reference','Datasheet','Description')},'attr':attrs[1:] if attrs else [],'pads':pads}
json.dump({'parts':parts,'nets':{k:sorted(set(v)) for k,v in nets.items()}},open(HERE/'netlist.json','w'),indent=1)
print(len(parts),'parts',len(nets),'nets')
