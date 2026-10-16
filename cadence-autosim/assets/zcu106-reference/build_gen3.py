#!/usr/bin/env python3
"""
PCIe 3.0 (8 GT/s) signal integrity on the ZCU106 PCIe x4 link.

Transmitter / receiver are the PCI Express Gen3 behavioural models that ship
with Allegro SPB 24.1 (share\\topxp\\ComplianceKits\\pcie3), rewritten with plain
linear controlled sources because tlsim has no expression-source parser; the
electrical behaviour is unchanged.  The interconnect is the same Cadence
field-solved RLGC + via models used for the Gen2 study.
"""

import json
import math
import re
import subprocess
from pathlib import Path
import os

# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
SPB_ROOT = Path(os.environ.get("SPBROOT", r"D:\tools\Cadence\SPB_24.1"))
TLSIM = SPB_ROOT / "tools" / "bin" / "tlsim.exe"
HERE = Path(__file__).resolve().parent
SIM_DIR = HERE / "sim"
OUT_DIR = HERE / "out"

MIL = 2.54e-5

# Cadence bem2d results for this board's stackup (same numbers as the Gen2 run)
TL = {
    "SL_4P2": dict(C=1.5223e-10, L=2.4851e-07, R=10.322,  t=2.89e-5,  h=3.0e-5),
    "MS_5P5": dict(C=1.1287e-10, L=2.9163e-07, R=4.1081,  t=3.5e-5,   h=0.0),
}
for v in TL.values():
    v["Z0"] = math.sqrt(v["L"] / v["C"])
    v["Td"] = math.sqrt(v["L"] * v["C"])
    v["Td_ps_in"] = v["Td"] * 0.0254 * 1e12
    v["v"] = 1.0 / v["Td"]                     # m/s

BIT_PS = 125.0        # 8 GT/s -> 125 ps UI
EDGE_PS = 30.0
DRV_RT = 50.0         # spec TX pull-up termination
CAP_NF = 100.0
CAP_ESL = 0.45e-9
RX_RT = 50.0
N_PAT = 3

LANES = [
    dict(lane="TX0", milA=2186.5154, milB=284.3756, cap="C759"),
    dict(lane="TX1", milA=2273.3886, milB=276.6948, cap="C763"),
    dict(lane="TX2", milA=2210.5569, milB=278.3459, cap="C761"),
    dict(lane="TX3", milA=2124.6132, milB=281.6339, cap="C765"),
]


def prbs(order, nbits):
    """PRBS-<order> as used for PCIe compliance patterns."""
    n = (1 << order) - 1
    taps = {7: (6, 5), 9: (8, 4), 15: (14, 13)}[order]
    reg = n
    out = []
    for _ in range(nbits):
        out.append(reg & 1)
        fb = 0
        for t in taps:
            fb ^= (reg >> t) & 1
        reg = ((reg << 1) | fb) & n
    return out


def pwl_from_bits(bits, deemphasis_db=0.0):
    """Trapezoidal PWL plus PCIe-style TX de-emphasis.

    First bit after a transition keeps full swing, following bits of the same
    value use the de-emphasised level (PCIe Gen3 preset with -3.5 dB / -6 dB).
    """
    tb = BIT_PS * 1e-12
    edge = EDGE_PS * 1e-12
    k = 10 ** (-abs(deemphasis_db) / 20.0) if deemphasis_db else 1.0
    lv = []
    for i, b in enumerate(bits):
        if i == 0 or bits[i - 1] != b:
            lv.append(1.0)              # transition bit: full swing
        else:
            lv.append(k)                # de-emphasised "repeat" bit
    pts = [(0.0, 0.0)]
    prev = bits[0]
    for i in range(1, len(bits)):
        t0 = i * tb
        if bits[i] != prev:
            pts.append((t0, prev * lv[i - 1]))
            pts.append((t0 + edge, bits[i] * lv[i]))
            prev = bits[i]
        else:
            pts.append((t0, bits[i] * lv[i - 1]))
            pts.append((t0 + edge, bits[i] * lv[i]))
    pts.append((len(bits) * tb, prev * lv[-1]))
    # collapse duplicates and keep time monotonic
    out = []
    for t, v in pts:
        if out and t <= out[-1][0]:
            continue
        out.append((t, v))
    return " ".join(f"{t:.6e} {v:.5f}" for t, v in out), len(bits) * tb


