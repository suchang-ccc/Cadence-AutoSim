#!/usr/bin/env python3
"""
PCIe on the ZCU106 with the official Xilinx UltraScale+ GTH models.

The board's PCIe lanes are on the PL GTH quad 224 (pins MGTHTXP3_224 /
MGTHRXP3_224 ...), so the GTH IBIS-AMI kit R1.1 applies directly.

What this script computes:
  * the differential channel response with the official models in the link
    (Tx_Die(TT) -> Tx_Package -> board -> Rx_Package -> Rx_Die, UG1183 Figure 2),
    and the insertion loss of each of the four lanes at the Gen1/Gen2/Gen3
    Nyquist frequencies;
  * the corresponding eye at the RX die node for 5 GT/s (Gen2) and 8 GT/s
    (Gen3), with the transmitter de-emphasis taken from UG1183 Table 2
    (TXPOSTCURSOR -> dB).

What it does NOT do: the receiver CTLE/DFE/CDR of the official RX AMI model.
The vendor DLLs are present and were driven directly (see si/ami_host.py and
si/README.md); the AMI_Init calling convention was reverse engineered from the
shipped binaries, but the RX model still rejects the arguments from a
hand-built host, so the eye here is the *unequalised* eye at the RX die node
with the official analog models.  That is stated in the report as well.
"""

import json
from pathlib import Path
import os

import numpy as np

import pcie_link as P
import s4p

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
NURSE = {"Gen1 (2.5 GT/s)": 1.25e9, "Gen2 (5 GT/s)": 2.5e9,
         "Gen3 (8 GT/s)": 4.0e9, "Gen4 (16 GT/s)": 8.0e9}
# UG1183 Table 2: TXPOSTCURSOR setting -> de-emphasis (dB)
POSTCURSOR_DB = {0: 0.0, 5: 1.05, 10: 2.27, 14: 3.45, 16: 4.05, 20: 5.58}
# UG1183 Table 2: TXDIFFCTRL -> peak-to-peak differential swing (V), TT corner
DIFFCTRL_VPP = {24: 0.873, 28: 1.000, 20: 0.707, 16: 0.523}


def prbs13(n):
    reg = 0x1FFF
    out = []
    for _ in range(n):
        out.append(reg & 1)
        fb = ((reg >> 12) & 1) ^ ((reg >> 11) & 1) ^ ((reg >> 10) & 1) ^ ((reg >> 8) & 1)
        reg = ((reg << 1) | fb) & 0x1FFF
    return np.array(out, dtype=float)


def ffe(bit, post_db):
    """3-tap TX FFE (main + one post cursor), normalised to unity steady state."""
    if post_db <= 0:
        return bit - 0.5
    r = 10 ** (-post_db / 20.0)           # de-emphasised bit level
    # UG1183 Table 2 defines Emphasis = 20log10(V_HIGH / V_LOW) with V_HIGH the
    # emphasised (first) bit after a transition, so the repeated bits must come
    # out at r x the transition bit - as in build_compliance.ffe3().
    main = 1.0 / (1.0 + r)
    post = main * (1.0 - r)
    w = main * bit - post * np.concatenate([[bit[0]], bit[:-1]])
    return w - w.mean()


def channel(freqs, lane, use_packages=True, use_rxdie=True):
    return P.link_transfer(freqs, lane, use_packages=use_packages,
                           use_rxdie=use_rxdie)


