#!/usr/bin/env python3
"""
DDR4-2666 signal integrity on the ZCU106 **PL** DDR4 bus (HP bank, POD12).

Topology, lengths, layers, widths and via counts come from the board itself
(Allegro etch-length / via reports).  The transmission-line impedance is
calibrated to the bem2d result that signoise produced for the same stackup:
the stripline closed form is fitted to the field-solved 4.2 mil case and then
re-used for the inner-layer widths this bus actually uses.  The 22.3 mil
outer-layer escape of each net is modelled with the field-solved microstrip
values in `MS` instead (the stripline form is wrong there).
"""

import json
import math
import re
import subprocess
import sys
from pathlib import Path
import os

# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
SPB_ROOT = Path(os.environ.get("SPBROOT", r"D:\tools\Cadence\SPB_24.1"))
TLSIM = SPB_ROOT / "tools" / "bin" / "tlsim.exe"
HERE = Path(__file__).resolve().parent
SIM = HERE / "sim"
OUT = HERE / "out"
MIL = 2.54e-5

VDDQ = 1.2
UI_PS = 750.0          # DDR4-2666 -> 0.75 ns
# Stimulus edge of the logic input.  tlsim's bdrvr synthesises the output
# transition from the V/I tables, C_comp and *this* edge (the AC_PARAM
# RISE_TIME is ignored - verified with si/build_ddr4.py:edge_test), so a fast
# stimulus lets the model's own natural edge dominate: ~105 ps (20-80%) into
# 40 ohm, between the vendor's F (80 ps) and M (208 ps) slew corners.
EDGE_PS = 20.0
R_DRV = 34.0           # DDR4 DQ driver impedance (x16 device)
R_ODT = 40.0           # DRAM ODT (RTT_NOM/PARK typical)
EPS_R = 3.4

# --- the official AMD / Xilinx models that arrived for this round ----------
AMD_IBS = Path(os.environ.get("AMD_MODELS", r"D:\Programs\candence")) / "zynquplus.ibs"
AMD_PKG = Path(os.environ.get("AMD_MODELS", r"D:\Programs\candence")) / "pkg" / "xczu7ev_ffvc1156.pkg"
# HP_POD12 = the DDR4 I/O standard on the ZU7EV HP banks (PL DDR4).
# F/M/S = fast / medium / slow slew corners, OUT40/48/60 = DCI output impedance.
AMD_MODELS = ["HP_POD12_DCI_F_OUT40", "HP_POD12_DCI_M_OUT40", "HP_POD12_DCI_S_OUT40",
              "HP_POD12_DCI_M_OUT48", "HP_POD12_DCI_M_OUT60"]
# U1 pins of the three channels under test (Allegro net report)
U1_PIN = {"DQ0": "AF16", "DQ8": "AA14", "DQS0_T": "AH14"}

# --- stripline impedance: closed form calibrated to the bem2d solution ------
B_TOT = 6.49           # mil between the reference planes (2.89 + 0.6 + 3.0)
T_CU = 0.6             # mil copper thickness


def z0_stripline(w_mil):
    w, b, t = w_mil, B_TOT, T_CU
    raw = 60.0 / math.sqrt(EPS_R) * math.log(4 * b / (0.67 * math.pi * w * (0.8 + t / w)))
    return raw * 1.0930                        # fitted to bem2d: 4.2 mil -> 40.4 ohm


# 1.017*sqrt(Er) ns/ft  ->  for Er 3.4 that is 156 ps/in
TD = 1.017 * math.sqrt(EPS_R) * 1000.0 / 12.0   # ps/in


def prbs9(nbits):
    reg = 0x1FF
    out = []
    for _ in range(nbits):
        out.append(reg & 1)
        fb = ((reg >> 8) & 1) ^ ((reg >> 4) & 1)
        reg = ((reg << 1) | fb) & 0x1FF
    return out


def pwl(bits, vhigh, edge_ps=None):
    tb = UI_PS * 1e-12
    edge = (EDGE_PS if edge_ps is None else edge_ps) * 1e-12
    pts = [(0.0, 0.0)]
    prev = bits[0]
    for i in range(1, len(bits)):
        t0 = i * tb
        if bits[i] != prev:
            pts.append((t0, vhigh * prev))
            pts.append((t0 + edge, vhigh * bits[i]))
            prev = bits[i]
    pts.append((len(bits) * tb, vhigh * prev))
    return " ".join(f"{t:.6e} {v:.4f}" for t, v in pts), len(bits) * tb


def rlgc_inc(path, models):
    """Write a Cadence RLGC data file.

    One DATAPOINTS block per line carries both the DC and the high-frequency
    limit, exactly like the files signoise generates.
    """
    txt = []
    for name, m in models.items():
        txt.append(f"DATAPOINTS RLGC {name}")
        txt.append("FREQUENCY=0")
        txt.append("CMATRIX")
        txt.append(f" {m['C']:.6e}")
        txt.append("LMATRIX")
        txt.append(f" {m['L']:.6e}")
        txt.append("GMATRIX")
        txt.append(" 0.000000e+00")
        txt.append("RMATRIX")
        txt.append(f" {m['R']:.6e}")
        txt.append("FREQUENCY=INF")
        txt.append("CMATRIX")
        txt.append(f" {m['C']:.6e}")
        txt.append("LMATRIX")
        txt.append(f" {m['L']:.6e}")
        txt.append("END RLGC")
        txt.append("")
    path.write_text("\n".join(txt), encoding="ascii")
    return path.name


