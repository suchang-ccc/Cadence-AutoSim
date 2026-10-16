#!/usr/bin/env python3
"""
Frequency-domain channel metrics for the ZCU106 PCIe link.

The distributed line parameters come from the Cadence field solver (bem2d, run
by signoise on this board); the cascade into S-parameters uses the standard
ABCD chain algebra for lossy lines plus lumped via and AC-coupling models.
The result is checked against the tlsim time-domain step response.
"""

import cmath
import json
import math
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
MIL = 2.54e-5

# Cadence bem2d per-unit-length results (this board's stackup)
SL = dict(C=1.5223e-10, L=2.4851e-07, R=10.322, name="03_SIG1/12_SIG4 stripline 4.2 mil")
MS = dict(C=1.1287e-10, L=2.9163e-07, R=4.1081, name="TOP/BOTTOM microstrip 5.5 mil")
DF = 0.012          # FR408HR dissipation factor (board library says 0.035 at 1 MHz)

CAP_NF = 100.0
CAP_ESL = 0.45e-9
VIA_L = 0.40e-9     # through via, V19H10 padstack on a 61 mil stack
VIA_C = 0.40e-12


def line_abcd(tl, length_m, f):
    w = 2 * math.pi * f
    r_ac = tl["R"] * math.sqrt(max(f / 1e9, 0.01))
    g = w * tl["C"] * DF
    z = complex(r_ac, w * tl["L"])
    y = complex(g, w * tl["C"])
    gamma = cmath.sqrt(z * y)
    zc = cmath.sqrt(z / y)
    gl = gamma * length_m
    return (cmath.cosh(gl), zc * cmath.sinh(gl),
            cmath.sinh(gl) / zc, cmath.cosh(gl)), zc, gamma


def casc(a, b):
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3])


def series_imp(z):
    return (1, z, 0, 1)


def shunt_adm(y):
    return (1, 0, y, 1)


def channel_abcd(f, milA, milB):
    """TX via - stripline - via - 100 nF cap (with ESL) - microstrip - RX via."""
    w = 2 * math.pi * f
    xs = line_abcd(SL, milA * MIL, f)[0]
    xm = line_abcd(MS, milB * MIL, f)[0]
    via = series_imp(complex(0, w * VIA_L))
    via = casc(via, shunt_adm(complex(0, w * VIA_C)))
    cap = series_imp(complex(0, w * CAP_ESL) + 1.0 / complex(0, w * CAP_NF * 1e-9))
    return casc(casc(casc(casc(via, xs), via), cap), casc(xm, via))


def s_params(x, z0=50.0):
    a, b, c, d = x
    den = a + b / z0 + c * z0 + d
    s11 = (a + b / z0 - c * z0 - d) / den
    s21 = 2.0 / den
    return s11, s21


def main():
    lanes = [("TX0", 2186.5154, 284.3756), ("TX1", 2273.3886, 276.6948),
             ("TX2", 2210.5569, 278.3459), ("TX3", 2124.6132, 281.6339)]
    freqs = [x / 20 for x in range(2, 301)]        # 0.1 .. 15 GHz
    out = dict(desc="S-parameters from the Cadence-solved RLGC", lanes={}, line_types={})

    for name, tl in (("SL_4P2", SL), ("MS_5P5", MS)):
        pts = []
        for fghz in freqs:
            _, zc, gamma = line_abcd(tl, 1.0, fghz * 1e9)
            att = 8.686 * gamma.real
            pts.append([round(fghz, 3), round(att, 4), round(abs(zc), 2)])
        out["line_types"][name] = dict(desc=tl["name"], units="dB/m, ohm", points=pts)

    for lane, milA, milB in lanes:
        il, rl = [], []
        for fghz in freqs:
            x = channel_abcd(fghz * 1e9, milA, milB)
            s11, s21 = s_params(x)
            il.append([round(fghz, 3), round(-20 * math.log10(abs(s21) + 1e-12), 3)])
            rl.append([round(fghz, 3), round(-20 * math.log10(abs(s11) + 1e-12), 3)])

        def worst_in_window(pts, f0, frac=0.12):
            """Worst case within +-frac of f0 - the channel response ripples
            because the 40 ohm line sits in a 50 ohm reference, so quoting the
            first sample above f0 would cherry-pick a null."""
            vals = [v for f, v in pts if abs(f - f0) <= frac * f0]
            return max(vals) if vals else None

        def at(pts, g):
            return next((p[1] for p in pts if p[0] >= g), pts[-1][1])
        out["lanes"][lane] = dict(
            milA=milA, milB=milB, il=il, rl=rl,
            il_2p5=at(il, 2.5), il_4p0=at(il, 4.0), il_8p0=at(il, 8.0),
            il_2p5_max=worst_in_window(il, 2.5), il_4p0_max=worst_in_window(il, 4.0),
            il_8p0_max=worst_in_window(il, 8.0),
            rl_2p5=at(rl, 2.5), rl_4p0=at(rl, 4.0))

    (OUT / "freq_results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", OUT / "freq_results.json")
    for lane, d in out["lanes"].items():
        print(f"  {lane}: IL@4GHz nominal={d['il_4p0']:.2f} dB worst={d['il_4p0_max']:.2f} dB  "
              f"IL@8GHz worst={d['il_8p0_max']:.2f} dB  RL@4GHz={d['rl_4p0']:.1f} dB")


if __name__ == "__main__":
    main()
