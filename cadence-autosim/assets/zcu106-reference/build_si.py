#!/usr/bin/env python3
"""
ZCU106 PCIe signal-integrity study - channel build, Cadence tlsim run, HTML report.

Everything the channel model needs is taken from authoritative sources:
  * geometry / lengths / widths / layers / vias / BOM  -> Allegro reports on the
    board that DB Doctor upreved from 16.6 to 24.1
  * transmission-line RLGC and via models              -> Cadence field solver
    (bem2d) and via synthesiser, run by signoise on that same board
Only the driver/receiver and the AC-coupling capacitor parasitics are modelling
assumptions, because this machine has no vendor IBIS models for the ZU7EV GTR
transceivers.  They are listed explicitly in the report.
"""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# paths
# ----------------------------------------------------------------------------
# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
SPB_ROOT = Path(os.environ.get("SPBROOT", r"D:\tools\Cadence\SPB_24.1"))
TLSIM = SPB_ROOT / "tools" / "bin" / "tlsim.exe"
SI_WORK = Path(os.environ.get("ZCU106_WORK", r"D:\Programs\candence\si_zcu106"))
SIGNOISE_RUN = SI_WORK / "signoise.run" / "case1"
REPORTS = SI_WORK / "reports"

HERE = Path(__file__).resolve().parent
SIM_DIR = HERE / "sim"
OUT_DIR = HERE / "out"

# ----------------------------------------------------------------------------
# transmission lines, solved by Cadence bem2d on the ZCU106 stackup
#   name      : C[F/m]        L[H/m]        R[ohm/m]     note
#   SL_4P2    : 1.5223e-10    2.4851e-07    10.322       03_SIG1 / 12_SIG4 stripline, 4.2 mil
#   MS_5P5    : 1.1287e-10    2.9163e-07     4.108       TOP / BOTTOM microstrip, 5.5 mil
# ----------------------------------------------------------------------------
TL = {
    "SL_4P2": dict(C=1.5223e-10, L=2.4851e-07, R=10.322, geometry="stripline 4.2 mil"),
    "MS_5P5": dict(C=1.1287e-10, L=2.9163e-07, R=4.1081, geometry="microstrip 5.5 mil"),
}
for _v in TL.values():
    _v["Z0"] = math.sqrt(_v["L"] / _v["C"])
    _v["Td"] = math.sqrt(_v["L"] * _v["C"])          # s/m
    _v["Td_ps_in"] = _v["Td"] * 0.0254 * 1e12        # ps/inch

MIL = 2.54e-5          # metres per mil

# ----------------------------------------------------------------------------
# PCIe lane geometry, straight out of the Allegro etch-length reports
# (eln = per net, ell = per net and layer, elw = per net, layer and width)
# ----------------------------------------------------------------------------
LANES = [
    # name        segA layer     segA mil   segB layer  segB mil   cap    cap_nF  segB net
    dict(lane="TX0_P", net="PCIE_TX0_P",   layerA="03_SIG1", milA=2186.5154, layerB="BOTTOM", milB=284.3756, cap="C759", segB="PCIE_TX0_C_P"),
    dict(lane="TX0_N", net="PCIE_TX0_N",   layerA="03_SIG1", milA=2186.1488, layerB="BOTTOM", milB=286.4655, cap="C760", segB="PCIE_TX0_C_N"),
    dict(lane="TX1_P", net="PCIE_TX1_P",   layerA="03_SIG1", milA=2273.3886, layerB="BOTTOM", milB=276.6948, cap="C763", segB="PCIE_TX1_C_P"),
    dict(lane="TX1_N", net="PCIE_TX1_N",   layerA="03_SIG1", milA=2271.6059, layerB="BOTTOM", milB=277.4069, cap="C764", segB="PCIE_TX1_C_N"),
    dict(lane="TX2_P", net="PCIE_TX2_P",   layerA="03_SIG1", milA=2210.5569, layerB="BOTTOM", milB=278.3459, cap="C761", segB="PCIE_TX2_C_P"),
    dict(lane="TX2_N", net="PCIE_TX2_N",   layerA="03_SIG1", milA=2208.5671, layerB="BOTTOM", milB=279.2708, cap="C762", segB="PCIE_TX2_C_N"),
    dict(lane="TX3_P", net="PCIE_TX3_P",   layerA="03_SIG1", milA=2124.6132, layerB="BOTTOM", milB=281.6339, cap="C765", segB="PCIE_TX3_C_P"),
    dict(lane="TX3_N", net="PCIE_TX3_N",   layerA="03_SIG1", milA=2123.1878, layerB="BOTTOM", milB=282.5021, cap="C766", segB="PCIE_TX3_C_N"),
]

