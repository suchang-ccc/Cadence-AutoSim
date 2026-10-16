#!/usr/bin/env python3
"""
Cross-section field solves for the ZCU106 PCIe routing (Cadence bem2d).

The Allegro/SigNoise flow stores every cross-section it solved in
``interconn.iml``; this script re-creates those cross-sections in the *bare
number* bem2d syntax and re-solves them, so we can sweep geometry that the
board itself never contained (differential pairs, pair-to-pair spacing) and
turn the earlier hand-waved "Zdiff is roughly 70-81 ohm" into a solved number.

bem2d is called exactly the way signoise calls it:

    bem2d 0 <file.in> 0.00127

Layer stack (03_SIG1 / 12_SIG4 stripline cavity, taken from the board's own
``report -v x-section`` and from the geometries stored in interconn.iml):

    shield 0.6 mil | 2.89 mil Er3.4 | 0.6 mil (trace) | 3.0 mil Er3.4 | shield 0.6

Outputs: si/out/xs_results.json
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
BEM2D = SPB_ROOT / "tools" / "bin" / "bem2d.exe"
HERE = Path(__file__).resolve().parent
XS = HERE / "xs"
OUT = HERE / "out"
REPORTS = Path(os.environ.get("ZCU106_WORK", r"D:\Programs\candence\si_zcu106")) / "reports"
MIL = 2.54e-5

SIGMA_CU = 5.959e7
ER = 3.4
TAND = 0.035
T_PLANE = 0.6 * MIL          # 1.524e-5 m, reference plane copper
T_CU = 0.6 * MIL             # trace copper thickness
D_BELOW = 2.89 * MIL         # plane -> trace bottom
D_ABOVE = 3.0 * MIL          # trace top -> plane
W_PCIE = 4.2 * MIL           # PCIe trace width
MESH = 0.00127

# propagation: stripline Er 3.4 -> 1.017*sqrt(Er) ns/ft = 156 ps/in
TD_PS_IN = 1.017 * math.sqrt(ER) * 1000.0 / 12.0


# --------------------------------------------------------------------------
# bem2d input / output handling
# --------------------------------------------------------------------------
def xs_text(name, conductors, t_below=D_BELOW, t_above=D_ABOVE, t_cu=T_CU):
    """conductors: [(x_left, x_right)] in metres, all on the same layer.

    y is measured from the bottom reference plane, and - as in the geometry
    Cadence itself stored in interconn.iml - the plane copper thickness does
    not enter the y coordinates: the trace bottom sits at exactly the total
    dielectric thickness below it.

    bem2d silently produces an empty result for geometries that reach into
    negative x, so everything is shifted so that the left-most edge is x = 0.
    """
    x0 = min(c[0] for c in conductors)
    conductors = [(a - x0, b - x0) for (a, b) in conductors]
    n = len(conductors)
    y0 = t_below
    y1 = y0 + t_cu
    ports = "+" + " ".join(f"in{i+1}" for i in range(n)) + " 0 " + \
            " ".join(f"out{i+1}" for i in range(n)) + " 0"
    lines = [f".subckt {name}", ports, ".layerstack Layerstack",
             f"+shield( {T_PLANE:.6e} 1 {TAND} )",
             f"+dielectric( {t_below:.6e} {ER} {TAND} )",
             f"+dielectric( {t_cu:.6e} {ER} {TAND} )",
             f"+dielectric( {t_above:.6e} {ER} {TAND} )",
             f"+shield( {T_PLANE:.6e} 1 {TAND} )",
             ".crosssection"]
    for (xl, xr) in conductors:
        lines.append(f"+rectangle ( {SIGMA_CU:.6e} {xl:.6e} {y0:.6e} "
                     f"{xr:.6e} {y1:.6e} )")
    lines += ["+Length=length", f".ends {name}"]
    return "\n".join(lines) + "\n"


def solve(name, text, timeout=900):
    """Run bem2d and return the parsed RLGC / modal results."""
    inp = XS / f"{name}.in"
    inp.write_text(text, encoding="ascii")
    for old in (XS / f"{name}.OUT",):
        if old.exists():
            old.unlink()
    try:
        subprocess.run([str(BEM2D), "0", str(inp), str(MESH)],
                       cwd=str(XS), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  !! bem2d timed out after {timeout}s on {name}")
        return None
    out = XS / f"{name}.OUT"
    if not out.exists() or out.stat().st_size < 200:
        return None
    return parse_out(out)


def _matrix(txt, tag, freq):
    m = re.search(rf"(?m)^\.{tag} {freq}\s*$\n((?:\+.*\n)+)", txt)
    if not m:
        return None
    vals = []
    for line in m.group(1).splitlines():
        for tok in line.lstrip("+ ").split():
            try:
                vals.append(float(tok))
            except ValueError:
                pass
    n = int(round(math.sqrt(len(vals))))
    if n * n != len(vals):
        return None
    return [vals[i * n:(i + 1) * n] for i in range(n)]


def parse_out(path):
    txt = Path(path).read_text(errors="ignore")
    res = {}
    for tag in ("C", "L", "Td", "Zc", "Yc"):
        mat = _matrix(txt, tag, "INF")
        if mat is not None:
            res[tag] = mat
    rdc = _matrix(txt, "R", "0")          # bem2d only reports R at DC
    if rdc is not None:
        res["R"] = rdc
    if "Zc" not in res:
        return None
    n = len(res["Zc"])
    res["n"] = n
    if n == 1:
        res["Z0"] = res["Zc"][0][0]
    else:
        z = res["Zc"]
        res["Zodd_se"] = (z[0][0] - z[0][1])
        res["Zeven_se"] = (z[0][0] + z[0][1])
        res["Zdiff"] = 2.0 * res["Zodd_se"]
    return res


def coupled_metrics(res):
    """Mutual/self ratios and the crosstalk coefficients they imply."""
    c, l = res.get("C"), res.get("L")
    if not c or not l or res["n"] < 2:
        return {}
    c11, c12 = c[0][0], c[0][1]
    l11, l12 = l[0][0], l[0][1]
    # Maxwell capacitance matrices carry C12 < 0 for coupled traces; the
    # coupling ratios that enter the crosstalk formulas are the magnitudes.
    kc, kl = abs(c12 / c11), abs(l12 / l11)
    return dict(C11=c11, C12=c12, L11=l11, L12=l12, kc=kc, kl=kl,
                # homogeneous medium (stripline): backward crosstalk saturates at
                #   Kb = (kc + kl)/4 , forward crosstalk is ~0 because kc == kl
                NEXT_coeff=(kc + kl) / 4.0,
                FEXT_coeff=(kc - kl) / 2.0)


# --------------------------------------------------------------------------
# board geometry: differential pairs and their spacing
# --------------------------------------------------------------------------
def dpg(path=REPORTS / "dpg.txt"):
    """Parse Allegro's diff-pair gap report into segments with coordinates."""
    pairs = {}
    rx = re.compile(r'^"([^"]+?)\s*\(([^)]*)\)",([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+),'
                    r'\(([-0-9.]+) ([-0-9.]+)\)\s*\(([-0-9.]+) ([-0-9.]+)\)')
    for line in Path(path).read_text(errors="ignore").splitlines():
        m = rx.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        pairs.setdefault(name, dict(nets=m.group(2), gap=float(m.group(3)), segs=[]))
        pairs[name]["segs"].append(dict(
            ln=float(m.group(6)),
            a=(float(m.group(7)), float(m.group(8))),
            b=(float(m.group(9)), float(m.group(10)))))
    return pairs


