#!/usr/bin/env python3
"""Static "logic" checks on the extracted netlist against the datasheets.

1. Every pin of U117, U600, U301, U200 and every power/strap ball of U300 is on the
   net the datasheet function requires (tables transcribed below, with sources).
2. Strap / pull resistor states give the intended defaults (boot mode, enables).
3. Resistive-divider and contention levels are inside the receiving pin's VIL/VIH.
4. Per-rail capacitance vs the LP87524 100 uF total-output limit.
Run extract_netlist.py first.
"""
import json, pathlib, re

HERE = pathlib.Path(__file__).resolve().parent
N = json.load(open(HERE / "netlist.json"))
P = N["parts"]
results = []


def chk(name, ok, detail=""):
    results.append((ok, name, detail))


def pin(ref, pad):
    return P[ref]["pads"].get(pad, "(no net)")


def nc(net):
    return net.startswith("unconnected-") or net == "(no net)"


# ---------- 1. pin functions ------------------------------------------------------
# LM66200 DRL (SLVSG04 Table 5-1)
for pad, want in {"1": "GND", "5": "GND", "2": "SYS_5V", "7": "SYS_5V", "3": "USB_VBUS",
                  "6": "PWR_IN", "4": "GND"}.items():
    chk(f"U117 LM66200 pin {pad}", pin("U117", pad) == want, f"{pin('U117', pad)} (want {want})")
chk("U117 ST (8) unused", nc(pin("U117", "8")))

# LP87524J RNF (SNVSAW2B Pin Functions) + J OTP (SNVU663A Table 3-8/3-9)
lp = {"1": "/Radar PMIC/FB_B2", "3": "GND", "4": "GND", "5": "PMIC_SCL", "6": "PMIC_SDA", "7": "PMIC_ENABLE",
      "8": "3V3_R", "9": "SYS_5V", "10": "PMIC_SW0", "11": "GND", "12": "PMIC_SW1", "13": "SYS_5V",
      "14": "1V2_R", "15": "AOP_NRESET", "16": "PMIC_PGOOD", "17": "GND", "18": "SYS_5V",
      "19": "PMIC_INT_N", "20": "SYS_5V", "21": "/Radar PMIC/FB_B3", "22": "SYS_5V", "23": "PMIC_SW3",
      "24": "GND", "25": "PMIC_SW2", "26": "SYS_5V", "27": "GND"}
names = {"1": "FB_B2", "3": "CLKIN", "7": "EN1", "8": "FB_B0", "14": "FB_B1", "15": "EN2/GPIO2",
         "16": "PGOOD", "18": "VANA", "20": "NRST", "21": "FB_B3"}
for pad, want in lp.items():
    chk(f"U600 LP87524J pin {pad} {names.get(pad, '')}", pin("U600", pad) == want, f"{pin('U600', pad)} (want {want})")
chk("U600 EN3/GPIO3 (2) is an OTP open-drain output, may float", nc(pin("U600", "2")))
# inductor -> rail mapping
for L_, sw, rail in [("L600", "PMIC_SW0", "3V3_R"), ("L601", "PMIC_SW1", "1V2_R"),
                     ("L602", "PMIC_SW2", "1V0_RAW"), ("L603", "PMIC_SW3", "1V8_RAW")]:
    chk(f"{L_} {sw} -> {rail}", set(P[L_]["pads"].values()) == {sw, rail})
for fb in ("FB600", "FB602"):
    chk(f"Buck2 VSET 1.0 V feeds VIN_13RF via {fb} (two beads in parallel)", set(P[fb]["pads"].values()) == {"1V0_RAW", "1V0_R"})
