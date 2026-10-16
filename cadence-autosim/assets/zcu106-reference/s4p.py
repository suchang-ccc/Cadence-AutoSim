#!/usr/bin/env python3
"""
Touchstone reader + multi-port S-parameter algebra for the PCIe AMI link.

Written for the Xilinx UltraScale+ GTH IBIS-AMI kit (UG1183).  The link the kit
expects is

    TX AMI -> Tx_Die(TT/FF/SS) -> Tx_Package -> channel -> Rx_Package
           -> Rx_Die -> RX AMI

and every passive block is a 4-port Touchstone file (one differential pair =
two single-ended ports on each side).  Blocks are cascaded in the Z-parameter
domain (exact for arbitrary port counts) and the differential response is taken
from the mixed-mode conversion of the resulting 4-port.
"""

import re
from pathlib import Path
import os

import numpy as np


def read_touchstone(path):
    """(freqs_Hz, S[nf,np,np] complex, zref) for a .s2p/.s4p file.

    Supports the Touchstone 1.0 keyword line ("# GHz S MA R 50"), arbitrary
    line breaks inside a frequency block, and MA / DB / RI data formats.
    """
    path = Path(path)
    m = re.search(r"\.s(\d+)p$", path.name.lower())
    if not m:
        raise ValueError(f"cannot infer port count from {path.name}")
    nport = int(m.group(1))
    need = 2 * nport * nport
    fmt, zref, fscale = "MA", 50.0, 1e9
    nums = []
    for line in path.read_text(errors="ignore").splitlines():
        s = line.strip()
        if not s:
            continue
        if s[0] in "!#":
            if s[0] == "#":
                t = [x.upper() for x in s[1:].split()]
                fmt = "MA" if "MA" in t else ("DB" if "DB" in t else "RI")
                if "GHZ" in t:
                    fscale = 1e9
                elif "MHZ" in t:
                    fscale = 1e6
                elif "KHZ" in t:
                    fscale = 1e3
                elif "HZ" in t:
                    fscale = 1.0
                for i, x in enumerate(t):
                    if x == "R" and i + 1 < len(t):
                        try:
                            zref = float(t[i + 1])
                        except ValueError:
                            pass
            continue
        for tok in s.split():
            try:
                nums.append(float(tok))
            except ValueError:
                pass
    freqs, blocks = [], []
    i = 0
    while i + need < len(nums) + 1:
        f = nums[i]
        i += 1
        if i + need > len(nums):
            break
        freqs.append(f * fscale)
        blocks.append(nums[i:i + need])
        i += need
    freqs = np.array(freqs)
    if not blocks:
        raise ValueError(f"no data parsed from {path}")
    raw = np.array(blocks, dtype=float).reshape(len(freqs), nport * nport, 2)
    a, b = raw[..., 0], raw[..., 1]
    if fmt == "MA":
        S = a * np.exp(1j * np.deg2rad(b))
    elif fmt == "DB":
        S = 10 ** (a / 20.0) * np.exp(1j * np.deg2rad(b))
    else:
        S = a + 1j * b
    return freqs, S.reshape(len(freqs), nport, nport), zref


def reorder(S, perm):
    """S'[i,j] = S[perm[i], perm[j]] - put a block into canonical port order."""
    p = np.array(perm, dtype=int)
    return S[:, p][:, :, p]


def s_to_z(S, zref):
    n = S.shape[-1]
    out = np.empty_like(S)
    I = np.eye(n)
    for f in range(S.shape[0]):
        A = I - S[f]
        out[f] = zref * (I + S[f]) @ np.linalg.inv(A)
    return out


def z_to_s(Z, zref):
    n = Z.shape[-1]
    out = np.empty_like(Z)
    I = np.eye(n)
    for f in range(Z.shape[0]):
        A = Z[f] + zref * I
        out[f] = (Z[f] - zref * I) @ np.linalg.inv(A)
    return out