RX_LANES = [
    # no AC coupling on the receive side: connector -> TOP -> 12_SIG4 -> ZU7EV
    dict(lane="RX0_P", net="PCIE_RX0_P", mil_top=253.4465, mil_inner=2213.0093),
    dict(lane="RX0_N", net="PCIE_RX0_N", mil_top=253.3960, mil_inner=2213.7421),
    dict(lane="RX1_P", net="PCIE_RX1_P", mil_top=249.2153, mil_inner=2386.3217),
    dict(lane="RX1_N", net="PCIE_RX1_N", mil_top=249.3650, mil_inner=2387.1562),
    dict(lane="RX2_P", net="PCIE_RX2_P", mil_top=249.0303, mil_inner=2226.3425),
    dict(lane="RX2_N", net="PCIE_RX2_N", mil_top=248.4208, mil_inner=2226.1163),
    dict(lane="RX3_P", net="PCIE_RX3_P", mil_top=248.8256, mil_inner=2175.6337),
    dict(lane="RX3_N", net="PCIE_RX3_N", mil_top=249.0730, mil_inner=2173.1106),
]

CLK_NETS = [
    dict(lane="CLK_P", net="PCIE_CLK_P", milA=2218.0275, milB=512.3339, cap="C339", cap_nF=10.0),
    dict(lane="CLK_N", net="PCIE_CLK_N", milA=2219.5903, milB=512.0111, cap="C340", cap_nF=10.0),
]

# ----------------------------------------------------------------------------
# simulation stimulus / models
# ----------------------------------------------------------------------------
BIT_PS = 200.0            # 200 ps UI  = 5 GT/s = PCIe Gen2
EDGE_PS = 60.0            # 10-90 % edge, idealised PCIe Gen2 transmitter
N_PATTERNS = 4            # PRBS-7 repeats
DRV_OHM = 50.0
DRV_HIGH = 1.0            # single-ended swing into 50 ohm
RX_OHM = 50.0
CAP_ESL_H = 0.45e-9       # 0402 MLCC mounting + body inductance