def mk_models(widths):
    """Build RLGC entries for each stripline width used by this bus."""
    models = {}
    for w in widths:
        z0 = z0_stripline(w)
        # keep the field-solved C for the 4.2 mil case and scale by impedance
        C = 1.5223e-10 * (40.4 / z0)
        L = z0 * z0 * C
        R = 10.322 * (4.2 / w)
        models[f"SL_{w:.1f}".replace(".", "P")] = dict(Z0=z0, C=C, L=L, R=R)
    return models


# --- TOP-layer microstrip escapes -------------------------------------------
# The 22.3 mil escape of every DDR4 net sits on the outer layer, so it must be
# modelled as a microstrip, not with the stripline closed form (which had given
# 12.8 / 30.4 ohm here).  These values come from bem2d on the *same* cross
# section Cadence itself solved for the outer layers (interconn.iml
# STL_1S_1R_14491/14492/14494: 0.8 mil coat + 2 mil copper foil + 3.5 mil
# prepreg to the reference plane, sigma 3.43e7).  Validation: re-solving that
# cross section at 4.0 and 5.5 mil reproduces Cadence's own numbers exactly
# (57.776 / 50.831 ohm, C 9.9174e-11 / 1.1287e-10, L 3.3105e-7 / 2.9163e-7,
# R 5.6487 / 4.1081) - see si/audit_zcu106_si_2026-10-16.md.
MS = {
    5.8:  dict(Z0=49.668, C=1.1555e-10, L=2.8505e-07, R=3.8957),
    10.0: dict(Z0=37.646, C=1.5333e-10, L=2.1730e-07, R=2.2595),
}


def mk_ms_models():
    models = {}
    for w, m in MS.items():
        models[f"MS_{w:.1f}".replace(".", "P")] = dict(m)
    return models


def eye(wave, ui_ps, skip_ui=6):
    if not wave:
        return dict(height=0.0, width=0.0, vmax=0, vmin=0, bathtub=[])
    ui = ui_ps * 1e-12
    pts = [(t - skip_ui * ui, v) for t, v in wave if t >= skip_ui * ui] or wave
    vmax = max(v for _, v in pts); vmin = min(v for _, v in pts)
    span = (vmax - vmin) or 1.0
    nb, nv = 60, 200
    bins = [[] for _ in range(nb)]
    for t, v in pts:
        ph = (t % (2 * ui)) / (2 * ui)
        bins[min(nb - 1, int(ph * nb))].append(v)
    op = []
    for b in bins:
        if len(b) < 5:
            op.append(0.0); continue
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
        op.append(best)
    h = max(op) if op else 0.0
    col = 2 * ui / nb
    best = run = 0
    for g in op:
        if g >= 0.5 * h:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return dict(height=h, width=best * col * 1e12,
                vmax=vmax, vmin=vmin, bathtub=[round(g * 1e3, 2) for g in op])