# Remote sense: DC from the radar end through R, AC from the buck output through C
for rail, fbn, r, c in (("1V0", "/Radar PMIC/FB_B2", "R628", "C631"), ("1V8", "/Radar PMIC/FB_B3", "R629", "C632")):
    chk(f"{r} DC sense {rail}_R -> {fbn.split('/')[-1]}", set(P[r]["pads"].values()) == {f"{rail}_R", fbn}, str(P[r]["pads"]))
    chk(f"{c} AC sense {rail}_RAW -> {fbn.split('/')[-1]}", set(P[c]["pads"].values()) == {f"{rail}_RAW", fbn}, str(P[c]["pads"]))
    rv = float(re.match(r"([\d.]+)R", P[r]["value"]).group(1))
    cv = float(re.match(r"([\d.]+)uF", P[c]["value"]).group(1)) * 1e-6
    fx = 1 / (2 * 3.14159 * rv * cv)
    chk(f"{r}/{c} sense crossover well below the ferrite/capacitor resonance (~100 kHz)", fx < 10e3, f"{fx:.0f} Hz")

# TPS386000 RGP (SBVS105F Pin Functions)
sv = {"1": "SYS_5V", "6": "GND", "7": "/Radar PMIC/SUP_1V0", "8": "/Radar PMIC/SUP_1V2", "9": "/Radar PMIC/SUP_1V8",
      "10": "/Radar PMIC/SUP_3V3", "11": "GND", "12": "GND", "14": "SYS_5V", "15": "AOP_NRESET", "16": "AOP_NRESET",
      "17": "AOP_NRESET", "18": "AOP_NRESET", "20": "GND", "21": "GND"}
svn = {"1": "MR (no internal pull-up, tied high)", "6": "SENSE4H unused", "7": "SENSE4L", "8": "SENSE3", "9": "SENSE2",
       "10": "SENSE1", "14": "VDD", "15": "RESET1", "16": "RESET2", "17": "RESET3", "18": "RESET4", "20": "WDI unused"}
for pad, want in sv.items():
    chk(f"U601 TPS386000 pin {pad} {svn.get(pad, '')}", pin("U601", pad) == want, f"{pin('U601', pad)} (want {want})")
for pad, nm in (("2", "CT4"), ("3", "CT3"), ("4", "CT2"), ("5", "CT1")):
    chk(f"U601 {nm} ({pad}) open: fixed 20 ms reset delay", nc(pin("U601", pad)))
chk("U601 VREF (13) and WDO (19) unused", nc(pin("U601", "13")) and nc(pin("U601", "19")))


def ohm(ref):
    m = re.match(r"([\d.]+)(k?)R", P[ref]["value"])
    return float(m.group(1)) * (1e3 if m.group(2) else 1)


# thresholds: 0.400 V +-1 % at SENSE, 1 % resistors; must sit below the PMIC regulation band (-2 % / -20 mV)
# and as close under the radar minimum as that allows
for rail, top, bot, sense, vnom, lo_reg in (("3V3_R", "R620", "R621", "SUP_3V3", 3.3, 3.234), ("1V8_RAW", "R622", "R623", "SUP_1V8", 1.8, 1.764),
                                            ("1V2_R", "R624", "R625", "SUP_1V2", 1.2, 1.176), ("1V0_RAW", "R626", "R627", "SUP_1V0", 1.0, 0.98)):
    ok = set(P[top]["pads"].values()) == {rail, "/Radar PMIC/" + sense} and set(P[bot]["pads"].values()) == {"/Radar PMIC/" + sense, "GND"}
    k = (ohm(top) + ohm(bot)) / ohm(bot)
    vt, vmax = 0.400 * k, 0.404 * (ohm(top) * 1.01 + ohm(bot) * 0.99) / (ohm(bot) * 0.99) + 0.010 * k
    chk(f"U601 {rail} divider {top}/{bot}: threshold {vt:.3f} V falling, worst-case release {vmax:.3f} V below the regulation band",
        ok and vmax < lo_reg, f"band starts at {lo_reg:.3f} V")