def cascade(Za, Zb, na_int=2, nb_int=2):
    """Connect the last `na_int` ports of A to the first `nb_int` ports of B."""
    nf = Za.shape[0]
    out = np.empty((nf, Za.shape[1] - na_int + Zb.shape[1] - nb_int,
                    Za.shape[1] - na_int + Zb.shape[1] - nb_int), dtype=complex)
    for f in range(nf):
        na, nb = Za.shape[1], Zb.shape[1]
        ae, ai = slice(0, na - na_int), slice(na - na_int, na)
        bi, bf = slice(0, nb_int), slice(nb_int, nb)
        Zae, Zai = Za[f][ae, ae], Za[f][ae, ai]
        Zie, Zii = Za[f][ai, ae], Za[f][ai, ai]
        Zii2, Zif = Zb[f][bi, bi], Zb[f][bi, bf]
        Zfi, Zff = Zb[f][bf, bi], Zb[f][bf, bf]
        M = np.linalg.inv(Zii + Zii2)
        out[f][ae, ae] = Zae - Zai @ M @ Zie
        out[f][ae, bf] = -(Zai @ M @ Zif)
        out[f][bf, ae] = -(Zfi @ M @ Zie)
        out[f][bf, bf] = Zff - Zfi @ M @ Zif
    return out


def sdd21(S, ipp, ipn, opp, opn):
    """SDD21 given explicit single-ended port indices (0-based)."""
    return 0.5 * (S[:, opp, ipp] - S[:, opp, ipn] - S[:, opn, ipp] + S[:, opn, ipn])


def sdd11(S, ipp, ipn):
    return 0.5 * (S[:, ipp, ipp] - S[:, ipp, ipn] - S[:, ipn, ipp] + S[:, ipn, ipn])


def resample(freqs, S, fgrid):
    """Linear interpolation onto a new (uniform) frequency grid."""
    out = np.empty((len(fgrid),) + S.shape[1:], dtype=complex)
    for a in range(S.shape[1]):
        for b in range(S.shape[2]):
            out[:, a, b] = (np.interp(fgrid, freqs, S[:, a, b].real)
                            + 1j * np.interp(fgrid, freqs, S[:, a, b].imag))
    return out


def sdd_2port(S, perm=(0, 1, 2, 3)):
    """Differential 2-port from a 4-port, ports in canonical order.

    `perm` maps the file's port indices to (P_in, P_out, N_in, N_out); the
    returned tuple is (Sdd11, Sdd12, Sdd21, Sdd22) as arrays over frequency.
    """
    S = reorder(S, perm)
    p1, p2, n1, n2 = 0, 1, 2, 3
    f = lambda a, b: S[:, a, b]
    s11 = 0.5 * (f(p1, p1) - f(p1, n1) - f(n1, p1) + f(n1, n1))
    s12 = 0.5 * (f(p1, p2) - f(p1, n2) - f(n1, p2) + f(n1, n2))
    s21 = 0.5 * (f(p2, p1) - f(p2, n1) - f(n2, p1) + f(n2, n1))
    s22 = 0.5 * (f(p2, p2) - f(p2, n2) - f(n2, p2) + f(n2, n2))
    return s11, s12, s21, s22


def s2abcd(s11, s12, s21, s22, z0):
    """S -> ABCD (chain) for a 2-port in a z0 reference."""
    d = 2.0 * s21
    A = ((1 + s11) * (1 - s22) + s12 * s21) / d
    B = z0 * ((1 + s11) * (1 + s22) - s12 * s21) / d
    C = (1.0 / z0) * ((1 - s11) * (1 - s22) - s12 * s21) / d
    D = ((1 - s11) * (1 + s22) + s12 * s21) / d
    return A, B, C, D


def abcd_s2(A, B, C, D, z0):
    """ABCD -> S for a 2-port in a z0 reference."""
    den = A + B / z0 + C * z0 + D
    s11 = (A + B / z0 - C * z0 - D) / den
    s21 = 2.0 / den
    s12 = 2.0 * (A * D - B * C) / den
    s22 = (-A + B / z0 - C * z0 + D) / den
    return s11, s12, s21, s22


def abcd_cascade(*mats):
    """Multiply ABCD matrices given as (A, B, C, D) tuples."""
    out = mats[0]
    for m in mats[1:]:
        a1, b1, c1, d1 = out
        a2, b2, c2, d2 = m
        out = (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
               c1 * a2 + d1 * c2, c1 * b2 + d1 * d2)
    return out