def build_netlist(ln, deemp_db, tag):
    bits = prbs(9, 511) * N_PAT
    bits = bits + [bits[0]] * 30
    pwl, t_end = pwl_from_bits(bits, deemp_db)
    lenA = ln["milA"] * MIL
    lenB = ln["milB"] * MIL
    return f"""* ZCU106 PCIe Gen3 x4 - {ln['lane']} channel, 8 GT/s  ({tag})
* PCIe Gen3 spec TX/RX behavioural models + Cadence field-solved interconnect
Vp   inp  0 PWL({pwl})
Vn   inn  0 PWL({invert(pwl)})
Vpwr pwr  0 1.0
Vgnd gnd  0 0
Xtx p n pwr inp inn gnd tx_pcix_lin
XVIA1_P p  a1 VIA_ZCU106_BRD24_V19H10_L6A0W42000LX28b8a2e8_2aa75340
XVIA1_N n  b1 VIA_ZCU106_BRD24_V19H10_L6A0W42000LX28b8a2e8_2aa75340
NTL_A_P ( a1 0 ) ( a2 0 )
+L={lenA:.8f} rlgc_name=SL_4P2 file=rlgc.inc
NTL_A_N ( b1 0 ) ( b2 0 )
+L={lenA:.8f} rlgc_name=SL_4P2 file=rlgc.inc
XVIA2_P a2 c1 VIA_ZCU106_BRD24_V20H10_L6A0W42000LX28969e50_2b07e1b0
XVIA2_N b2 d1 VIA_ZCU106_BRD24_V20H10_L6A0W42000LX28969e50_2b07e1b0
Lcap_P c1 c2 {CAP_ESL:.4e}
Ccap_P c2 c3 {CAP_NF*1e-9:.6e}
Lcap_N d1 d2 {CAP_ESL:.4e}
Ccap_N d2 d3 {CAP_NF*1e-9:.6e}
NTL_B_P ( c3 0 ) ( rp 0 )
+L={lenB:.8f} rlgc_name=MS_5P5 file=rlgc.inc
NTL_B_N ( d3 0 ) ( rn 0 )
+L={lenB:.8f} rlgc_name=MS_5P5 file=rlgc.inc
Xrx rp rn pwr gnd rxdiff rx_pcix_lin
Rdum rxdiff 0 1e9
.node_param rxdiff name=RX_DIFF vlo_max=-0.1 vhi_min=0.1 print refnode cycles=(1)
.node_param p     name=TX_P    vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.include models_g3.inc
.include via.inc
.include rlgc.inc
.end
""", t_end


def invert(pwl):
    """Complement a two-level PWL string (values 0/1 -> 1/0)."""
    toks = pwl.split()
    out = []
    for i in range(0, len(toks), 2):
        t = float(toks[i]); v = float(toks[i + 1])
        # complement about the maximum level seen in the pattern
        out.append(f"{t:.6e} {max(0.0, 1.0 - v):.5f}")
    return " ".join(out)


def parse_sim(path):
    waves = {}
    for line in open(path, "r", errors="ignore"):
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