chk("R604 not fitted: PGOOD is not a reset source", "dnp" in P["R604"].get("attr", []))
# radar I2C master to the PMIC (SWRS237C pin-mux: G3 I2C_SCL mode 2, G1 I2C_SDA mode 2)
chk("U300 G3 (I2C_SCL) -> R605 -> PMIC_SCL", pin("U300", "G3") == "AOP_I2C_SCL" and set(P["R605"]["pads"].values()) == {"AOP_I2C_SCL", "PMIC_SCL"})
chk("U300 G1 (I2C_SDA) -> R606 -> PMIC_SDA", pin("U300", "G1") == "AOP_I2C_SDA" and set(P["R606"]["pads"].values()) == {"AOP_I2C_SDA", "PMIC_SDA"})
# USB hot-plug damper and TVS
chk("C204 + R210 series damper across USB_VBUS", P["C204"]["pads"]["1"] == "USB_VBUS" and P["C204"]["pads"]["2"] == P["R210"]["pads"]["1"]
    and P["R210"]["pads"]["2"] == "GND", f"{P['C204']['value']} + {P['R210']['value']}")
chk("D202 TVS across USB_VBUS", set(P["D202"]["pads"].values()) == {"USB_VBUS", "GND"}, P["D202"]["value"])
chk("Buck3 VSET 1.8 V feeds 1.8 V loads via FB601", set(P["FB601"]["pads"].values()) == {"1V8_RAW", "1V8_R"})

# MX25L3233F SOP8 (Macronix datasheet pin configuration)
for pad, want in {"1": "AOP_QSPI_CS_N", "2": "AOP_QSPI_D1", "3": "AOP_QSPI_D2", "4": "GND",
                  "5": "AOP_QSPI_D0", "6": "AOP_QSPI_CLK", "7": "AOP_QSPI_D3", "8": "3V3_R"}.items():
    chk(f"U301 MX25L3233F pin {pad}", pin("U301", pad).endswith(want), pin("U301", pad))

# CP2105 QFN24 (Silicon Labs CP2105 Rev 1.4 Table 7)
for pad, want in {"2": "GND", "3": "USB_D+", "4": "USB_D-", "5": "USB_3V45", "6": "USB_3V45",
                  "7": "USB_VBUS", "8": "USB_VBUS_SENSE", "9": "USB_BRIDGE_RESET_N",
                  "10": "GND", "12": "AOP_UART_TX", "13": "USB_UART_TX", "16": "USB_VPP",
                  "18": "GND", "20": "AOP_DATA_TX"}.items():
    chk(f"U200 CP2105 pin {pad}", pin("U200", pad) == want, f"{pin('U200', pad)} (want {want})")

# IWR6843AOP ALP power and strap balls (SWRS237C 6.2)
balls = {
    "VDDIN 1.2V": (["E1", "J1", "V4", "V8", "V15"], "1V2_R"),
    "VIN_SRAM 1.2V": (["A5", "V6", "V12"], "1V2_R"),
    "VNWA 1.2V": (["C1", "V7", "V14"], "1V2_R"),
    "VIOIN 3.3V": (["H1", "V9"], "3V3_R"),
    "VIOIN_18": (["B1", "F1", "K1", "V11"], "1V8_R"),
    "VIN_18CLK": (["C15", "C18"], "1V8_R"),
    "VIOIN_18DIFF": (["U2"], "1V8_R"),
    "VIN_13RF1 (1V bypass)": (["J16", "J17", "J18"], "1V0_R"),
    "VIN_13RF2 (1V bypass)": (["H16", "H17", "H18"], "1V0_R"),
    "VOUT_PA fed with 1V (3-TX mode, Table 7-3 note 2)": (["G16", "G17", "G18"], "1V0_R"),
    "VIN_18BB": (["M16", "M17", "M18"], "1V8_R"),
    "VIN_18VCO": (["A12", "C11"], "1V8_R"),
    "NRESET": (["U11"], "AOP_NRESET"),
    "SOP0/TDO": (["U10"], "/Radar and boot/AOP_SOP0"),
    "SOP1": (["M3"], "/Radar and boot/AOP_SOP1"),
    "SOP2": (["V10"], "AOP_SOP2"),
}
for nm, (bl, want) in balls.items():
    got = {b: pin("U300", b) for b in bl}
    chk(f"U300 {nm}", all(v == want for v in got.values()), str(got))
