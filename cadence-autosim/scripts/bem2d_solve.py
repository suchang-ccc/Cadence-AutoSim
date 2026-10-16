#!/usr/bin/env python3
"""Solve 2-D PCB cross sections with Cadence's ``bem2d`` boundary-element solver.

``bem2d`` is the field solver Allegro/SigNoise calls internally.  Driving it
directly lets a script sweep geometries the board never contained (other widths,
intra-pair gaps, pair-to-pair spacing) while staying on the fabrication stackup.

Hard-won conventions this module encodes (see references/cadence-cli-invariants.md):

* input uses the **bare-number** syntax - ``+shield(...)``/``+dielectric(...)``
  with plain numbers, ``+rectangle(...)`` per conductor, closing ``.ends``;
* geometry must not reach into **negative x** (the solver returns an empty result
  silently), so everything is shifted so the left-most edge is x = 0;
* ``y`` accumulates **dielectric thickness only** - reference-plane copper
  thickness is passed as the shield argument and does not move the conductors.

Usage
-----
    python bem2d_solve.py --demo
    python bem2d_solve.py --stripline 4.2 --below 2.89 --above 3.0
    python bem2d_solve.py --microstrip 10.0 --plane 3.5 --copper 2.0
    python bem2d_solve.py --stripline 4.2 --n 2 --gap 4.2      # differential pair
    python bem2d_solve.py --section mysection.json -o out.json

Lengths are in mil on the command line and in metres inside the JSON spec.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MIL = 2.54e-5

# x-section.txt reports these as "mho/cm": inner layers 595900, outer foil 343000.
SIGMA_INNER = 5.959e7
SIGMA_FOIL = 3.43e7

DEFAULTS = dict(
    er=3.4,
    tand=0.035,
    t_plane_mil=0.6,        # reference-plane copper (shield argument)
    t_cu_mil=0.6,           # inner-layer trace copper
    below_mil=2.89,         # plane -> trace bottom
    above_mil=3.0,          # trace top -> plane
)


# --------------------------------------------------------------------------
# locating the solver
# --------------------------------------------------------------------------
def find_bem2d(explicit=None):
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"bem2d not found at {p}")
        return p
    roots = [os.environ.get("SPBROOT"), os.environ.get("CDSROOT")]
    for root in roots:
        if not root:
            continue
        for name in ("bem2d.exe", "bem2d"):
            p = Path(root) / "tools" / "bin" / name
            if p.exists():
                return p
    raise SystemExit("bem2d not found - set SPBROOT/CDSROOT (see scripts/spb_env.ps1) "
                     "or pass --bem2d")


# --------------------------------------------------------------------------
# cross-section builders  (y = 0 at the *outer* surface / bottom of the stack)
# --------------------------------------------------------------------------
def stripline(width_mil, below_mil=None, above_mil=None, t_cu_mil=None,
              t_plane_mil=None, er=None, tand=None, sigma=None,
              n=1, gap_mil=0.0, offset_mil=0.0):
    """`n` coplanar traces on an inner layer, `gap_mil` edge-to-edge.

    Layer order (bottom to top): shield | dielectric(below) | copper | dielectric(above)
    | shield.  The trace bottom sits at exactly `below_mil` of dielectric, i.e. the
    reference-plane copper does not enter the y coordinates.
    """
    d = dict(DEFAULTS)
    below = (d["below_mil"] if below_mil is None else below_mil) * MIL
    above = (d["above_mil"] if above_mil is None else above_mil) * MIL
    t_cu = (d["t_cu_mil"] if t_cu_mil is None else t_cu_mil) * MIL
    t_pl = (d["t_plane_mil"] if t_plane_mil is None else t_plane_mil) * MIL
    er = d["er"] if er is None else er
    tand = d["tand"] if tand is None else tand
    sigma = SIGMA_INNER if sigma is None else sigma

    w = width_mil * MIL
    g = gap_mil * MIL
    x0 = offset_mil * MIL
    conductors = []
    for k in range(n):
        xl = x0 + k * (w + g)
        conductors.append(dict(x0=xl, x1=xl + w, y0=below, y1=below + t_cu, sigma=sigma))
    layers = [dict(kind="shield", t=t_pl, er=1.0, tand=tand),
              dict(kind="dielectric", t=below, er=er, tand=tand),
              dict(kind="dielectric", t=t_cu, er=er, tand=tand),
              dict(kind="dielectric", t=above, er=er, tand=tand),
              dict(kind="shield", t=t_pl, er=1.0, tand=tand)]
    return dict(name="SL", layers=layers, conductors=conductors)


def microstrip(width_mil, plane_mil=3.5, t_cu_mil=2.0, coat_mil=0.8, er=None,
               tand=None, sigma=None, n=1, gap_mil=0.0):
    """Outer-layer traces: soldermask/coat, copper foil, prepreg to the plane, shield.

    This is the structure Cadence itself uses for TOP/BOTTOM sections (it appears in
    the tool's own ``interconn.iml``); model outer-layer escapes with it, not with the
    stripline form - the difference is a factor of two in impedance.
    """
    d = dict(DEFAULTS)
    coat = coat_mil * MIL
    t_cu = (d["t_cu_mil"] if t_cu_mil is None else t_cu_mil) * MIL
    plane = plane_mil * MIL
    er = d["er"] if er is None else er
    tand = d["tand"] if tand is None else tand
    sigma = SIGMA_FOIL if sigma is None else sigma

    w = width_mil * MIL
    g = gap_mil * MIL
    conductors = []
    for k in range(n):
        xl = k * (w + g)
        conductors.append(dict(x0=xl, x1=xl + w, y0=coat, y1=coat + t_cu, sigma=sigma))
    layers = [dict(kind="dielectric", t=coat, er=er, tand=0.0),
              dict(kind="dielectric", t=t_cu, er=er, tand=tand),
              dict(kind="dielectric", t=plane, er=er, tand=tand),
              dict(kind="shield", t=0.6 * MIL, er=1.0, tand=tand)]
    return dict(name="MS", layers=layers, conductors=conductors)


# --------------------------------------------------------------------------
# solver I/O
# --------------------------------------------------------------------------
def section_text(section):
    """Render the bare-number bem2d netlist for a section dict."""
    name = str(section.get("name", "XS"))[:20]
    conds = section["conductors"]
    if not conds:
        raise SystemExit("section has no conductors")
    # the solver returns nothing for geometries that reach into negative x
    x_min = min(c["x0"] for c in conds)
    shift = -x_min if x_min < 0 else 0.0

    ports = "+" + " ".join(f"in{i+1}" for i in range(len(conds))) + " 0 " + \
            " ".join(f"out{i+1}" for i in range(len(conds))) + " 0"
    lines = [f".subckt {name}", ports, ".layerstack Layerstack"]
    for lay in section["layers"]:
        if lay["kind"] == "shield":
            lines.append(f"+shield( {lay['t']:.6e} {lay.get('er', 1.0):g} "
                         f"{lay.get('tand', 0.0):g} )")
        else:
            lines.append(f"+dielectric( {lay['t']:.6e} {lay.get('er', 1.0):g} "
                         f"{lay.get('tand', 0.0):g} )")
    lines.append(".crosssection")
    for c in conds:
        lines.append(f"+rectangle ( {c['sigma']:.6e} {c['x0']+shift:.8e} {c['y0']:.6e} "
                     f"{c['x1']+shift:.8e} {c['y1']:.6e} )")
    lines += ["+Length=length", f".ends {name}"]
    return "\n".join(lines) + "\n"


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
    n = int(round(math.sqrt(len(vals)))) if vals else 0
    if n == 0 or n * n != len(vals):
        return None
    return [vals[i * n:(i + 1) * n] for i in range(n)]


def parse_out(path):
    """RLGC / Zc / Yc / Td matrices from a bem2d .OUT file."""
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
    return res


def derive(res):
    """Add Z0 (n=1) / Zodd-Zeven-Zdiff and kc,kl (n>=2) to a parsed result."""
    n = len(res["Zc"])
    res["n"] = n
    if n == 1:
        res["Z0"] = res["Zc"][0][0]
        return res
    z = res["Zc"]
    res["Zodd"] = z[0][0] - z[0][1]
    res["Zeven"] = z[0][0] + z[0][1]
    res["Zdiff"] = 2.0 * res["Zodd"]
    c, l = res.get("C"), res.get("L")
    if c and l:
        res["kc"] = abs(c[0][1] / c[0][0])      # Maxwell C12 < 0 -> use magnitudes
        res["kl"] = abs(l[0][1] / l[0][0])
        res["kc_kl_consistent"] = abs(res["kc"] - res["kl"]) <= 0.1 * max(res["kc"],
                                                                        res["kl"])
        res["NEXT_se_coeff"] = (res["kc"] + res["kl"]) / 4.0
        res["FEXT_coeff"] = (res["kc"] - res["kl"]) / 2.0
    return res


def solve(section, mesh=0.00127, bem2d=None, workdir=None, timeout=900, keep=False):
    """Write the section, run bem2d, return the parsed + derived result."""
    exe = find_bem2d(bem2d)
    tmp = None
    if workdir is None:
        tmp = tempfile.mkdtemp(prefix="bem2d_")
        workdir = tmp
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    name = str(section.get("name", "XS"))
    inp = workdir / f"{name}.in"
    inp.write_text(section_text(section), encoding="ascii")
    out = workdir / f"{name}.OUT"
    if out.exists():
        out.unlink()
    proc = subprocess.run([str(exe), "0", str(inp), str(mesh)], cwd=str(workdir),
                          capture_output=True, text=True, timeout=timeout)
    if not out.exists() or out.stat().st_size < 200:
        return dict(error=f"bem2d produced no result (rc={proc.returncode}); "
                          f"stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
    res = parse_out(out)
    if res is None:
        return dict(error="could not parse the bem2d .OUT file")
    res = derive(res)
    res["input"] = str(inp)
    if not keep and tmp:
        res["note"] = f"scratch dir kept for inspection-free runs: {tmp}"
    return res


# --------------------------------------------------------------------------
def _show(tag, res):
    if "error" in res:
        print(f"{tag:28s} FAILED: {res['error']}")
        return
    if res["n"] == 1:
        print(f"{tag:28s} Z0 = {res['Z0']:7.3f} ohm   C = {res['C'][0][0]:.4e} F/m   "
              f"L = {res['L'][0][0]:.4e} H/m   R = {res['R'][0][0]:.3f} ohm/m   "
              f"Td = {res['Td'][0][0]:.4e} s/m")
    else:
        print(f"{tag:28s} Zdiff = {res['Zdiff']:6.2f} ohm (Zodd {res['Zodd']:.2f} / "
              f"Zeven {res['Zeven']:.2f})   kc = {res['kc']:.5f}   kl = {res['kl']:.5f}"
              f"   kc==kl: {res['kc_kl_consistent']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--section", help="JSON cross-section spec")
    ap.add_argument("--stripline", type=float, metavar="WIDTH_MIL")
    ap.add_argument("--microstrip", type=float, metavar="WIDTH_MIL")
    ap.add_argument("--n", type=int, default=1, help="number of coplanar conductors")
    ap.add_argument("--gap", type=float, default=0.0, help="edge-to-edge gap (mil)")
    ap.add_argument("--below", type=float, help="plane -> trace bottom (mil)")
    ap.add_argument("--above", type=float, help="trace top -> plane (mil)")
    ap.add_argument("--plane", type=float, default=3.5, help="microstrip prepreg (mil)")
    ap.add_argument("--copper", type=float, default=2.0, help="outer-layer foil (mil)")
    ap.add_argument("--er", type=float, default=None)
    ap.add_argument("--tand", type=float, default=None)
    ap.add_argument("--mesh", type=float, default=0.00127)
    ap.add_argument("--bem2d", help="path to the bem2d executable")
    ap.add_argument("--workdir", help="keep the solver input/output here")
    ap.add_argument("-o", "--out", help="write the result JSON here")
    ap.add_argument("--demo", action="store_true",
                    help="solve a few reference sections and print a table")
    args = ap.parse_args(argv)

    if args.demo or not (args.section or args.stripline or args.microstrip):
        cases = [("stripline 4.2 mil", stripline(4.2)),
                 ("stripline 3.5 mil", stripline(3.5)),
                 ("microstrip 5.5 mil", microstrip(5.5)),
                 ("microstrip 10.0 mil", microstrip(10.0)),
                 ("pair 4.2/4.2 mil", stripline(4.2, n=2, gap_mil=4.2)),
                 ("pair 4.2/10 mil", stripline(4.2, n=2, gap_mil=10.0))]
        print("bem2d self-test (defaults: FR408HR Er 3.4 / tanD 0.035, "
              "cavity 2.89+0.6+3.0 mil, mesh 0.00127)")
        out = {}
        for tag, sec in cases:
            res = solve(sec, mesh=args.mesh, bem2d=args.bem2d, workdir=args.workdir)
            _show(tag, res)
            out[tag] = res
        if args.out:
            Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
        return 0

    if args.section:
        section = json.loads(Path(args.section).read_text(encoding="utf-8"))
    elif args.stripline:
        section = stripline(args.stripline, below_mil=args.below, above_mil=args.above,
                            er=args.er, tand=args.tand, n=args.n, gap_mil=args.gap)
    else:
        section = microstrip(args.microstrip, plane_mil=args.plane,
                             t_cu_mil=args.copper, er=args.er, tand=args.tand,
                             n=args.n, gap_mil=args.gap)
    res = solve(section, mesh=args.mesh, bem2d=args.bem2d, workdir=args.workdir)
    _show(Path(args.section).stem if args.section else "section", res)
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
        print("wrote", args.out)
    return 1 if "error" in res else 0


if __name__ == "__main__":
    sys.exit(main())