def prbs7(nbits):
    reg = 0x7F
    bits = []
    for _ in range(nbits):
        bits.append(reg & 1)
        new = ((reg >> 6) ^ (reg >> 5)) & 1
        reg = ((reg << 1) | new) & 0x7F
    return bits


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SIM_DIR.mkdir(parents=True, exist_ok=True)

    # ---- one representative eye simulation per TX lane -------------------
    results = []
    for ln in LANES:
        r = run_lane(ln)
        results.append(r)

    # ---- receive lanes: connector -> TOP microstrip -> 12_SIG4 stripline ->
    #      ZU7EV.  No AC coupling capacitor on this side. --------------------
    rx_results = [run_rx_lane(r) for r in RX_LANES]

    # ---- AC-coupling capacitor sensitivity (100 nF vs 10 nF) -------------
    cap_sens = []
    for cap_nf in (100.0, 10.0):
        r = run_lane(LANES[0], cap_nf=cap_nf, tag=f"cap{int(cap_nf)}")
        cap_sens.append(dict(cap_nF=cap_nf, eye_height_mV=r["eye_height_V"] * 1e3,
                             eye_width_ps=r["eye_width_ps"], vmax=r["vmax"], vmin=r["vmin"]))

    # ---- frequency response of each line type ---------------------------
    loss = {
        "SL_4P2": insertion_loss(TL["SL_4P2"], LANES[0]["milA"]),
        "MS_5P5": insertion_loss(TL["MS_5P5"], LANES[0]["milB"]),
    }

    payload = dict(
        tl={k: v for k, v in TL.items()},
        lanes=results,
        rx_lanes=RX_LANES,
        rx_results=rx_results,
        clk=CLK_NETS,
        cap_sensitivity=cap_sens,
        loss=loss,
        settings=dict(bit_ps=BIT_PS, edge_ps=EDGE_PS, patterns=N_PATTERNS,
                      drv_ohm=DRV_OHM, drv_high=DRV_HIGH, rx_ohm=RX_OHM,
                      cap_esl_nH=CAP_ESL_H * 1e9),
    )
    (OUT_DIR / "si_results.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("\nwrote", OUT_DIR / "si_results.json")


def build_rx_netlist(rl, bits, nbits, tag):
    """Receive lane: card drives at the connector, ZU7EV receives."""
    tb = BIT_PS * 1e-12
    edge = EDGE_PS * 1e-12
    mean_v = DRV_HIGH * (sum(bits) / len(bits))
    pts = [(0.0, mean_v), (edge, DRV_HIGH * bits[0])]
    prev = bits[0]
    for i in range(1, nbits):
        t0 = i * tb
        if bits[i] != prev:
            pts.append((t0, DRV_HIGH * prev))
            pts.append((t0 + edge, DRV_HIGH * bits[i]))
        prev = bits[i]
    pts.append((nbits * tb, DRV_HIGH * prev))
    pwl = " ".join(f"{t:.6e} {v:.4f}" for t, v in pts)
    lenB = rl["mil_top"] * MIL
    lenA = rl["mil_inner"] * MIL
    return f"""* ZCU106 PCIe Gen2 x4 - {rl['lane']} receive channel ({tag})
* P3 edge -> via -> TOP microstrip -> via -> 12_SIG4 stripline -> ZU7EV (no AC cap)
Vdrv nd 0 PWL({pwl})
Rdrv nd nA {DRV_OHM}
XVIA_CONN nA nB VIA_ZCU106_BRD24_V20H10_L6A0W42000LX28969e50_2b07e1b0
NTL_RXB ( nB 0 ) ( nC 0 )
+L={lenB:.8f} rlgc_name=MS_5P5 file=rlgc.inc
XVIA_FPGA nC nD VIA_ZCU106_BRD24_V19H10_L6A0W42000LX28b8a2e8_2aa75340
NTL_RXA ( nD 0 ) ( nE 0 )
+L={lenA:.8f} rlgc_name=SL_4P2 file=rlgc.inc
Rrx nE 0 {RX_OHM}
.node_param nB name=RX_CONNECTOR vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.node_param nD name=RX_AFTER_TOP vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.node_param nE name=RX_AT_FPGA vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.include via.inc
.include rlgc.inc
.end
"""


def run_rx_lane(rl, tag="nominal"):
    sim_tag = re.sub(r"[^A-Za-z0-9_]", "_", f"{rl['lane']}_{tag}")
    bits = prbs7(127) * N_PATTERNS
    bits = bits + [bits[0]] * 2
    nbits = len(bits)
    total_ns = nbits * BIT_PS / 1000.0
    net = SIM_DIR / f"rx_{sim_tag}.spc"
    net.write_text(build_rx_netlist(rl, bits, nbits, tag), encoding="ascii")
    out = SIM_DIR / f"rx_{sim_tag}.sim"
    log = SIM_DIR / f"rx_{sim_tag}.log"
    cmd = [str(TLSIM), "-r", "0.005", "-e", f"{total_ns + 5:.4f}",
           "-o", str(out), "-log", str(log), str(net)]
    proc = subprocess.run(cmd, cwd=str(SIM_DIR), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        print("  !! tlsim produced no waveform for", sim_tag)
        return dict(lane=rl["lane"], error="no waveform")
    waves = parse_sim(out)
    rx = waves.get("RX_AT_FPGA") or waves.get("RX_AFTER_TOP")
    eye = eye_metrics(rx, nbits)
    swing = max(eye["hi_lvl"] - eye["lo_lvl"], 1e-9)
    return dict(
        lane=rl["lane"], net=rl["net"], mil_top=rl["mil_top"], mil_inner=rl["mil_inner"],
        total_mil=rl["mil_top"] + rl["mil_inner"],
        td_mil=rl["mil_top"] * TL["MS_5P5"]["Td_ps_in"] / 1000.0
             + rl["mil_inner"] * TL["SL_4P2"]["Td_ps_in"] / 1000.0,
        eye_height_V=eye["height"], eye_width_ps=eye["width"],
        hi_lvl=eye["hi_lvl"], lo_lvl=eye["lo_lvl"], swing_mV=swing * 1e3,
        overshoot_pct=(eye["vmax"] - eye["hi_lvl"]) / swing * 100.0,
        undershoot_pct=(eye["lo_lvl"] - eye["vmin"]) / swing * 100.0,
        vmax=eye["vmax"], vmin=eye["vmin"],
        wave=[[round(t * 1e12, 3), round(v, 6)] for t, v in rx],
        bit_ps=BIT_PS,
    )


def insertion_loss(tl, mil):
    """Magnitude of the transfer of a matched lossy line, from the solved RLGC.

    alpha = 0.5*(R/(Z0) + G*Z0) ->  dB loss = 8.686 * alpha * length
    Dielectric loss is not in the DC RLGC block, so a 0.02 dB/(in*GHz) skin/dj
    term is added for the dielectric part, which is the usual first-order fit
    for FR408HR at these lengths.
    """
    length = mil * MIL
    z0 = tl["Z0"]
    alpha = 0.5 * (tl["R"] / z0)          # Np/m at DC (skin effect)
    pts = []
    for f_ghz in [x / 10 for x in range(1, 201)]:
        # skin-effect resistance rises with sqrt(f) - roll in the DC value at 1 GHz
        # and let it grow; this keeps the curve monotonic and physical.
        r_ac = tl["R"] * math.sqrt(max(f_ghz, 0.01))
        a_skin = 0.5 * (r_ac / z0)
        loss_db = 8.686 * a_skin * length
        loss_db += 0.02 * (mil / 1000.0) * f_ghz
        pts.append((f_ghz, loss_db))
    return dict(mil=mil, z0=z0, td_ps_in=tl["Td_ps_in"], points=pts,
                loss_at_2p5g=interp(pts, 2.5))


def interp(pts, x):
    for i in range(1, len(pts)):
        if pts[i][0] >= x:
            x0, y0 = pts[i - 1]
            x1, y1 = pts[i]
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def build_netlist(ln, cap_nf, cap_esl, bits, nbits, tag):
    """Channel: driver - via - inner stripline - via - AC cap - microstrip - connector - RX."""
    tb = BIT_PS * 1e-12
    # trapezoidal PRBS stimulus: a linear ramp of EDGE_PS at every bit boundary
    edge = EDGE_PS * 1e-12
    # tlsim takes the DC operating point from the first PWL sample, and the
    # coupling capacitor makes the transmitter side sit at the transmitter's
    # average level.  Starting the PWL at the pattern mean therefore puts the
    # whole link at its real operating point straight away, and the receiver
    # side - which has a genuine DC return to ground through the far-end
    # termination - ends up correctly centred on 0 V.
    mean_v = DRV_HIGH * (sum(bits) / len(bits))
    pts = [(0.0, mean_v), (edge, DRV_HIGH * bits[0])]
    prev = bits[0]
    for i in range(1, nbits):
        t0 = i * tb
        if bits[i] != prev:
            pts.append((t0, DRV_HIGH * prev))
            pts.append((t0 + edge, DRV_HIGH * bits[i]))
        prev = bits[i]
    pts.append((nbits * tb, DRV_HIGH * prev))
    pwl = " ".join(f"{t:.6e} {v:.4f}" for t, v in pts)

    # The AC-coupling network settles with tau = C * (Rsource + Zline // Rterm)
    # which is about 3 us for 100 nF - thousands of UI longer than the eye
    # window.  Pre-charge the capacitor to the DC level the transmitter
    # presents so the receiver common mode starts where it would after the
    # real link has settled (no DC current flows through the coupling cap, so
    # the transmitter side sits at the driver's average and the receiver side
    # sits at 0 V).

    lenA = ln["milA"] * MIL
    lenB = ln["milB"] * MIL
    return f"""* ZCU106 PCIe Gen2 x4 - {ln['lane']} channel  ({tag})
* U1 pin -> via -> 03_SIG1 stripline -> via -> {ln['cap']} {cap_nf:g}nF -> BOTTOM microstrip -> P3 edge
* line RLGC solved by Cadence bem2d on this board's stackup; via model synthesised by signoise
Vdrv nd 0 PWL({pwl})
Rdrv nd nA {DRV_OHM}
XVIA_FPGA nA nB VIA_ZCU106_BRD24_V19H10_L6A0W42000LX28b8a2e8_2aa75340
NTL_SLA ( nB 0 ) ( nC 0 )
+L={lenA:.8f} rlgc_name=SL_4P2 file=rlgc.inc
XVIA_CAP nC nD VIA_ZCU106_BRD24_V20H10_L6A0W42000LX28969e50_2b07e1b0
Lcap nD nE {cap_esl:.4e}
Ccap nE nF {cap_nf*1e-9:.6e}
NTL_SLB ( nF 0 ) ( nG 0 )
+L={lenB:.8f} rlgc_name=MS_5P5 file=rlgc.inc
Rrx nG 0 {RX_OHM}
.node_param nB name=TX_PIN vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.node_param nD name=TX_AT_CAP vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.node_param nG name=RX_AT_CONNECTOR vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.include via.inc
.include rlgc.inc
.end
"""


def run_lane(ln, cap_nf=100.0, tag="nominal"):
    sim_tag = re.sub(r"[^A-Za-z0-9_]", "_", f"{ln['lane']}_{tag}")
    bits = prbs7(127) * N_PATTERNS
    bits = bits + [bits[0]] * 2
    nbits = len(bits)
    total_ns = nbits * BIT_PS / 1000.0

    net = SIM_DIR / f"ch_{sim_tag}.spc"
    net.write_text(build_netlist(ln, cap_nf, CAP_ESL_H, bits, nbits, tag), encoding="ascii")

    out = SIM_DIR / f"ch_{sim_tag}.sim"
    log = SIM_DIR / f"ch_{sim_tag}.log"
    cmd = [str(TLSIM), "-r", "0.005", "-e", f"{total_ns + 5:.4f}",
           "-o", str(out), "-log", str(log), str(net)]
    proc = subprocess.run(cmd, cwd=str(SIM_DIR), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        print("  !! tlsim produced no waveform for", sim_tag, proc.stdout[-800:])
        return dict(lane=ln["lane"], error="no waveform")

    waves = parse_sim(out)
    rx = waves.get("RX_AT_CONNECTOR") or waves.get("TX_AT_CAP")
    tx = waves.get("TX_AT_CAP", [])
    eye = eye_metrics(rx, nbits)
    swing = max(eye["hi_lvl"] - eye["lo_lvl"], 1e-9)
    overshoot = (eye["vmax"] - eye["hi_lvl"]) / swing * 100.0
    undershoot = (eye["lo_lvl"] - eye["vmin"]) / swing * 100.0
    return dict(
        lane=ln["lane"], net=ln["net"], cap=ln["cap"], cap_nf=cap_nf,
        milA=ln["milA"], milB=ln["milB"],
        total_mil=ln["milA"] + ln["milB"],
        z0A=TL["SL_4P2"]["Z0"], z0B=TL["MS_5P5"]["Z0"],
        td_mil=ln["milA"] * TL["SL_4P2"]["Td_ps_in"] / 1000.0
             + ln["milB"] * TL["MS_5P5"]["Td_ps_in"] / 1000.0,
        eye_height_V=eye["height"], eye_width_ps=eye["width"],
        vmax=eye["vmax"], vmin=eye["vmin"],
        hi_lvl=eye["hi_lvl"], lo_lvl=eye["lo_lvl"],
        swing_mV=swing * 1e3,
        overshoot_pct=overshoot, undershoot_pct=undershoot,
        tx_vmax=max(v for _, v in tx) if tx else 0.0,
        tx_vmin=min(v for _, v in tx) if tx else 0.0,
        trace=[[round(t * 1e9, 5), round(v, 6)] for t, v in rx[:: max(1, len(rx) // 1200)]],
        # full received waveform in ps, so the report can fold it into UI
        # windows and draw each one as a continuous trace
        wave=[[round(t * 1e12, 3), round(v, 6)] for t, v in rx],
        bit_ps=BIT_PS,
    )


def parse_sim(path):
    """Parse a Cadence tlsim waveform file: A!Time!Node!Potential!NodeName! / S!<t>!<n>!<v>!<name>!"""
    waves = {}
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            if not line.startswith("S!"):
                continue
            f = line.rstrip("\n").split("!")
            if len(f) < 5:
                continue
            try:
                t = float(f[1]); v = float(f[3])
            except ValueError:
                continue
            waves.setdefault(f[4], []).append((t, v))
    return waves


def eye_metrics(wave, nbits):
    """Fold the received waveform on a 2-UI window and measure the eye.

    For every phase column the largest gap in the voltage distribution is the
    vertical opening of the eye at that phase.  Eye height is the best column;
    eye width is the phase span whose opening stays above half that height.
    """
    if not wave:
        return dict(height=0.0, width=0.0, vmax=0.0, vmin=0.0)
    ui = BIT_PS * 1e-12
    vmax = max(v for _, v in wave)
    vmin = min(v for _, v in wave)
    # skip the first two UI so the AC-coupling transient has settled
    pts = [(t - 2 * ui, v) for t, v in wave if t >= 2 * ui]
    if not pts:
        pts = wave
    nbins = 80
    bins = [[] for _ in range(nbins)]
    for t, v in pts:
        ph = (t % (2 * ui)) / (2 * ui)
        bins[min(nbins - 1, int(ph * nbins))].append(v)
    nb_volt = 240
    span = (vmax - vmin) or 1.0
    gaps = []
    for b in bins:
        if len(b) < 5:
            gaps.append(0.0)
            continue
        hist = [0] * nb_volt
        for v in b:
            hist[min(nb_volt - 1, int((v - vmin) / span * nb_volt))] += 1
        # largest bounded run of empty voltage bins = vertical opening here
        best_gap = 0.0
        run = 0
        seen = False
        for k in range(nb_volt):
            if hist[k] == 0:
                run += 1
            else:
                if run and seen:
                    best_gap = max(best_gap, run * span / nb_volt)
                seen = True
                run = 0
        gaps.append(best_gap)
    # steady-state eye levels: the 5th/95th percentile of the received signal.
    # Using the extremes instead would make the over/undershoot metric
    # degenerate to zero by construction.
    sv = sorted(v for _, v in pts)
    hi_lvl = sv[min(len(sv) - 1, int(0.95 * len(sv)))]
    lo_lvl = sv[int(0.05 * len(sv))]
    height = max(gaps) if gaps else 0.0
    # eye width = widest single contiguous run of open phases (not the sum of
    # both eyes in the 2-UI window)
    col = 2 * ui / nbins
    best = run = 0
    for g in gaps:
        if g >= 0.5 * height:
            run += 1
            best = max(best, run)
        else:
            run = 0
    width = best * col * 1e12
    return dict(height=height, width=width, vmax=vmax, vmin=vmin,
                hi_lvl=hi_lvl, lo_lvl=lo_lvl, gaps=gaps, nbins=nbins)


if __name__ == "__main__":
    main()