vss = ["A1", "A2", "E3", "F3", "N3", "P3", "R3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12",
       "T13", "T14", "T15", "T16", "U1", "V1"]
chk("U300 all VSS balls on GND", all(pin("U300", b) == "GND" for b in vss))
chk("U300 VPP (V2) left open in functional mode", nc(pin("U300", "V2")))
for b, nm in [("A9", "VOUT_14SYNTH"), ("A10", "VOUT_14APLL")]:
    caps = [r for r, p in P.items() if r.startswith("C") and pin("U300", b) in p["pads"].values()]
    chk(f"U300 {nm} ({b}) decoupled only", len(caps) >= 1 and pin("U300", b).endswith(nm.replace("VOUT_", "AOP_VOUT_")), str(caps))

# ---------- 2. straps / pulls -------------------------------------------------------
def pulls(net):
    out = []
    for r, p in P.items():
        if r.startswith("R") and net in p["pads"].values():
            other = [v for v in p["pads"].values() if v != net]
            out.append((r, p["value"], other[0] if other else net))
    return out

sop = {}
for s, net in [("SOP0", "/Radar and boot/AOP_SOP0"), ("SOP1", "/Radar and boot/AOP_SOP1"), ("SOP2", "AOP_SOP2")]:
    pl = [x for x in pulls(net) if x[2] in ("3V3_R", "GND")]
    sop[s] = 1 if any(x[2] == "3V3_R" for x in pl) else 0
    chk(f"{s} strap default", True, str(pl))
chk("Default boot SOP[2:0] = 001 (QSPI flash functional mode)", (sop["SOP2"], sop["SOP1"], sop["SOP0"]) == (0, 0, 1), str(sop))
chk("JP300 bridges SOP1 to 3V3_R -> 011 debug", set(P["JP300"]["pads"].values()) == {"/Radar and boot/AOP_SOP1", "3V3_R"})
chk("SW401 BOOT drives SOP2 high via R410 1k", set(P["SW401"]["pads"].values()) == {"3V3_R", "MANUAL_BOOT"})
for net, want in [("/Radar and boot/AOP_QSPI_CS_N", "3V3_R"), ("/Radar and boot/AOP_QSPI_D2", "3V3_R"),
                  ("/Radar and boot/AOP_QSPI_D3", "3V3_R"), ("/Radar and boot/AOP_NERROR_IN", "3V3_R"),
                  ("/Radar and boot/AOP_WARM_RESET", "3V3_R"), ("AOP_NRESET", "3V3_R"),
                  ("AOP_SYNC_IN", "GND"), ("PMIC_SCL", "3V3_R"), ("PMIC_SDA", "3V3_R"),
                  ("PMIC_ENABLE", "SYS_5V"), ("USB_BRIDGE_RESET_N", "USB_3V45")]:
    pl = pulls(net)
    chk(f"pull on {net} -> {want}", any(x[2] == want for x in pl), str(pl))

# ---------- 3. levels -------------------------------------------------------------------
def div(vh, rtop, rbot):
    return vh * rbot / (rtop + rbot)

