#!/usr/bin/env python3
"""
PCIe link channel for the ZCU106, built from the official Xilinx GTH IBIS-AMI
kit plus this board's field-solved interconnect.

Topology (UG1183 Figure 2, with the board dropped in for "channel"):

    ideal TX source -> Tx_Die(TT) -> Tx_Package -> BOARD -> Rx_Package
                    -> Rx_Die -> RX AMI (high impedance)

Everything is handled as a *differential* 2-port in a 100 ohm reference:

  * Tx_Die  : unilateral buffer (Sdd12 = 0, Sdd21 = H(f), Sdd11 = 0) ->
              an ideal source with a frequency dependent source impedance
              taken from Sdd22, times the gain H(f).
  * packages, Rx_Die : official s4p, converted with the kit's port map.
  * BOARD   : this board.  Odd-mode RLGC solved with bem2d (si/build_xs.py):
              Zdiff = 77.0 ohm, C_odd = 1.5974e-10 F/m, L_odd = 2.3682e-7 H/m,
              R(DC) = 10.322 ohm/m; vias and the 100 nF AC-coupling caps as in
              si/build_freq.py, all doubled for the differential loop.

link_transfer() returns the voltage transfer from the ideal TX source to the
RX AMI input node - the impulse response the IBIS-AMI models expect.
"""

import math
from pathlib import Path
import os

import numpy as np

import s4p

# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
KIT = Path(os.environ.get("GTH_KIT", r"D:\Programs\candence\Xilinx_UltraScalePlus_GTH_AMI_Kit_R1p1"))
SP = KIT / "S-Parameters"
MIL = 2.54e-5
ZREF = 100.0             # differential reference impedance

TX0_MIL_STRIP = 2186.5154
TX0_MIL_MICRO = 284.3756
TX1_MIL_STRIP = 2273.3886
TX1_MIL_MICRO = 276.6948
TX2_MIL_STRIP = 2210.5569
TX2_MIL_MICRO = 278.3459
TX3_MIL_STRIP = 2124.6132
TX3_MIL_MICRO = 281.6339

LANES = {
    "TX0": (TX0_MIL_STRIP, TX0_MIL_MICRO),
    "TX1": (TX1_MIL_STRIP, TX1_MIL_MICRO),
    "TX2": (TX2_MIL_STRIP, TX2_MIL_MICRO),
    "TX3": (TX3_MIL_STRIP, TX3_MIL_MICRO),
}

# odd-mode (differential) line: 03_SIG1/12_SIG4 stripline, 4.2 mil, 4.2 mil gap
L_ODD = 2.3682e-07
C_ODD = 1.5974e-10
R_DC_ODD = 10.322
ZDIFF_LINE = 2.0 * math.sqrt(L_ODD / C_ODD)          # 77.0 ohm

# microstrip escape (TOP/BOTTOM, 5.5 mil) - only ~7 mm of the route
MS_C_SE = 1.1287e-10
MS_L_SE = 2.9163e-07
MS_R_DC = 4.1081
MS_ZDIFF = 2.0 * math.sqrt(MS_L_SE / MS_C_SE)         # 101.6 ohm

DF = 0.012               # FR408HR dissipation factor used in earlier rounds
VIA_L = 0.40e-9
VIA_C = 0.40e-12
CAP_NF = 100.0
CAP_ESL = 0.45e-9


def abcd(a, b):
    a1, b1, c1, d1 = a
    a2, b2, c2, d2 = b
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2)


def line_abcd(zc, gamma, length_m):
    gl = gamma * length_m
    return (np.cosh(gl), zc * np.sinh(gl),
            np.sinh(gl) / zc, np.cosh(gl))


def series(z):
    return (np.ones_like(z), z, np.zeros_like(z), np.ones_like(z))


def shunt(y):
    return (np.ones_like(y), np.zeros_like(y), y, np.ones_like(y))


def line_gamma(f, l_odd, c_odd, r_dc):
    w = 2 * np.pi * f
    r_ac = r_dc * np.sqrt(np.maximum(f / 1e9, 0.01))
    g = w * c_odd * DF
    return np.sqrt((r_ac + 1j * w * l_odd) * (g + 1j * w * c_odd))