def eye_metrics(wave, ui_ps, skip_ui=4):
    """Eye height / width plus the per-phase vertical opening for a bathtub."""
    if not wave:
        return dict(height=0.0, width=0.0, vmax=0.0, vmin=0.0, bathtub=[])
    ui = ui_ps * 1e-12
    vmax = max(v for _, v in wave); vmin = min(v for _, v in wave)
    pts = [(t - skip_ui * ui, v) for t, v in wave if t >= skip_ui * ui] or wave
    nb = 60
    bins = [[] for _ in range(nb)]
    for t, v in pts:
        ph = (t % (2 * ui)) / (2 * ui)
        bins[min(nb - 1, int(ph * nb))].append(v)
    span = (vmax - vmin) or 1.0
    nv = 200
    open_ph = []
    for b in bins:
        if len(b) < 5:
            open_ph.append(0.0); continue
        hist = [0] * nv
        for v in b:
            hist[min(nv - 1, int((v - vmin) / span * nv))] += 1
        best = run = 0; seen = False
        for k in range(nv):
            if hist[k] == 0:
                run += 1
            else:
                if run and seen:
                    best = max(best, run * span / nv)
                seen = True; run = 0
        open_ph.append(best)
    height = max(open_ph) if open_ph else 0.0
    col = 2 * ui / nb
    best = run = 0
    for g in open_ph:
        if g >= 0.5 * height:
            run += 1
            best = max(best, run)
        else:
            run = 0
    width = best * col * 1e12
    sv = sorted(v for _, v in pts)
    hi = sv[min(len(sv) - 1, int(0.95 * len(sv)))]
    lo = sv[int(0.05 * len(sv))]
    return dict(height=height, width=width, vmax=vmax, vmin=vmin,
                hi_lvl=hi, lo_lvl=lo, bathtub=[round(g * 1e3, 3) for g in open_ph])