def run(title, vhigh, sections, r_odt=R_ODT, ui_ps=UI_PS, bits_n=511, patterns=2,
        term="vddq40"):
    """sections: list of (model_name, length_mil)

    `term` must match the POD12 arm (vddq40) for the generic-IBIS comparison in
    the report to be a like-for-like comparison - the earlier version
    terminated this arm to ground, which made the DC levels differ by ~600 mV
    for reasons that had nothing to do with the driver model.
    """
    bits = prbs9(bits_n) * patterns
    bits += [bits[0]] * 20
    p, t_end = pwl(bits, vhigh)
    sl = "".join(f"NTL_S{i} ( n{i} 0 ) ( n{i+1} 0 )\n"
                 f"+L={ln*MIL:.8f} rlgc_name={mn} file=rlgc_ddr.inc\n"
                 for i, (mn, ln) in enumerate(sections))
    last = f"n{len(sections)}"
    net = SIM / f"ddr_{re.sub(r'[^A-Za-z0-9]', '_', title)}.spc"
    # driver: real IBIS model (Cadence 1.2 V, 40 ohm, 1 pF) converted to the
    # DATAPOINTS/BDRVR form tlsim consumes
    driver = f"""Vpwr pwr 0 {VDDQ}
Vgnd gnd 0 0
Ven  en  0 1.0
Vdrv drv 0 PWL({p})
bdrvr pwr n0 gnd drv en pwr gnd Model=cds_out_gen_1p2v_40_1pf File=ibis_ddr.inc
"""
    term_txt, extra = term_block(term, last)
    net.write_text(f"""* DDR4-2666 {title}
* driver = Cadence 1.2 V IBIS model (cds_out_gen_1p2v_40_1pf), DRAM ODT {r_odt:.0f} ohm,
* termination = {term} (same as the POD12 arm)
{driver}{extra}{sl}{term_txt}
Rdummy {last} 0 1e12
.node_param {last} name=DRAM_PIN vlo_max={VDDQ*0.4:.3f} vhi_min={VDDQ*0.6:.3f} print refnode cycles=(1)
.node_param n0 name=DRV_PIN vlo_max={VDDQ*0.4:.3f} vhi_min={VDDQ*0.6:.3f} print refnode cycles=(1)
.include rlgc_ddr.inc
.include ibis_ddr.inc
.end
""", encoding="ascii")
    out = SIM / f"ddr_{re.sub(r'[^A-Za-z0-9]', '_', title)}.sim"
    cmd = [str(TLSIM), "-r", "0.002", "-e", f"{t_end*1e9+3:.3f}", "-o", str(out), str(net)]
    subprocess.run(cmd, cwd=str(SIM), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        return None
    w = {}
    for line in open(out, errors="ignore"):
        if line.startswith("S!"):
            f = line.rstrip().split("!")
            try:
                w.setdefault(f[4], []).append((float(f[1]), float(f[3])))
            except ValueError:
                pass
    pin = w.get("DRAM_PIN", [])
    e = eye(pin, ui_ps)
    return dict(title=title, sections=sections, term=term,
                eye_height_mV=e["height"] * 1e3,
                eye_width_ps=e["width"], vmax=e["vmax"], vmin=e["vmin"],
                high_lvl_mV=e["vmax"] * 1e3, low_lvl_mV=e["vmin"] * 1e3,
                ui_ps=ui_ps, bathtub=e["bathtub"],
                wave=[[round(t * 1e12, 3), round(v, 6)] for t, v in pin])


def run_ideal(title, vhigh, sections, r_odt=R_ODT, ui_ps=UI_PS, bits_n=511, patterns=2,
              term="vddq40"):
    """Same channel with the previous hand-written R driver, for comparison."""
    bits = prbs9(bits_n) * patterns
    bits += [bits[0]] * 20
    p, t_end = pwl(bits, vhigh)
    sl = "".join(f"NTL_S{i} ( n{i} 0 ) ( n{i+1} 0 )\n"
                 f"+L={ln*MIL:.8f} rlgc_name={mn} file=rlgc_ddr.inc\n"
                 for i, (mn, ln) in enumerate(sections))
    last = f"n{len(sections)}"
    net = SIM / f"ddri_{re.sub(r'[^A-Za-z0-9]', '_', title)}.spc"
    term_txt, extra = term_block(term, last)
    net.write_text(f"""* DDR4-2666 {title} (ideal R driver, for comparison)
Vdrv drv 0 PWL({p})
Rdrv drv n0 {R_DRV}
{extra}{sl}{term_txt}
Rdummy {last} 0 1e12
.node_param {last} name=DRAM_PIN vlo_max={VDDQ*0.4:.3f} vhi_min={VDDQ*0.6:.3f} print refnode cycles=(1)
.node_param n0 name=DRV_PIN vlo_max={VDDQ*0.4:.3f} vhi_min={VDDQ*0.6:.3f} print refnode cycles=(1)
.include rlgc_ddr.inc
.end
""", encoding="ascii")
    out = SIM / f"ddri_{re.sub(r'[^A-Za-z0-9]', '_', title)}.sim"
    cmd = [str(TLSIM), "-r", "0.002", "-e", f"{t_end*1e9+3:.3f}", "-o", str(out), str(net)]
    subprocess.run(cmd, cwd=str(SIM), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        return None
    w = {}
    for line in open(out, errors="ignore"):
        if line.startswith("S!"):
            f = line.rstrip().split("!")
            try:
                w.setdefault(f[4], []).append((float(f[1]), float(f[3])))
            except ValueError:
                pass
    pin = w.get("DRAM_PIN", [])
    e = eye(pin, ui_ps)
    return dict(title=title, sections=sections, eye_height_mV=e["height"] * 1e3,
                eye_width_ps=e["width"], vmax=e["vmax"], vmin=e["vmin"],
                ui_ps=ui_ps, bathtub=e["bathtub"],
                wave=[[round(t * 1e12, 3), round(v, 6)] for t, v in pin])


# ===========================================================================
#  Official AMD/Xilinx model support (this round)
# ===========================================================================

def pkg_rlc(pin, path=AMD_PKG):
    """Self R / L / C of one pin from an IBIS [Package Model] file.

    The XtractIM-generated .pkg file stores a banded resistance matrix and
    sparse inductance/capacitance matrices; only the self (diagonal) terms are
    used here.
    """
    lines = Path(path).read_text(errors="ignore").splitlines()
    sec = None
    out = {}
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        low = s.lower()
        if low.startswith("[resistance matrix]"):
            sec = "R"
        elif low.startswith("[inductance matrix]"):
            sec = "L"
        elif low.startswith("[capacitance matrix]"):
            sec = "C"
        elif low.startswith("[row]"):
            name = s.split(None, 1)[1].strip()
            vals = []
            j = i + 1
            while j < len(lines):
                t = lines[j].strip()
                if not t or t.startswith("[") or t.startswith("|"):
                    break
                p = t.split()
                try:
                    vals.append((name if len(p) == 1 else p[0], float(p[-1])))
                except ValueError:
                    pass
                j += 1
            if name == pin and sec:
                selfv = [v for k, v in vals if k == name]
                if selfv:
                    out[sec] = selfv[0]
            i = j
            continue
        i += 1
    return out.get("R"), out.get("L"), out.get("C")


def pod12_include(models=AMD_MODELS, path=None, clamps=True):
    """Convert the requested POD12 models out of zynquplus.ibs for tlsim."""
    path = path or (SIM / "ibis_pod12.inc")
    sys.path.insert(0, str(HERE))
    import ibis2tlsim as I
    mdls = I.parse_many(str(AMD_IBS), models)
    emit = []
    for m in mdls:
        if not clamps:
            m = dict(m)
            m["pclamp"] = []
            m["gclamp"] = []
        emit.append(I.emit(m))
    path.write_text("\n".join(emit), encoding="ascii")
    meta = {}
    for m in mdls:
        ramp = m["ramp_r"]
        edge = None
        for w in I.waveforms(m["raw"]):
            if w["kind"] == "Rising Waveform" and abs((w["v_fixture"] or 0) - 0.84) < 1e-9:
                dt, _, _ = I.edge_time(w["rows"])
                edge = dt
                break
        meta[m["name"]] = dict(
            vdd=m["vdd"], vinl=m["vinl"], vinh=m["vinh"], c_comp=m["c_comp"],
            dVdt_r=(ramp[0] / ramp[1]) if ramp else None,
            dVdt_f=(m["ramp_f"][0] / m["ramp_f"][1]) if m["ramp_f"] else None,
            edge_20_80_ps=(edge * 1e12) if edge else None,
            n_pullup=len(m["pullup"]), n_pulldown=len(m["pulldown"]))
    return meta


def term_block(term, last):
    """DRAM-side termination.

    gnd40   : 40 ohm to VSSQ  (the previous round's JEDEC "40 ohm ODT"
              equivalent, kept as a control)
    vddq40  : 40 ohm to VDDQ  (the POD12 arrangement - the far end is pulled
              to the I/O supply, which is what makes the bus compatible with
              the model's own Vinl = 0.76 V / Vinh = 0.92 V thresholds)
    split   : 80 ohm to VDDQ + 80 ohm to VSSQ (Thevenin 0.6 V / 40 ohm)
    """
    if term == "gnd40":
        return f"Rterm {last} 0 40.0\n", ""
    if term == "vddq40":
        return f"Rterm {last} vddq 40.0\n", "Vvddq vddq 0 1.2\n"
    if term == "split":
        return (f"Rpu {last} vddq 80.0\nRpd {last} 0 80.0\n",
                "Vvddq vddq 0 1.2\n")
    raise ValueError(term)


def run_amd(title, model, sections, term="vddq40", use_pkg=True, ui_ps=UI_PS,
            bits_n=511, patterns=2, clamps=False, stim_edge_ns=None,
            keep_wave=True):
    """DDR4 write channel driven by the official Xilinx POD12 IBIS model."""
    # clamps/use_pkg belong in the name: without them the main run and the
    # "with IBIS clamps" sensitivity run overwrite each other's netlist and
    # waveform, which made the on-disk artefacts disagree with the JSON.
    key = re.sub(r"[^A-Za-z0-9]", "_",
                 f"{title}_{model}_{term}_{'pkg' if use_pkg else 'nopkg'}"
                 f"_{'clamp' if clamps else 'noclamp'}")
    bits = prbs9(bits_n) * patterns
    bits += [bits[0]] * 20
    # tlsim synthesises the output transition from the V/I tables, C_comp and
    # the *input* stimulus edge (measured: RISE_TIME in AC_PARAM has no effect),
    # so the stimulus edge is the knob that emulates the vendor's slew corners.
    p, t_end = pwl(bits, VDDQ, None if stim_edge_ns is None else stim_edge_ns * 1000.0)
    sl = "".join(f"NTL_S{i} ( n{i} 0 ) ( n{i+1} 0 )\n"
                 f"+L={ln*MIL:.8f} rlgc_name={mn} file=rlgc_ddr.inc\n"
                 for i, (mn, ln) in enumerate(sections))
    last = f"n{len(sections)}"
    term_txt, extra = term_block(term, last)
    pkg_txt, sl, first = "", sl, "n0"
    if use_pkg:
        pin = U1_PIN[title.split()[0]]
        Rp, Lp, Cp = pkg_rlc(pin)
        pkg_txt = (f"* U1 package model from xczu7ev_ffvc1156.pkg, pin {pin}\n"
                   f"Lpkg ndrv npk1 {Lp:.6e}\nRpkg npk1 npk2 {Rp:.6e}\n"
                   f"Cpkg npk2 0 {Cp:.6e}\n")
        sl, first = sl.replace("( n0 0 )", "( npk2 0 )", 1), "npk2"
    else:
        # the model drives node ndrv: the first line section must start there
        sl, first = sl.replace("( n0 0 )", "( ndrv 0 )", 1), "ndrv"
    txt = f"""* DDR4-2666 write, {title} - official AMD model {model}
* driver: zynquplus.ibs POD12 ({model}), termination={term}, package={use_pkg}
* DRAM ODT: POD12 (see build_report section 15)
Vpwr pwr 0 {VDDQ}
Vgnd gnd 0 0
Ven  en  0 1.0
Vdrv drv 0 PWL({p})
bdrvr pwr ndrv gnd drv en pwr gnd Model={model} File={"ibis_pod12.inc" if clamps else "ibis_pod12_noclamp.inc"}
{extra}{pkg_txt}{sl}{term_txt}Rdummy {last} 0 1e12
.node_param {last} name=DRAM_PIN vlo_max={VDDQ*0.4:.3f} vhi_min={VDDQ*0.6:.3f} print refnode cycles=(1)
.include rlgc_ddr.inc
.include {"ibis_pod12.inc" if clamps else "ibis_pod12_noclamp.inc"}
.end
"""
    net = SIM / f"amd_{key}.spc"
    net.write_text(txt, encoding="ascii")
    out = SIM / f"amd_{key}.sim"
    if out.exists():
        out.unlink()
    cmd = [str(TLSIM), "-r", "0.002", "-e", f"{t_end*1e9+3:.3f}", "-o", str(out), str(net)]
    r = subprocess.run(cmd, cwd=str(SIM), capture_output=True, text=True)
    if not out.exists() or out.stat().st_size < 100:
        return dict(title=title, model=model, term=term, error=r.stdout[-400:] + r.stderr[-400:])
    w = {}
    for line in open(out, errors="ignore"):
        if line.startswith("S!"):
            f = line.rstrip().split("!")
            try:
                w.setdefault(f[4], []).append((float(f[1]), float(f[3])))
            except ValueError:
                pass
    pin = w.get("DRAM_PIN", [])
    e = eye(pin, ui_ps)
    vs = sorted(v for _, v in pin)
    n = len(vs)
    lo_med = vs[int(0.10 * n)] if n else 0.0
    hi_med = vs[int(0.90 * n)] if n else 0.0
    edge_measured = wave_edge_20_80(pin)
    return dict(title=title, model=model, term=term, use_pkg=use_pkg,
                sections=sections, eye_height_mV=e["height"] * 1e3,
                stim_edge_ns=(EDGE_PS / 1000.0 if stim_edge_ns is None else stim_edge_ns),
                eye_width_ps=e["width"], vmax=e["vmax"], vmin=e["vmin"],
                high_mV=e["vmax"] * 1e3, low_mV=e["vmin"] * 1e3,
                high_lvl_mV=hi_med * 1e3, low_lvl_mV=lo_med * 1e3,
                margin_vinh_mV=(hi_med - 0.92) * 1e3,
                margin_vinl_mV=(0.76 - lo_med) * 1e3,
                edge_ps=edge_measured,
                ui_ps=ui_ps, bathtub=e["bathtub"],
                wave=([[round(t * 1e12, 3), round(v, 6)] for t, v in pin]
                      if keep_wave else None))


def dc_check(model, term, use_pkg=False, enable=1.0, ibs_file="ibis_pod12.inc"):
    """Static high / low at the DRAM pin: validates the converted V/I tables."""
    term_txt, extra = term_block(term, "n0")
    pkg_txt, pad = "", "n0"
    if use_pkg:
        pin = U1_PIN["DQ0"]
        Rp, Lp, Cp = pkg_rlc(pin)
        pkg_txt = (f"Lpkg ndrv npk1 {Lp:.6e}\nRpkg npk1 n0 {Rp:.6e}\n"
                   f"Cpkg n0 0 {Cp:.6e}\n")
    res = {}
    for level, name in ((VDDQ, "high"), (0.0, "low")):
        txt = f"""* DDR4 POD12 DC operating point, {model}, term={term}, level={name}
Vpwr pwr 0 {VDDQ}
Vgnd gnd 0 0
Ven  en  0 {enable}
Vdrv drv 0 {level}
bdrvr pwr ndrv gnd drv en pwr gnd Model={model} File={ibs_file}
{extra}{pkg_txt}{term_txt}.node_param {pad} name=DC_PIN print refnode cycles=(1)
.include {ibs_file}
.end
"""
        net = SIM / f"amd_dc_{model}_{term}_{name}.spc"
        net.write_text(txt, encoding="ascii")
        out = SIM / f"amd_dc_{model}_{term}_{name}.sim"
        if out.exists():
            out.unlink()
        subprocess.run([str(TLSIM), "-r", "0.001", "-e", "40", "-o", str(out), str(net)],
                       cwd=str(SIM), capture_output=True, text=True)
        vals = []
        if out.exists():
            for line in open(out, errors="ignore"):
                if line.startswith("S!"):
                    f = line.rstrip().split("!")
                    try:
                        if f[4] == "DC_PIN":
                            vals.append(float(f[3]))
                    except (IndexError, ValueError):
                        pass
        res[name] = vals[-1] if vals else None
    return res


def iv_probe(model, level, vtest, rbig=1000.0, ibs_file="ibis_pod12.inc"):
    """Measure the driver I(V) at the pad.

    NOTE on the bdrvr element syntax (established by reading a signoise
    generated netlist, signoise.run/case1/sim1/comps.spc):

        bdrvr <pullup_ref> <out> <pulldown_ref> <DATA> <ENABLE>
              <powerclamp_ref> <groundclamp_ref> Model=... File=...

    node 4 is the DATA input and node 5 is the ENABLE input.

    The model drives node `ndrv`; an ideal source pushes that same node
    through a 1 kohm resistor, so the current into the node is
    (vtest - vpad)/rbig.  Used to calibrate and verify the sign / axis
    conventions of the converted V/I tables.
    """
    tag = re.sub(r"[^A-Za-z0-9]", "_", f"{model}_{level}_{vtest:.3f}_{rbig:.0f}")
    net = SIM / f"iv_{tag}.spc"
    net.write_text(f"""* I-V probe: {model}, data={level} V, forced {vtest} V through {rbig} ohm
Vpwr pwr 0 {VDDQ}
Vgnd gnd 0 0
Ven  en  0 1.0
Vdrv drv 0 {level}
bdrvr pwr ndrv gnd drv en pwr gnd Model={model} File={ibs_file}
Vsrc src 0 {vtest}
Rbig src ndrv {rbig}
.node_param ndrv name=PAD print refnode cycles=(1)
.include {ibs_file}
.end
""", encoding="ascii")
    out = SIM / f"iv_{tag}.sim"
    if out.exists():
        out.unlink()
    subprocess.run([str(TLSIM), "-r", "0.001", "-e", "4", "-o", str(out), str(net)],
                   cwd=str(SIM), capture_output=True, text=True)
    vals = []
    if out.exists():
        for line in open(out, errors="ignore"):
            if line.startswith("S!"):
                f = line.rstrip().split("!")
                try:
                    if f[4] == "PAD":
                        vals.append(float(f[3]))
                except (IndexError, ValueError):
                    pass
    if not vals:
        return None
    vpad = vals[-1]
    return dict(vpad=vpad, i_into_node=(vtest - vpad) / rbig)


def edge_test(model, ibs_file="ibis_pod12.inc", r_load=40.0, t_edge_ps=80.0,
              rise_time=None, fall_time=None):
    """Step the model into a resistive load and return the pad waveform.

    Used to find out which AC_PARAM term tlsim actually uses for the output
    transition: the model's own RISE_TIME, or the edge of the input stimulus.
    """
    inc = ibs_file
    if rise_time is not None or fall_time is not None:
        txt = (SIM / ibs_file).read_text()
        if rise_time is not None:
            txt = re.sub(r"RISE_TIME=\S+", f"RISE_TIME={rise_time:.6e}", txt)
            txt = re.sub(r"dVdtr=\S+", f"dVdtr={VDDQ/rise_time:.6e}", txt)
        if fall_time is not None:
            txt = re.sub(r"FALL_TIME=\S+", f"FALL_TIME={fall_time:.6e}", txt)
            txt = re.sub(r"dVdtf=\S+", f"dVdtf={VDDQ/fall_time:.6e}", txt)
        inc = "ibis_edge_test.inc"
        (SIM / inc).write_text(txt)
    tag = re.sub(r"[^A-Za-z0-9]", "_",
                 f"{model}_{r_load:.0f}_{t_edge_ps:.0f}_{rise_time}_{fall_time}")
    t1 = 5e-9 + t_edge_ps * 1e-12
    net = SIM / f"et_{tag}.spc"
    net.write_text(f"""* edge test: {model}, R_load={r_load}, input edge={t_edge_ps} ps,
* RISE_TIME={rise_time}, FALL_TIME={fall_time}
Vpwr pwr 0 {VDDQ}
Vgnd gnd 0 0
Ven  en  0 1.0
Vdrv drv 0 PWL(0 0 5e-9 0 {t1:.6e} {VDDQ} 40e-9 {VDDQ})
bdrvr pwr ndrv gnd drv en pwr gnd Model={model} File={inc}
Rload ndrv 0 {r_load}
.node_param ndrv name=PAD print refnode cycles=(1)
.include {inc}
.end
""", encoding="ascii")
    out = SIM / f"et_{tag}.sim"
    if out.exists():
        out.unlink()
    subprocess.run([str(TLSIM), "-r", "0.0005", "-e", "30", "-o", str(out), str(net)],
                   cwd=str(SIM), capture_output=True, text=True)
    w = []
    if out.exists():
        for line in open(out, errors="ignore"):
            if line.startswith("S!"):
                f = line.rstrip().split("!")
                try:
                    if f[4] == "PAD":
                        w.append((float(f[1]) * 1e9, float(f[3])))
                except (IndexError, ValueError):
                    pass
    return w or None


def t20_80(w):
    """20-80% transition time (ps) of a rising step waveform."""
    v0 = w[0][1]
    v1 = max(v for _, v in w)
    span = v1 - v0
    if span < 1e-6:
        return None

    def cross(frac):
        lvl = v0 + frac * span
        for (ta, a), (tb, b) in zip(w, w[1:]):
            if (a - lvl) * (b - lvl) <= 0 and a != b:
                return ta + (tb - ta) * (lvl - a) / (b - a)
        return None
    a, b = cross(0.2), cross(0.8)
    return None if a is None or b is None else (b - a) * 1000.0


def wave_edge_20_80(wave, ui_ps=UI_PS):
    """Median 20-80% rising-edge time (ps) inside a DDR4 pin waveform."""
    if not wave:
        return None
    t0 = wave[0][0]
    pts = [(t, v) for t, v in wave if t - t0 > 6 * ui_ps * 1e-12]
    if len(pts) < 20:
        return None
    vmax = max(v for _, v in pts)
    vmin = min(v for _, v in pts)
    if vmax - vmin < 1e-9:
        return None
    lo = vmin + 0.2 * (vmax - vmin)
    hi = vmin + 0.8 * (vmax - vmin)
    edges = []
    i = 0
    while i < len(pts) - 1:
        if pts[i][1] < lo <= pts[i + 1][1]:
            j = i
            while j < len(pts) - 1 and pts[j][1] < hi:
                j += 1
            dt = pts[j][0] - pts[i][0]
            if 0 < dt < 1.5 * ui_ps * 1e-12:
                edges.append(dt)
            i = j
        i += 1
    if not edges:
        return None
    edges.sort()
    return round(edges[len(edges) // 2] * 1e12, 1)

    def cross(frac):
        lvl = v0 + frac * span
        for (ta, a), (tb, b) in zip(w, w[1:]):
            if (a - lvl) * (b - lvl) <= 0 and a != b:
                return ta + (tb - ta) * (lvl - a) / (b - a)
        return None
    a, b = cross(0.2), cross(0.8)
    return None if a is None or b is None else (b - a) * 1000.0


def main():
    OUT.mkdir(exist_ok=True)
    widths = [3.5, 4.1, 5.5, 5.8, 6.2, 10.0]
    models = mk_models(widths)
    models.update(mk_ms_models())          # outer-layer escapes = microstrip
    rlgc_inc(SIM / "rlgc_ddr.inc", models)
    key = {w: f"SL_{w:.1f}".replace(".", "P") for w in widths}
    mskey = {w: f"MS_{w:.1f}".replace(".", "P") for w in MS}

    res = dict(models={k: dict(Z0=round(v["Z0"], 2), C=v["C"], L=v["L"], R=v["R"])
                       for k, v in models.items()},
               td_ps_in=round(TD, 1), vddq=VDDQ, ui_ps=UI_PS,
               r_drv=R_DRV, r_odt=R_ODT, tests=[])

    tests = [
        ("DQ0 (U1-DRAM, 1809 mil)", 1.2,
         [(key[3.5], 872.4), (key[5.5], 914.0), (mskey[10.0], 22.3)]),
        ("DQ8 (U1-DRAM, 1863 mil)", 1.2,
         [(key[3.5], 821.3), (key[5.5], 1019.4), (mskey[10.0], 22.3)]),
        ("DQS0_T (1683 mil)", 1.2,
         [(key[4.1], 648.3), (key[5.5], 1012.7), (mskey[5.8], 22.3)]),
    ]
    for name, vh, secs in tests:
        secs = [(m, l) for m, l in secs if m in models]
        r = run(name, vh, secs)
        ri = run_ideal(name, vh, secs)
        if r and ri:
            r["ideal_eye_mV"] = ri["eye_height_mV"]
            r["ideal_swing_mV"] = (ri["vmax"] - ri["vmin"]) * 1e3
            r["ideal_eye_width_ps"] = ri["eye_width_ps"]
        if r:
            res["tests"].append(r)
            print(f"  {name}: IBIS eyeH={r['eye_height_mV']:.0f} mV  "
                  f"eyeW={r['eye_width_ps']:.0f} ps  "
                  f"vpp={(r['vmax']-r['vmin'])*1e3:.0f} mV   |  "
                  f"R-model eyeH={r.get('ideal_eye_mV',0):.0f} mV")
        else:
            print(f"  {name}: no waveform")

    # ---------------- official AMD POD12 runs (this round) ----------------
    print("AMD/Xilinx zynquplus.ibs POD12 ->", SIM / "ibis_pod12.inc")
    meta = pod12_include()
    pod12_include(path=SIM / "ibis_pod12_noclamp.inc", clamps=False)
    res["amd_models"] = meta
    res["amd_src"] = dict(ibs=str(AMD_IBS), pkg=str(AMD_PKG),
                          ibs_rev="IBIS 5.1 Rev 2.12 (2026-06-01)")
    res["pkg"] = {}
    for ch, pin in U1_PIN.items():
        Rp, Lp, Cp = pkg_rlc(pin)
        res["pkg"][pin] = dict(R=round(Rp, 6), L=Lp, C=Cp, ch=ch)

    def show(r, extra=""):
        if "error" in r or not r.get("eye_height_mV"):
            print(f"  {r.get('title','?'):26s} {r.get('model','?'):24s} FAILED "
                  f"{str(r.get('error',''))[-120:]}")
            return
        print(f"  {r['title']:26s} {r['model']:24s} eyeH={r['eye_height_mV']:6.0f} mV "
              f"eyeW={r['eye_width_ps']:5.0f} ps  edge={r.get('edge_ps')} ps "
              f"hi={r['high_lvl_mV']:4.0f} lo={r['low_lvl_mV']:4.0f} mV {extra}")

    amd = []
    for name, vh, secs in tests:
        secs = [(m, l) for m, l in secs if m in models]
        for model in ("HP_POD12_DCI_M_OUT40", "HP_POD12_DCI_M_OUT48",
                      "HP_POD12_DCI_M_OUT60"):
            r = run_amd(name, model, secs, term="vddq40", use_pkg=True, clamps=False,
                        keep_wave=(model == "HP_POD12_DCI_M_OUT40"))
            amd.append(r)
            show(r)
    res["amd_tests"] = amd

    secs_dq0 = [(m, l) for m, l in tests[0][2] if m in models]
    dq0 = "DQ0 (U1-DRAM, 1809 mil)"

    # the F/M/S corners differ only in slew, and tlsim derives the output edge
    # from the V/I tables + C_comp + the *input* edge, so the vendor corner is
    # emulated with the stimulus edge instead.  Recorded as a sensitivity curve.
    slew = []
    for tag, edge_ns in (("F ~80 ps", 0.08), ("M ~208 ps", 0.20),
                         ("slow ~470 ps", 0.45), ("very slow", 0.8)):
        r = run_amd(dq0, "HP_POD12_DCI_M_OUT40", secs_dq0, term="vddq40",
                    use_pkg=True, clamps=False, stim_edge_ns=edge_ns, keep_wave=False)
        r["variant"] = tag
        slew.append(r)
        show(r, f"[{tag}]")
    res["amd_slew"] = slew

    # termination study on DQ0 (40 ohm, typical impedance corner)
    term_study = []
    for term in ("gnd40", "split", "vddq40"):
        r = run_amd(dq0, "HP_POD12_DCI_M_OUT40", secs_dq0, term=term,
                    use_pkg=True, clamps=False, keep_wave=False)
        r["variant"] = term
        term_study.append(r)
        show(r, f"[term={term}]")
    res["term_study"] = term_study

    # package / clamp sensitivity on DQ0
    sens = []
    for tag, kw in (("no package", dict(use_pkg=False)),
                    ("with IBIS clamps", dict(use_pkg=True, clamps=True))):
        r = run_amd(dq0, "HP_POD12_DCI_M_OUT40", secs_dq0, term="vddq40",
                    keep_wave=False, **kw)
        r["variant"] = tag
        sens.append(r)
        show(r, f"[{tag}]")
    res["amd_sensitivity"] = sens

    # static operating point check for the converted driver
    dc = {}
    for term in ("gnd40", "split", "vddq40"):
        dc[term] = dc_check("HP_POD12_DCI_M_OUT40", term, use_pkg=True,
                            ibs_file="ibis_pod12_noclamp.inc")
        print(f"  DC {term:7s}: high={dc[term]['high']} low={dc[term]['low']} V")
    dc["baseline_model"] = dc_check("cds_out_gen_1p2v_40_1pf", "vddq40",
                                    use_pkg=True, ibs_file="ibis_ddr.inc")
    print("  DC baseline model (VDDQ term):", dc["baseline_model"])
    res["amd_dc"] = dc

    # ---- which knob sets the output edge?  (self-check, re-run every time) --
    # the report claims tlsim takes the output transition from the V/I tables +
    # C_comp + the input stimulus edge and *ignores* AC_PARAM's RISE_TIME; this
    # block re-measures that instead of leaving it as a hand-run experiment.
    et = dict(rise_time_sweep=[], stim_edge_sweep=[])
    for lab, rt in (("as shipped", None), ("100 ps", 1e-10), ("619 ps", 6.19e-10),
                    ("1 ns", 1e-9), ("2 ns", 2e-9)):
        w = edge_test("HP_POD12_DCI_M_OUT40", ibs_file="ibis_pod12_noclamp.inc",
                      r_load=R_ODT, t_edge_ps=EDGE_PS,
                      rise_time=rt, fall_time=rt)
        et["rise_time_sweep"].append(dict(label=lab, rise_time=rt,
                                          edge_20_80_ps=None if not w else t20_80(w)))
        print(f"  edge test: RISE_TIME = {lab:10s} -> 20-80% = "
              f"{'n/a' if not w else f'{t20_80(w):.1f} ps'}")
    for te in (20.0, 80.0, 200.0, 450.0, 800.0):
        w = edge_test("HP_POD12_DCI_M_OUT40", ibs_file="ibis_pod12_noclamp.inc",
                      r_load=R_ODT, t_edge_ps=te)
        et["stim_edge_sweep"].append(dict(stim_edge_ps=te,
                                          edge_20_80_ps=None if not w else t20_80(w)))
        print(f"  edge test: stimulus {te:5.0f} ps -> 20-80% = "
              f"{'n/a' if not w else f'{t20_80(w):.1f} ps'}")
    res["edge_test"] = et

    (OUT / "ddr4_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("wrote", OUT / "ddr4_results.json")


if __name__ == "__main__":
    main()