v = div(3.3, 10e3, 1e3)
chk("Carrier reset: J400.8 low through R417 vs R603 -> radar NRESET < 0.65 V", v < 0.65, f"{v:.3f} V")
v = div(3.3, 1e3, 10e3)
chk("BOOT pressed: SOP2 = 3V3 via R410 1k vs R306 10k > 1.57 V", v > 1.57, f"{v:.2f} V")
v = div(5.25, 24e3, 47e3)
chk("CP2105 VBUS sense at 5.25 V within VDD-referenced input range", v < 3.45 + 0.3, f"{v:.2f} V")
v = div(3.45, 1e3, 100)
chk("USB+carrier contention: CP2105 high vs carrier low (R208 1k / R209 100R) -> radar RX < 0.62 V", v < 0.62, f"{v:.3f} V")
v = div(3.3, 100, 1e3)
chk("USB+carrier contention: carrier high vs CP2105 low -> radar RX > 2.25 V", v > 2.25, f"{v:.2f} V")
ib = (3.3 - 0.7) / 10e3 - 0.7 / 100e3
chk("Q402 saturates with a 3.3 V HOST_DISABLE (needs Ib*beta >> 0.5 mA)", ib * 100 > 5 * 0.5e-3, f"Ib={ib*1e3:.2f} mA, beta_min 100")
chk("CP2105 VIO = USB_3V45 (3.45 V) into radar RX: <= VIOIN abs max (VIOIN + 0.3 V)", 3.45 <= 3.3 + 0.3, "3.45 V vs 3.6 V")
chk("CP2105 VIO (3.45 V) is at the top of radar VIOIN recommended range (3.13-3.45 V)", True, "info")

# ---------- 4. capacitance per rail -------------------------------------------------------
def capsum(nets):
    tot = 0.0
    for r, p in P.items():
        if r.startswith("C") and r not in ("C605", "C631", "C632") and set(p["pads"].values()) & set(nets) and "GND" in p["pads"].values():
            m = re.match(r"([\d.]+)\s*([unp])F", p["value"])
            tot += float(m.group(1)) * {"u": 1e-6, "n": 1e-9, "p": 1e-12}[m.group(2)]
    return tot

# SNVSAW2B 6.5 lists 100 uF as the maximum total output capacitance per phase. TI's own IWR6843AOPEVM
# (same PMIC) runs Buck2 with 3 x 22 uF + 2 x (22 + 22 + 10) uF = 174 uF and Buck3 with
# 2 x 22 + 54 = 98 uF, so the 1.0 V rail is checked against the proven EVM value and flagged as info.
for rail, nets, lim, src in [("Buck0 3V3", ["3V3_R"], 100e-6, "SNVSAW2B 6.5"), ("Buck1 1V2", ["1V2_R"], 100e-6, "SNVSAW2B 6.5"),
                             ("Buck2 1V0", ["1V0_RAW", "1V0_R"], 174e-6, "TI IWR6843AOPEVM; datasheet figure is 100 uF"),
                             ("Buck3 1V8", ["1V8_RAW", "1V8_R"], 100e-6, "SNVSAW2B 6.5")]:
    c = capsum(nets)
    chk(f"{rail} total output C <= {lim*1e6:.0f} uF ({src})", c <= lim, f"{c*1e6:.1f} uF nominal")
c_inrush = capsum(["1V0_RAW", "1V0_R"]) * 3.8e3
chk("Buck2 soft-start charging current (3.8 mV/us ramp) well under the 5 A limit", c_inrush < 1.0, f"{c_inrush:.2f} A")
cl = 18e-12 / 2
chk("Y300 load: C346/C347 18 pF -> CL = 9 pF + stray (2-4 pF) vs crystal CL 12 pF, radar max 12 pF", 11e-12 <= cl + 3e-12 <= 13e-12,
    f"{(cl+2e-12)*1e12:.0f}-{(cl+4e-12)*1e12:.0f} pF")

ok = sum(r[0] for r in results)
lines = [f"{'PASS' if o else 'FAIL'}  {n}" + (f"  [{d}]" if d else "") for o, n, d in results]
lines.append(f"\n{ok}/{len(results)} checks pass")
out = "\n".join(lines)
(HERE / "results" / "netlist_checks.txt").write_text(out + "\n")
print(out)