def run_channel(ln, deemp_db=0.0, tag="nominal"):
    st = re.sub(r"[^A-Za-z0-9_]", "_", f"{ln['lane']}_{tag}")
    net_txt, t_end = build_netlist(ln, deemp_db, tag)
    net = SIM_DIR / f"g3_{st}.spc"
    net.write_text(net_txt, encoding="ascii")
    out = SIM_DIR / f"g3_{st}.sim"
    log = SIM_DIR / f"g3_{st}.log"
    cmd = [str(TLSIM), "-r", "0.002", "-e", f"{t_end*1e9 + 5:.3f}",
           "-o", str(out), "-log", str(log), str(net)]
    p = subprocess.run(cmd, cwd=str(SIM_DIR), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        print("   !! no waveform", st, p.stdout[-400:])
        return dict(lane=ln["lane"], error="no waveform")
    w = parse_sim(out)
    rx = w.get("RX_DIFF", [])
    tx = w.get("TX_P", [])
    e = eye_metrics(rx, BIT_PS)
    return dict(lane=ln["lane"], deemp_db=deemp_db, cap=ln["cap"],
                milA=ln["milA"], milB=ln["milB"], total_mil=ln["milA"] + ln["milB"],
                eye_height_mV=e["height"] * 1e3, eye_width_ps=e["width"],
                vmax=e["vmax"], vmin=e["vmin"],
                swing_mV=(e["hi_lvl"] - e["lo_lvl"]) * 1e3,
                tx_vmax=max((v for _, v in tx), default=0.0),
                tx_vmin=min((v for _, v in tx), default=0.0),
                bathtub=e["bathtub"],
                wave=[[round(t * 1e12, 3), round(v, 6)] for t, v in rx])


def tdr_profile(ln):
    """TDR impedance profile from a Cadence tlsim step response.

    A 30 ps step is launched into the single-ended TX path; the reflected wave
    at the source is converted to impedance with the standard
    Z(t) = Z0 (1 + rho)/(1 - rho) relation.
    """
    # TDR reference: the impedance of the *launch* (50 ohm source / receiver),
    # not the impedance of the line being measured.
    z0 = 50.0
    lenA = ln["milA"] * MIL
    lenB = ln["milB"] * MIL
    net = SIM_DIR / f"tdr_{ln['lane']}.spc"
    net.write_text(f"""* TDR probe on the {ln['lane']} TX path
Vsrc src 0 PWL(0n 0 1n 0 1.03n 1 60n 1)
Rs src probe 50
XVIA1 probe  a1 VIA_ZCU106_BRD24_V19H10_L6A0W42000LX28b8a2e8_2aa75340
NTL_A ( a1 0 ) ( a2 0 )
+L={lenA:.8f} rlgc_name=SL_4P2 file=rlgc.inc
XVIA2 a2 c1 VIA_ZCU106_BRD24_V20H10_L6A0W42000LX28969e50_2b07e1b0
Lcap c1 c2 {CAP_ESL:.4e}
Ccap c2 c3 {CAP_NF*1e-9:.6e}
NTL_B ( c3 0 ) ( rp 0 )
+L={lenB:.8f} rlgc_name=MS_5P5 file=rlgc.inc
Rrx rp 0 50
.node_param probe name=TDR_PROBE vlo_max=0.1 vhi_min=0.9 print refnode cycles=(1)
.include via.inc
.include rlgc.inc
.end
""", encoding="ascii")
    out = SIM_DIR / f"tdr_{ln['lane']}.sim"
    cmd = [str(TLSIM), "-r", "0.0005", "-e", "30", "-o", str(out), str(net)]
    subprocess.run(cmd, cwd=str(SIM_DIR), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        return None
    w = parse_sim(out)
    probe = w.get("TDR_PROBE", [])
    if not probe:
        return None
    # launched step is 0.5 V (1 V behind 50 ohm), so incident = 0.5 V
    inc = 0.5
    prof = []
    for t, v in probe:
        if t < 1.0e-9:
            continue
        rho = (v - inc) / inc
        if abs(1 - rho) < 1e-3:
            continue
        z = z0 * (1 + rho) / (1 - rho)
        prof.append([round((t - 1.0e-9) * 1e12, 1), round(z, 2)])
    return dict(lane=ln["lane"], z0=z0, profile=prof[::8])


def insertion_loss():
    """Channel transfer from the Cadence field-solved RLGC (ABCD chain)."""
    series = []
    for name, tl in TL.items():
        pts = []
        z0, v = tl["Z0"], tl["v"]
        R, C, L = tl["R"], tl["C"], tl["L"]
        for f_ghz in [x / 10 for x in range(1, 201)]:
            f = f_ghz * 1e9
            w = 2 * math.pi * f
            r_ac = R * math.sqrt(max(f_ghz, 0.01))
            g_d = w * C * 0.012                     # FR408HR Df ~0.012
            gamma = complex(0.0, 0.0)
            zc = complex(z0, 0.0)
            if r_ac or g_d:
                y = complex(g_d, w * C)
                z = complex(r_ac, w * L)
                gamma = (z * y) ** 0.5
                zc = (z / y) ** 0.5
            pts.append((f_ghz, gamma))
        series.append((name, pts))
    return series


def main():
    OUT_DIR.mkdir(exist_ok=True)
    res = dict(lanes=[], tdr=[], caps={})

    # --- eye with and without TX de-emphasis -------------------------------
    for ln in LANES:
        for deemp, tg in ((0.0, "nodeemp"), (-3.5, "deemp35")):
            r = run_channel(ln, deemp, tg)
            r["lane"] = ln["lane"]
            res["lanes"].append(r)
            print(f"  {ln['lane']} deemp={deemp:+.1f}dB  "
                  f"eyeH={r.get('eye_height_mV',0):.0f} mV  "
                  f"eyeW={r.get('eye_width_ps',0):.0f} ps  "
                  f"vpp={r.get('swing_mV',0):.0f} mV")

    # --- TDR ---------------------------------------------------------------
    for ln in LANES[:2]:
        t = tdr_profile(ln)
        if t:
            res["tdr"].append(t)
            print(f"  TDR {ln['lane']}: {len(t['profile'])} points")

    (OUT_DIR / "gen3_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("wrote", OUT_DIR / "gen3_results.json")


if __name__ == "__main__":
    main()