def seg_dist(a1, b1, a2, b2):
    """Minimum distance between the 2-D segments a1b1 and a2b2 (mils)."""
    def d_pt_seg(pt, a, b):
        ax, ay = a
        bx, by = b
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 == 0:
            return math.hypot(pt[0] - ax, pt[1] - ay), 0.0
        t = max(0.0, min(1.0, ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2))
        return math.hypot(pt[0] - (ax + t * vx), pt[1] - (ay + t * vy)), t
    cand = [d_pt_seg(a1, a2, b2), d_pt_seg(b1, a2, b2),
            d_pt_seg(a2, a1, b1), d_pt_seg(b2, a1, b1)]
    return min(c[0] for c in cand)


def parallel_runs(pa, pb, max_dist_mil=200.0):
    """Segments of pair A that run alongside pair B, with their spacing."""
    runs = []
    for sa in pa["segs"]:
        if sa["ln"] < 20.0:
            continue
        for sb in pb["segs"]:
            if sb["ln"] < 20.0:
                continue
            d = seg_dist(sa["a"], sa["b"], sb["a"], sb["b"])
            if d <= max_dist_mil:
                runs.append(dict(d=d, la=sa["ln"], lb=sb["ln"],
                                 a=sa["a"], b=sa["b"]))
    runs.sort(key=lambda r: r["d"])
    return runs


# --------------------------------------------------------------------------
def pair_conductors(gap_mil, width_mil=4.2, offset_mil=0.0):
    """Two traces of `width_mil`, edge-to-edge `gap_mil`, centred on x=0."""
    w = width_mil * MIL
    g = gap_mil * MIL
    x = offset_mil * MIL
    return [(x - (g / 2 + w), x - g / 2), (x + g / 2, x + g / 2 + w)]