def board_channel(f, strip_mil=TX0_MIL_STRIP, micro_mil=TX0_MIL_MICRO):
    """Differential ABCD of the board route (via/cap/via as in build_freq.py)."""
    w = 2 * np.pi * f
    xs = line_abcd(ZDIFF_LINE,
                   line_gamma(f, L_ODD, C_ODD, R_DC_ODD), strip_mil * MIL)
    xm = line_abcd(MS_ZDIFF,
                   line_gamma(f, MS_L_SE * 2, MS_C_SE / 2, MS_R_DC),
                   micro_mil * MIL)
    # differential loop: the two via inductances add (0.8 nH), while the two
    # via pad capacitances to ground appear in series -> C/2
    via = abcd(series(2j * w * VIA_L), shunt(1j * w * VIA_C / 2))
    cap = series(2.0 * (1.0 / (1j * w * CAP_NF * 1e-9) + 1j * w * CAP_ESL))
    x = abcd(via, xs)
    x = abcd(x, via)
    x = abcd(x, cap)
    x = abcd(x, xm)
    return abcd(x, via)


def load_sdd(name, perm=None):
    f, S, _ = s4p.read_touchstone(SP / name)
    return f, s4p.sdd_2port(S, perm or (0, 1, 2, 3))


def interp(fnew, fold, s):
    return [np.interp(fnew, fold, x.real) + 1j * np.interp(fnew, fold, x.imag)
            for x in s]


def link_transfer(f, lane="TX0", use_packages=True, use_rxdie=True,
                  termination="open"):
    """Voltage transfer ideal-TX-source -> RX AMI node over frequency f [Hz].

    termination="open"  : the RX AMI input (the default for the AMI flow)
    termination="match" : a 100 ohm differential load - the reference used when
                          a channel is described by its own S-parameters
    """
    f = np.asarray(f, dtype=float)
    ftx, txd = load_sdd("Xilinx_ultrascaleplus_gth_Tx_TT.s4p")
    txd = interp(f, ftx, txd)
    zs = ZREF * (1 + txd[3]) / (1 - txd[3])      # TX source impedance
    h_tx = txd[2]                                # TX die forward gain

    chain = (np.ones_like(f), np.zeros_like(f), np.zeros_like(f), np.ones_like(f))
    if use_packages:
        ftp, txp = load_sdd("Xilinx_ultrascaleplus_gth_Tx_Package.s4p")
        chain = abcd(chain, s4p.s2abcd(*interp(f, ftp, txp), z0=ZREF))
    s_mil, m_mil = LANES[lane]
    chain = abcd(chain, board_channel(f, s_mil, m_mil))
    if use_packages:
        frp, rxp = load_sdd("Xilinx_ultrascaleplus_gth_Rx_Package.s4p", (1, 0, 3, 2))
        chain = abcd(chain, s4p.s2abcd(*interp(f, frp, rxp), z0=ZREF))
    if use_rxdie:
        frd, rxd = load_sdd("Xilinx_ultrascaleplus_gth_Rx.s4p")
        chain = abcd(chain, s4p.s2abcd(*interp(f, frd, rxd), z0=ZREF))

    A, B, C, D = chain
    if termination == "match":
        # I2 = V2 / ZREF  ->  V2/V1 = ZREF/(A*ZREF + B)
        v2 = ZREF / (A * ZREF + B)
        zin = (A * ZREF + B) / (C * ZREF + D)
    else:
        v2 = 1.0 / A
        zin = A / C
    vin = zin / (zin + zs)                 # source divider
    return h_tx * vin * v2                 # V(rx node) / V(ideal tx)


def impulse_response(f, H, n_fft=None):
    """IFFT of H(f) given on a uniform grid starting at f[0]."""
    df = f[1] - f[0]
    k = len(f)
    N = n_fft or 2 * (k - 1)
    Hfull = np.zeros(N, dtype=complex)
    Hfull[:k] = H
    if k > 1:
        Hfull[-1:-(k - 1):-1] = np.conj(H[1:])
    h = np.fft.ifft(Hfull) * N             # sum(h) == H(f0)
    dt = 1.0 / (N * df)
    return np.arange(N) * dt, h.real


if __name__ == "__main__":
    f = np.linspace(1e6, 40e9, 401)
    for lane in LANES:
        H = link_transfer(f, lane)
        il = -20 * np.log10(np.abs(H) + 1e-30)
        nyq = 4e9 if "Gen3" else 0
        print(f"{lane}: IL@2.5GHz={il[np.argmin(abs(f-2.5e9))]:5.2f} dB  "
              f"@4GHz={il[np.argmin(abs(f-4e9))]:5.2f} dB  "
              f"@5GHz={il[np.argmin(abs(f-5e9))]:5.2f} dB  "
              f"@8GHz={il[np.argmin(abs(f-8e9))]:5.2f} dB")
