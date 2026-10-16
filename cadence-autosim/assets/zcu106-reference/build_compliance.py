#!/usr/bin/env python3
"""
PCIe compliance checks for the ZCU106 board channel.

Criteria come from the compliance artefacts that ship with the tool
(`share/topxp/sample_masks/`), so every limit is traceable to a file:

    pcie3_eye.maskfile        hexagon eye mask, 0.30 UI x 25 mV  (Rx side)
    pcie4_il.maskfile         insertion-loss mask
    generic_il.maskfile       generic insertion-loss mask
    generic_rl_tx.maskfile    generic Tx return-loss mask

Three checks are produced:

  1. insertion loss of every lane against the IL masks;
  2. return loss against the RL mask;
  3. the 8 GT/s eye at the receiver, using the PCIe reference transmitter
     (0.804 V swing, 1 pF, -3.5 dB FFE per the compliance kit's
     tx_pcieg3_spec.sp) together with the official GTH analog models, followed
     by the specification's reference receiver CTLE, checked against the
     pcie3_eye mask.

The reference receiver CTLE is re-implemented here from its documented
specification parameters (zero at f0, AC/DC gain step `dbl`; the Cadence
spec-receiver parameter file amictwf_pcix3spec_tr_v1.ami cites PCIe Table 4.20
for the 15 GHz gain-bandwidth and 6 GHz AC bandwidth, and notes that 9.2425 dB
is "a gain of 3x").  The filter is therefore a 1-zero / 1-pole shelf:

    H(s) = Gdc * (1 + s/wz) / (1 + s/wp),   Gdc = 10^(-dbl/20), wp = wz/Gdc

so that the DC-to-AC gain step is exactly `dbl` and the high-frequency gain is
unity.
"""

import json
import math
from pathlib import Path
import os

import numpy as np

import pcie_link as P

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
MASKS = Path(os.environ.get("SPBROOT", r"D:\tools\Cadence\SPB_24.1")) / "share" / "topxp" / "sample_masks"

RATE = 8.0e9
UI = 1.0 / RATE
SPB = 32
CTLE_DB = 9.2425          # AC vs DC gain of the spec reference Rx CTLE ("3x")
CTLE_FZ = 1.5e9           # zero frequency from the spec receiver .ami


def read_mask(name):
    """Parse a Cadence maskfile; returns dict(kind, segs/vertices, unit)."""
    txt = (MASKS / name).read_text(errors="ignore").splitlines()
    xunit = yunit = ""
    kind = None
    verts, segs = [], []
    for raw in txt:
        s = raw.strip()
        if not s or s.startswith("!"):
            continue
        if s.upper().startswith("UI") or s.upper().startswith("HZ") or \
           s.upper().startswith("GHZ"):
            parts = s.split()
            xunit, yunit = parts[0], parts[1] if len(parts) > 1 else ""
            continue
        up = s.upper()
        if up in ("HEXAGON", "POLYGON"):
            kind = up.lower()
            continue
        if up == "BORDERS":
            kind = "borders"
            continue
        try:
            v = [float(x) for x in s.split()]
        except ValueError:
            continue
        if kind == "hexagon":
            verts.append(v)
        elif kind == "borders":
            if len(v) == 1:            # the border-count line
                continue
            segs.append(v)
    return dict(kind=kind, verts=verts, segs=segs, xunit=xunit, yunit=yunit)


def hexagon_xy(mask):
    v = mask["verts"]
    if len(v) < 2:
        return None
    # the mask file uses a 0..1 UI axis; the eye is evaluated with its centre
    # at 0, so shift the mask by half a UI
    xs = [x - 0.5 for x in v[0]]
    ys = v[1]
    return list(zip(xs + xs[:1], ys + ys[:1]))


def in_hexagon(pts, hexa):
    """Vectorised point-in-polygon (ray casting)."""
    if hexa is None:
        return np.zeros(len(pts), dtype=bool)
    poly = np.array(hexa)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cond = ((y1 > y) != (y2 > y))
        with np.errstate(divide="ignore", invalid="ignore"):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + 0.0) + x1
        inside ^= cond & (x < xin)
    return inside


def mask_value_at(mask, f):
    """Linear interpolation of a borders mask (first column Hz -> dB)."""
    for seg in mask["segs"]:
        f1, a1, f2, a2 = seg[0], seg[1], seg[2], seg[3]
        if f1 <= f <= f2:
            return abs(a1 + (a2 - a1) * (f - f1) / (f2 - f1))
    return None