def crosstalk_conductors(gap_mil, sep_mil, width_mil=4.2):
    """victim pair then aggressor pair, `sep_mil` edge-to-edge between pairs."""
    w = width_mil * MIL
    g = gap_mil * MIL
    s = sep_mil * MIL
    # 4 traces: [v+, v-, a+, a-] spaced  gap | sep | gap  apart.  The fourth
    # x used to be written "w+g+s+w+g" (i.e. only one width + one gap past the
    # third trace), which put traces 3 and 4 edge to edge - bem2d then never
    # returned a result and the script silently fell back to an old artefact.
    xs = [0.0, w + g, w + g + s + w, w + g + s + w + g + w]
    return [(x, x + w) for x in xs]


def main(stage="all"):
    XS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    resfile = OUT / "xs_results.json"
    res = json.loads(resfile.read_text(encoding="utf-8")) if resfile.exists() else {}
    res.update(dict(stack=dict(t_plane_mil=0.6, d_below_mil=2.89, d_above_mil=3.0,
                               trace_mil=0.6, er=ER, tand=TAND,
                               sigma=SIGMA_CU, td_ps_in=round(TD_PS_IN, 1)),
                    mesh=MESH))
    res.setdefault("tests", {})
    if stage == "xt":
        return crosstalk_stage(res, resfile)

    # 1. regression: the board's own 4.2 mil single-ended stripline
    r = solve("xs_single_4p2", xs_text("XS_SINGLE", [(0.0, W_PCIE)]))
    board = dict(Z0=40.404, C=1.5223e-10, L=2.4851e-07, R=10.322)
    if r:
        res["tests"]["single_4p2"] = dict(
            Z0=r["Z0"], C=r["C"][0][0], L=r["L"][0][0], R=r["R"][0][0],
            Td=r["Td"][0][0], board=board,
            dZ0_pct=100.0 * (r["Z0"] - board["Z0"]) / board["Z0"],
            dC_pct=100.0 * (r["C"][0][0] - board["C"]) / board["C"])
        print(f"1 conductor 4.2 mil : Z0 = {r['Z0']:.3f} ohm "
              f"(board .iml: 40.404, {res['tests']['single_4p2']['dZ0_pct']:+.3f}%)")

    # 2. differential pair, the gaps this board actually uses
    for tag, gap in (("TX", 4.2), ("RX", 4.5)):
        rr = solve(f"xs_pair_{tag}", xs_text(f"XS_PAIR_{tag}", pair_conductors(gap)))
        if not rr:
            continue
        m = coupled_metrics(rr)
        res["tests"][f"pair_{tag}"] = dict(gap_mil=gap, Zdiff=rr["Zdiff"],
                                           Zodd=rr["Zodd_se"], Zeven=rr["Zeven_se"],
                                           Td=rr["Td"][0][0], **m)
        print(f"pair gap {gap:>4} mil   : Zdiff = {rr['Zdiff']:.2f} ohm, "
              f"Zodd = {rr['Zodd_se']:.2f}, Zeven = {rr['Zeven_se']:.2f}, "
              f"kc = {m['kc']:.4f}, kl = {m['kl']:.4f}")

    # 3. intra-pair gap sweep
    sweep = []
    for gap in (3.5, 4.0, 4.5, 5.0, 6.0, 8.0, 10.0):
        rr = solve(f"xs_gap_{gap:.1f}".replace(".", "p"),
                   xs_text("XS_GAP", pair_conductors(gap)))
        if not rr:
            continue
        m = coupled_metrics(rr)
        sweep.append(dict(gap_mil=gap, Zdiff=rr["Zdiff"], Zodd=rr["Zodd_se"],
                          Zeven=rr["Zeven_se"], C12=m["C12"], L12=m["L12"],
                          kc=m["kc"], kl=m["kl"]))
    res["gap_sweep"] = sweep
    for s in sweep:
        print(f"   gap {s['gap_mil']:5.1f} mil -> Zdiff {s['Zdiff']:6.2f} ohm "
              f"(Zodd {s['Zodd']:5.1f}, Zeven {s['Zeven']:5.1f})")

    # 4. pair-to-pair spacing from the board's own diff-pair report
    pairs = dpg()
    spacing = {}
    for a, b in (("PCIE_TX0_C_N,PCIE_TX0_C_P", "PCIE_TX1_C_N,PCIE_TX1_C_P"),
                 ("PCIE_RX0_N,PCIE_RX0_P", "PCIE_RX1_N,PCIE_RX1_P")):
        if a in pairs and b in pairs:
            runs = parallel_runs(pairs[a], pairs[b])
            if runs:
                spacing[f"{a.split(',')[0]} | {b.split(',')[0]}"] = dict(
                    min_center_mil=round(runs[0]["d"], 2),
                    n_parallel=len(runs),
                    longest_overlap_mil=round(max(r["la"] for r in runs), 1))
    res["pair_spacing"] = spacing
    for k, v in spacing.items():
        print(f"  spacing {k}: min centre-to-centre {v['min_center_mil']} mil")

    (OUT / "xs_results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("wrote", OUT / "xs_results.json")


def solve_retry(name, text, tries=3, timeout=150):
    for _ in range(tries):
        r = solve(name, text, timeout=timeout)
        if r:
            return r
    return None


def crosstalk_stage(res, resfile):
    """Coupling vs spacing.

    Two parts, both fully field-solved:
      * a two-conductor sweep gives the single-ended coupling coefficient as a
        function of edge-to-edge spacing (this is the curve the board's real
        geometry is read off);
      * a four-conductor model of a tight four-trace bundle gives the
        *differential* NEXT coefficient, i.e. how much of that coupling
        survives when both sides are differential.

    Note: the two-conductor sweep runs out to 35 mil without trouble.  A
    four-conductor bundle only solves when the conductors are close together
    (it is the worst case anyway); with an empty gap of tens of mil inside the
    cross-section bem2d does not return a result.
    """
    w = 4.2 * MIL
    gap_sweep = []
    for gap in (4.2, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 35.0):
        g = gap * MIL
        rr = solve(f"xs_c{xs_name(gap)}",
                   xs_text("XS_C", [(0.0, w), (w + g, 2 * w + g)]))
        if not rr:
            continue
        m = coupled_metrics(rr)
        gap_sweep.append(dict(gap_mil=gap, kc=m["kc"], kl=m["kl"],
                              NEXT_se_coeff=m["NEXT_coeff"],
                              FEXT_coeff=m["FEXT_coeff"], Zdiff=rr["Zdiff"]))
        print(f"  2 conductors, gap {gap:5.1f} mil: kc = {m['kc']:.5f}, "
              f"kl = {m['kl']:.5f} -> NEXT(se) = {m['NEXT_coeff']*100:.4f} %, "
              f"FEXT = {m['FEXT_coeff']*100:.5f} %")
    res["crosstalk_vs_gap"] = gap_sweep

    # tight four-trace bundle: pairs (0,1) and (2,3), every gap 4.2 mil
    bundle = None
    rr = solve_retry("xs_bundle",
                     xs_text("XS_BUNDLE",
                             crosstalk_conductors(4.2, 4.2)), tries=3, timeout=180)
    if not rr:
        print("  !! the four-conductor bundle did not solve - the coupling row "
              "for the tight bundle will be missing from xs_results.json")
    if rr and rr["n"] == 4:
        c, l = rr["C"], rr["L"]
        c11, l11 = c[0][0], l[0][0]
        kc, kl = abs(c[0][2] / c11), abs(l[0][2] / l11)
        cdm = abs(c[0][2] - c[0][3] - c[1][2] + c[1][3])
        ldm = abs(l[0][2] - l[0][3] - l[1][2] + l[1][3])
        # In a homogeneous dielectric kc must equal kl.  In this four-conductor
        # cross section the inductance matrix is well resolved but the
        # capacitance matrix's *distant* terms come out ~100x too small
        # (C02/C11 = 2e-5 while L02/L11 = 2e-3), so the single-ended coefficient
        # built from (kc + kl) is not trustworthy; where they disagree the
        # kc = kl value is used instead and the raw pair is kept for the record.
        consistent = abs(kc - kl) <= 0.1 * max(kc, kl)
        kc_se = kc if consistent else kl
        bundle = dict(gap_mil=4.2, sep_mil=4.2,
                      NEXT_se_coeff=(kc_se + kl) / 4.0,
                      NEXT_se_coeff_raw=(kc + kl) / 4.0,
                      kc_kl_consistent=bool(consistent),
                      NEXT_diff_coeff=(cdm / c11 + ldm / l11) / 4.0,
                      FEXT_diff_coeff=(cdm / c11 - ldm / l11) / 2.0,
                      kc_se=kc, kl_se=kl,
                      kc_pair=cdm / c11, kl_pair=ldm / l11,
                      C=c, L=l)
        print(f"  4 conductors, every gap 4.2 mil: "
              f"NEXT(se) = {bundle['NEXT_se_coeff']*100:.2f} %, "
              f"NEXT(diff) = {bundle['NEXT_diff_coeff']*100:.2f} %, "
              f"FEXT(diff) = {bundle['FEXT_diff_coeff']*100:.3f} %")
    else:
        print("  !! four-conductor bundle solve failed")
    res["crosstalk_bundle"] = bundle
    res["crosstalk"] = gap_sweep          # kept for the report's KPI strip
    resfile.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("wrote", resfile)


def xs_name(v):
    return ("%g" % v).replace(".", "p")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