def eye_at_rx(lane, rate, post_db=0.0, diffctrl=24, nbits=4000, spb=32,
              use_packages=True, use_rxdie=True):
    ui = 1.0 / rate
    dt = ui / spb
    # frequency grid for the impulse response: df chosen so that dt matches
    nfft = 4096
    df = 1.0 / (nfft * dt)
    f = np.arange(0, nfft // 2 + 1) * df
    f[0] = 1e3            # avoid the 1/jwC singularity of the AC-coupling caps
    H = channel(f, lane, use_packages, use_rxdie)
    # taper the response above the model's range to avoid IFFT ringing
    hi = 30e9
    taper = np.where(f <= hi, 1.0, np.maximum(0.0, 1 - (f - hi) / 10e9))
    h = np.fft.irfft(H * taper, n=nfft)
    h = np.roll(h, 0)
    bit = prbs13(nbits + 40)
    w = ffe(bit, post_db) * DIFFCTRL_VPP[diffctrl]
    tx = np.repeat(w, spb)[:nfft]
    rx = np.convolve(tx, h, mode="full")[:nfft]
    # eye: sample at the middle of each UI after settling
    skip = 40
    nui = (nfft // spb) - skip
    idx = (np.arange(nui) * spb + spb // 2 + skip * spb)
    vals = rx[idx]
    hi_v, lo_v = np.percentile(vals, 90), np.percentile(vals, 10)
    # eye height: vertical opening between the two level clusters at the
    # nominal sampling phase (5%/95% of the sampled distribution)
    eye_h = hi_v - lo_v
    # eye width: scan the sampling phase and keep the opening above 50%
    widths = []
    for ph in range(spb):
        v = rx[np.arange(nui) * spb + ph + skip * spb]
        widths.append(np.percentile(v, 95) - np.percentile(v, 5))
    widths = np.array(widths)
    ok = widths >= 0.5 * eye_h
    eye_w = ok.sum() * dt
    return dict(lane=lane, rate_gbs=rate / 1e9, post_db=post_db,
                eye_mV=eye_h * 1e3, eye_ps=eye_w * 1e12,
                hi_mV=hi_v * 1e3, lo_mV=lo_v * 1e3,
                vpp_mV=(hi_v - lo_v) * 1e3,
                wave=[[round(i * dt * 1e12, 2), round(v, 5)]
                      for i, v in enumerate(rx[skip * spb: skip * spb + 220 * spb])])


def main():
    OUT.mkdir(exist_ok=True)
    fres = np.linspace(1e6, 20e9, 401)
    res = dict(lanes={}, nyquist=list(NURSE), kit="GTH AMI kit R1.1",
               pins="MGTHTXP3_224 / MGTHRXP3_224 (quad 224)")
    for lane in P.LANES:
        H = channel(fres, lane)
        il = -20 * np.log10(np.abs(H) + 1e-30)
        row = {name: float(il[np.argmin(abs(fres - f0))]) for name, f0 in NURSE.items()}
        res["lanes"][lane] = dict(il=[[round(fr / 1e9, 3), round(x, 3)]
                                      for fr, x in zip(fres, il)], il_at=row)
        print(f"{lane}: " + "  ".join(f"{k} {v:5.2f} dB" for k, v in row.items()))

    # eyes: Gen2 without emphasis, Gen3 with the -3.5 dB preset
    eyes = []
    for lane in ("TX0", "TX1", "TX2", "TX3"):
        eyes.append(eye_at_rx(lane, 5e9, post_db=0.0))
        eyes.append(eye_at_rx(lane, 8e9, post_db=POSTCURSOR_DB[14]))
    res["eyes"] = eyes
    for e in eyes:
        print(f"  {e['lane']} {e['rate_gbs']:.0f} GT/s post={e['post_db']:.2f} dB: "
              f"eye {e['eye_mV']:.0f} mV / {e['eye_ps']:.0f} ps  "
              f"(hi {e['hi_mV']:.0f} lo {e['lo_mV']:.0f} mV)")
    # emphasis sensitivity for Gen3
    sens = [eye_at_rx("TX0", 8e9, post_db=POSTCURSOR_DB[k]) for k in (0, 5, 10, 14, 20)]
    res["gen3_emphasis_sweep"] = [dict(post=k, **{kk: v for kk, v in e.items()
                                                  if kk != "wave"})
                                  for k, e in zip((0, 5, 10, 14, 20), sens)]
    (OUT / "pcie_gth_results.json").write_text(json.dumps(res, indent=1),
                                               encoding="utf-8")
    print("wrote", OUT / "pcie_gth_results.json")


if __name__ == "__main__":
    main()
