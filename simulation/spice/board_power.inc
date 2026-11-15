* IWR-AOP-MODULE board-level power, enable and reset network
* Net and reference names follow the KiCad netlist (see simulation/netlist.json).
* Values from the BOM; behavioral parts are marked [BEHAV], guesses are marked [ASSUMED].
*
* Rev 1.1: supervisor U601 on all four rails, R604 not fitted, FB_B2/FB_B3 remote sense
* (R628/C631, R629/C632), FB602 in parallel with FB600, C626/C628 at the buck outputs,
* C204 + R210 damper on USB_VBUS.  Path resistances from results/pdn.json (85 C).
*
* Required .param from the including deck:
*   r604   1e9 (not fitted, default) or 0.001 (option: PGOOD also resets)
*   c605   0 (DNP) or 1u
*   rdis   PMIC discharge resistors active (1/0)
*   rio    radar VIOIN equivalent load while unpowered [ASSUMED]
*   tcp    time after USB VDD good before CP2105 drives TXD high [ASSUMED]

.include lp87524j_behav.lib

* ---------------- Input: USB-C (J200) -> F200 -> USB_VBUS -------------------------
* source and cable are defined in the deck: VUSB_SRC -> RCAB/LCAB -> USB_CONN_VBUS
RF200   USB_CONN_VBUS USB_VBUS 0.03
C100    USB_VBUS 0 1u
C202    USB_VBUS 0 1u
* hot-plug damper: 10 uF 50 V X7R (about 8 uF at 5 V) in series with 0.47 ohm
C204    USB_VBUS c204m 8u
R210    c204m 0 0.47
* carrier 5 V (J400.11/.12), source defined in the deck -> PWR_IN
C140    PWR_IN 0 10u

* ---------------- U117 LM66200 ideal-diode OR [BEHAV] --------------------------------
* 1 ms delay + 1.7 ms soft start (datasheet tD, tSS), 37 mohm, forward only
Cu117t  u117t 0 1
Bu117t  0 u117t I = (max(V(USB_VBUS),V(PWR_IN)) > 1.6 ? 1 : -1e6*V(u117t))
Bu117r  u117r 0 V = max(0, min(6, 5.5*(V(u117t)-1m)/1.7m))
B117a   USB_VBUS SYS_5V I = V(USB_VBUS) >= V(PWR_IN) ? max(0, min(V(USB_VBUS), V(u117r)) - V(SYS_5V))/0.037 : 0
B117b   PWR_IN   SYS_5V I = V(PWR_IN) >  V(USB_VBUS) ? max(0, min(V(PWR_IN),   V(u117r)) - V(SYS_5V))/0.037 : 0

* ---------------- SYS_5V bulk (4 x 22 uF 1210 X7R, ~20 % DC-bias loss at 5 V) --------
C600    SYS_5V s600 17.6u
R600e   s600 0 3m
C601    SYS_5V s601 17.6u
R601e   s601 0 3m
C602    SYS_5V s602 17.6u
R602e   s602 0 3m
C603    SYS_5V s603 17.6u
R603e   s603 0 3m
C604    SYS_5V 0 100n

* ---------------- Enable: R133, Q402, R413, R414 ---------------------------------
R133    SYS_5V PMIC_ENABLE 10k
Q402    PMIC_ENABLE Q402_BASE 0 Q2N3904
R413    HOST_DISABLE Q402_BASE 10k
R414    Q402_BASE 0 100k
.model Q2N3904 NPN(IS=6.734f XTI=3 EG=1.11 VAF=74.03 BF=416.4 NE=1.259 ISE=6.734f IKF=66.78m
+ XTB=1.5 BR=.7371 NC=2 ISC=0 IKR=0 RC=1 CJC=3.638p MJC=.3085 VJC=.75 FC=.5 CJE=4.493p
+ MJE=.2593 VJE=.75 TR=239.5n TF=301.2p ITF=.4 VTF=4 XTF=2 RB=10)

* ---------------- U600 LP87524J-Q1 [BEHAV] ---------------------------------------
* inductors omitted in this timing model (power stage injects into the RAW node)
XU600 SYS_5V PMIC_ENABLE 3V3_R 1V2_R FB_B2 FB_B3 3V3_R 1V2_R 1V0_RAW 1V8_RAW AOP_NRESET PMIC_PGOOD LP87524J
+ params: rdis={rdis}
* remote sense: DC from the radar end, AC from the buck output
R628    1V0_R FB_B2 22
C631    1V0_RAW FB_B2 4.7u
R629    1V8_R FB_B3 22
C632    1V8_RAW FB_B3 4.7u

* ferrites (BLM18KG121TH1D, low-frequency model): FB600 || FB602 on 1.0 V, FB601 on 1.8 V,
* each followed by the extracted copper resistance to the radar balls
RFB600  1V0_RAW fb600m 0.014
LFB600  fb600m fb600o 180n
RCU10   fb600o 1V0_R 0.013
RFB601  1V8_RAW fb601m 0.028
LFB601  fb601m fb601o 360n
RCU18   fb601o 1V8_R 0.040