def ctle_response(f, dbl_db=CTLE_DB, fz=CTLE_FZ):
    """Specification reference receiver CTLE: unity DC gain, a zero at `fz`
    and one pole placed so that the AC/DC gain step equals `dbl_db`
    (the parameter file notes 9.2425 dB == a gain of 3x)."""
    ratio = 10 ** (dbl_db / 20.0)
    return (1 + 1j * f / fz) / (1 + 1j * f / (fz * ratio))


def ffe3(bit, post_db):
    """Reference transmitter 3-tap FFE with `post_db` post-cursor de-emphasis."""
    if post_db <= 0:
        return bit - bit.mean()
    r = 10 ** (-post_db / 20.0)
    main = 1.0 / (1.0 + r)
    post = main * (1.0 - r)
    w = main * bit - post * np.concatenate([[bit[0]], bit[:-1]])
    return w - w.mean()


def prbs13(n):
    reg = 0x1FFF
    out = np.empty(n)
    for i in range(n):
        out[i] = reg & 1
        fb = ((reg >> 12) & 1) ^ ((reg >> 11) & 1) ^ ((reg >> 10) & 1) ^ ((reg >> 8) & 1)
        reg = ((reg << 1) | fb) & 0x1FFF
    return out


def eye_and_mask(lane, post_db=3.45, swing=0.804, use_ctle=True, nbits=20000):
    nfft = 1 << 15
    dt = UI / SPB
    df = 1.0 / (nfft * dt)
    f = np.arange(0, nfft // 2 + 1) * df
    f[0] = 1e3
    H = P.link_transfer(f, lane)
    hi = 30e9
    H = H * np.where(f <= hi, 1.0, np.maximum(0.0, 1 - (f - hi) / 10e9))
    if use_ctle:
        H = H * ctle_response(f)
    h = np.fft.irfft(H, n=nfft)
    bit = prbs13(nbits + 64)
    tx = np.repeat(ffe3(bit, post_db) * swing, SPB)
    tx = np.resize(tx, nfft)
    rx = np.convolve(tx, h)[:nfft]
    skip = 64 * SPB
    nui = (nfft - skip) // SPB
    # eye opening versus sampling phase (in UI, 0..1 between bit boundaries)
    op = np.zeros(SPB)
    p5 = np.zeros(SPB)
    p95 = np.zeros(SPB)
    for ph in range(SPB):
        v = rx[skip + np.arange(nui) * SPB + ph]
        p5[ph], p95[ph] = np.percentile(v, 5), np.percentile(v, 95)
        op[ph] = p95[ph] - p5[ph]
    eye_h = op.max()
    eye_w = (op >= 0.5 * eye_h).sum() * dt
    # the eye is centred on the UI: 0.5 UI == the middle of the unit interval
    phases = np.arange(SPB) / SPB
    # eye centre: the phase farthest from the data transitions.  A transition
    # phase is one where many samples sit inside the middle voltage band.
    band = 0.25 * (np.percentile(rx, 99.9) - np.percentile(rx, 0.1))
    dens = np.array([np.mean(np.abs(rx[skip + np.arange(nui) * SPB + ph]) < band)
                     for ph in range(SPB)])
    quiet = dens < max(1e-9, 0.5 * dens.max())
    best_len = best_start = -1
    for start in range(SPB):                        # cyclic longest quiet run
        if not quiet[start]:
            continue
        ln = 0
        while ln < SPB and quiet[(start + ln) % SPB]:
            ln += 1
        if ln > best_len:
            best_len, best_start = ln, start
    ph_center = ((best_start + best_len / 2.0) % SPB) / SPB
    vals = np.concatenate([rx[skip + np.arange(nui) * SPB + ph] for ph in range(SPB)])
    ph_all = np.repeat(phases, nui)
    # re-centre the phase axis on the eye centre, with wraparound to [-0.5, 0.5)
    ph_rel = (ph_all - ph_center + 0.5) % 1.0 - 0.5
    return dict(eye_mV=eye_h * 1e3, eye_ps=eye_w * 1e12,
                hi_mV=p95.max() * 1e3, lo_mV=p5.min() * 1e3,
                phase_ui=ph_center,
                curve=[[round(float(p), 4), round(float(o) * 1e3, 1)]
                       for p, o in zip(phases, op)],
                points=[[round(float(p), 4), round(float(v), 6)]
                        for p, v in zip(ph_rel, vals)][::7])


def main():
    OUT.mkdir(exist_ok=True)
    res = dict(criteria={}, checks={}, eyes={})
    m_pcie3_eye = read_mask("pcie3_eye.maskfile")
    m_pcie4_il = read_mask("pcie4_il.maskfile")
    m_gen_il = read_mask("generic_il.maskfile")
    m_rl = read_mask("generic_rl_tx.maskfile")
    res["criteria"] = dict(
        pcie3_eye=hexagon_xy(m_pcie3_eye),
        pcie4_il=m_pcie4_il["segs"], generic_il=m_gen_il["segs"],
        rl_tx=m_rl["segs"])
    print("criteria loaded: eye hexagon", hexagon_xy(m_pcie3_eye))

    # ---- 1/2: IL and RL of every lane -----------------------------------
    fres = np.linspace(1e6, 15e9, 421)
    il_checks = {}
    for lane in ("TX0", "TX1", "TX2", "TX3"):
        # the loss masks describe the *channel* (TP1..TP2/TP3), so the die and
        # package models are excluded for this check
        A, B, C, D = P.board_channel(fres, *P.LANES[lane])
        s21 = 2.0 / (A + B / P.ZREF + C * P.ZREF + D)
        il = -20 * np.log10(np.abs(s21) + 1e-30)
        worst = {}
        for nm, m in (("pcie4_il", m_pcie4_il), ("generic_il", m_gen_il)):
            over, marg = [], []
            for fr, a in zip(fres, il):
                lim = mask_value_at(m, fr)
                if lim is None:
                    continue
                marg.append((float(lim - a), float(fr)))
                if a > lim:
                    over.append((float(fr), float(a), float(lim)))
            worst_margin, worst_f = min(marg, default=(None, None))
            worst[nm] = dict(pass_=not over, worst_excess_db=(
                max((o[1] - o[2] for o in over), default=0.0)),
                worst_margin_db=worst_margin,
                worst_margin_ghz=None if worst_f is None else worst_f / 1e9,
                n_violations=len(over), first_violation=over[0] if over else None)
        il_checks[lane] = dict(
            il_at={f"{fr/1e9:g}GHz": float(il[np.argmin(abs(fres - fr))])
                   for fr in (1.25e9, 2.5e9, 4e9, 8e9)},
            masks=worst)
        print(f"{lane}: IL@4GHz {il[np.argmin(abs(fres-4e9))]:.2f} dB  "
              + "  ".join(f"{k}:{'PASS' if v['pass_'] else 'FAIL'}"
                          for k, v in worst.items()))
    res["checks"]["insertion_loss"] = il_checks

    # ---- return loss -----------------------------------------------------
    rl_checks = {}
    for lane in ("TX0", "TX1", "TX2", "TX3"):
        A, B, C, D = P.board_channel(fres, *P.LANES[lane])
        s11 = (A + B / P.ZREF - C * P.ZREF - D) / (A + B / P.ZREF + C * P.ZREF + D)
        rl = -20 * np.log10(np.abs(s11) + 1e-30)
        over = []
        for fr, a in zip(fres, rl):
            lim = mask_value_at(m_rl, fr)
            if lim is not None and a < lim:
                over.append((float(fr), float(a), float(lim)))
        rl_checks[lane] = dict(
            rl_at={f"{fr/1e9:g}GHz": float(rl[np.argmin(abs(fres - fr))])
                   for fr in (0.5e9, 1.25e9, 2.5e9, 4e9, 8e9)},
            pass_=not over, n_violations=len(over),
            band_ghz=([over[0][0] / 1e9, over[-1][0] / 1e9] if over else None),
            min_rl_db=float(rl.min()),
            worst_deficit_db=min((o[2] - o[1] for o in over), default=0.0),
            first_violation=over[0] if over else None)
        print(f"{lane}: RL@2.5GHz {rl[np.argmin(abs(fres-2.5e9))]:.1f} dB  "
              f"mask {'PASS' if rl_checks[lane]['pass_'] else 'FAIL'}")
    res["checks"]["return_loss"] = rl_checks

    # ---- 3: eye vs pcie3_eye mask ---------------------------------------
    hexa = hexagon_xy(m_pcie3_eye)
    for lane in ("TX0", "TX1", "TX2", "TX3"):
        for use_ctle in (False, True):
            e = eye_and_mask(lane, use_ctle=use_ctle)
            pts = np.array(e["points"])
            # the mask spans 0.35..0.65 UI, so shift the sampling phase so that
            # the middle of the UI sits at 0.5 UI
            inside = in_hexagon(pts, hexa)
            e["mask_hits"] = int(inside.sum())
            e["mask_pass"] = bool(inside.sum() == 0)
            e["ctle"] = use_ctle
            res["eyes"][f"{lane}{'_ctle' if use_ctle else ''}"] = e
            print(f"  {lane} ctle={int(use_ctle)}: eye {e['eye_mV']:.0f} mV / "
                  f"{e['eye_ps']:.0f} ps, mask hits {e['mask_hits']}")

    (OUT / "compliance_results.json").write_text(json.dumps(res, indent=1),
                                                encoding="utf-8")
    print("wrote", OUT / "compliance_results.json")


if __name__ == "__main__":
    main()