* ---------------- rail capacitance (lumped per rail, from the netlist) -----------
* 3V3_R : C610 22u, C334 10u, C629 10u, 4 x 100n
C3V3    3V3_R c3v3e 42.4u
R3V3e   c3v3e 0 2m
* 1V2_R : C611 22u, C300 10u, 11 x 100n
C1V2    1V2_R c1v2e 33.1u
R1V2e   c1v2e 0 2m
* 1V0_RAW : C612 22u, C626 22u ; 1V0_R : C312 10u, C620 10u, C624 22u, C341 4.7u, C313 1u, 10 x 100n
C1V0RAW 1V0_RAW c10re 44u
R1V0RAWe c10re 0 2m
C1V0    1V0_R c1v0e 48.7u
R1V0e   c1v0e 0 2m
* 1V8_RAW : C613 22u, C628 22u ; 1V8_R : C320 10u, C621 10u, C625 22u, C321 1u, 13 x 100n
C1V8RAW 1V8_RAW c18re 44u
R1V8RAWe c18re 0 2m
C1V8    1V8_R c1v8e 44.3u
R1V8e   c1v8e 0 2m

* ---------------- radar + flash load [ASSUMED currents] ---------------------------
* running (after AOP_NRESET > 1.57 V) vs held in reset; scales with rail voltage
Brun run_r 0 V = V(AOP_NRESET) > 1.57 ? 1 : 0
Rrunf run_r run 1k
Crunf run 0 1n
* in reset the I/O ring only draws current once the core rails are up
BL3V3   3V3_R 0 I = (V(run) > 0.5 ? 0.040 : 0.010*min(1, max(0, V(1V2_R)/1.2))) * min(1, max(0, V(3V3_R)/3.3))
BL1V2   1V2_R 0 I = (V(run) > 0.5 ? 0.500 : 0.100) * min(1, max(0, V(1V2_R)/1.2))
BL1V0   1V0_R 0 I = (V(run) > 0.5 ? 0.800 : 0.050) * min(1, max(0, V(1V0_R)/1.0))
BL1V8   1V8_R 0 I = (V(run) > 0.5 ? 0.600 : 0.100) * min(1, max(0, V(1V8_R)/1.8))
* unpowered VIOIN equivalent load (only matters for back-power)
RIO     3V3_R 0 {rio}

* ---------------- reset network --------------------------------------------------
R603    3V3_R AOP_NRESET 10k
R604    PMIC_PGOOD AOP_NRESET {r604}
C605    AOP_NRESET 0 {c605}
CNRST   AOP_NRESET 0 30p
R417    HOST_NRESET AOP_NRESET 1k

* ---------------- U601 TPS386000 quad supervisor [BEHAV, SBVS105F] ----------------
* SENSEn falling threshold 0.400 V, 3.5 mV hysteresis; each channel releases its open-drain
* RESETn 20 ms (CTn open) after its rail is good; all four outputs are wired to AOP_NRESET.
* Outputs are valid from VDD = 0.9 V; the 20 ms timers only run above the 1.8 V minimum supply.
* Sense-to-reset delay modelled as a 15 us lag.  Thresholds at the rail:
*   3V3_R 3.067 V (R620/R621), 1V8_RAW 1.653 V (R622/R623), 1V2_R 1.120 V (R624/R625),
*   1V0_RAW 0.900 V (R626/R627)
R620    3V3_R   sup1 100k
R621    sup1 0 15k
R622    1V8_RAW sup2 47k
R623    sup2 0 15k
R624    1V2_R   sup3 27k
R625    sup3 0 15k
R626    1V0_RAW sup4 15k
R627    sup4 0 12k
C606    SYS_5V 0 100n
.subckt SVSCH sense vdd on
Cc c 0 1
Bc 0 c I = (V(sense) > 0.4035 ? 1e6*(1-V(c)) : 0) - (V(sense) < 0.400 ? 1e6*V(c) : 0)
Ct t 0 1
Bt 0 t I = (V(c) > 0.5 && V(vdd) > 1.8) ? 1 : -1e6*V(t)
Bo o 0 V = (V(t) < 20m && V(vdd) > 0.9) ? 1 : 0
Ro o on 1k
Co on 0 15n
.ends
XS1 sup1 SYS_5V svon1 SVSCH
XS2 sup2 SYS_5V svon2 SVSCH
XS3 sup3 SYS_5V svon3 SVSCH
XS4 sup4 SYS_5V svon4 SVSCH
Bsvs AOP_NRESET 0 I = (max(max(V(svon1),V(svon2)),max(V(svon3),V(svon4))) > 0.5) ? V(AOP_NRESET)/100 : 0

* ---------------- CP2105 back-power path (USB only) ------------------------------
* VDD/VIO = USB_3V45 from REGIN; TXD_ECI idles high after tcp
Bvdd    USB_3V45 0 V = max(0, min(3.45, V(USB_VBUS)-0.2))
Ccpt    cpt 0 1
Bcpt    0 cpt I = (V(USB_3V45) > 3.0 ? 1 : -1e6*V(cpt))
Btx     txi 0 V = V(cpt) > {tcp} ? V(USB_3V45) : 0
Rtx     txi USB_UART_TX 50
R208    USB_UART_TX AOP_UART_RX 1k
R416    3V3_R AOP_UART_RX 10k
R314    AOP_UART_RX AOP_UART_RX_BALL 1m
* radar V16 ESD diode to VIOIN (non-failsafe IO, SWRS237C 6.2)
DESD    AOP_UART_RX_BALL 3V3_R DESD
.model DESD D(IS=1e-14 N=1.0 RS=5)
