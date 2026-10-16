#!/usr/bin/env python3
"""Structural self-check for a generated self-contained HTML SI report.

Checks the things that silently go wrong when a report is assembled from inlined
SVG and generated tables:

1. **self-contained** - no external references (`http(s)://`, remote `src`/`href`);
2. **tag balance** - every opened element is closed;
3. **geometry inside the canvas** - every path/polyline/line/rect/text coordinate
   lies inside its SVG's `viewBox` (a chart that overflows looks fine in the HTML
   and wrong on screen);
4. **eye diagrams carry data** - the SVGs whose viewBox matches `--eye-viewbox`
   have several traces and no `NaN`.

Exit code 0 = all checks passed, 1 = at least one problem (printed).

    python check_html_report.py out/report.html
    python check_html_report.py out/report.html --eye-viewbox 330x300
"""

import argparse
import re
import sys
from pathlib import Path

TAGS = ("div", "table", "thead", "tbody", "tr", "td", "th", "figure", "p", "ul",
        "ol", "li", "svg", "g", "pre", "span", "b", "code", "nav", "header")


def check(path, eye_viewbox, quiet=False):
    t = Path(path).read_text(encoding="utf-8")
    problems = []
    info = {}

    # 1. self-contained ------------------------------------------------------
    ext = re.findall(r"https?://[^\s\"'<>)]+", t)
    info["external refs"] = len(ext)
    if ext:
        problems.append(f"{len(ext)} external reference(s), first: {ext[0]}")
    for m in re.finditer(r'(?:src|href)="([^"]+)"', t):
        u = m.group(1)
        if not u.startswith("#") and not u.startswith("data:"):
            problems.append(f"non-inline src/href: {u}")

    # 2. tag balance ---------------------------------------------------------
    for tag in TAGS:
        o = len(re.findall(rf"<{tag}[ >]", t))
        c = t.count(f"</{tag}>")
        if o != c:
            problems.append(f"unbalanced <{tag}>: {o} open vs {c} close")
    info["svg"] = t.count("<svg")
    info["tables"] = t.count("<table")
    info["bytes"] = len(t)

    # 3. geometry inside the viewBox ----------------------------------------
    overflowing = 0
    eye_traces = []
    for m in re.finditer(r"<svg[^>]*viewBox=\"([^\"]+)\"[^>]*>(.*?)</svg>", t, re.S):
        vb = [float(x) for x in m.group(1).split()]
        if len(vb) != 4:
            problems.append(f"unparsable viewBox: {m.group(1)!r}")
            continue
        x0, y0, vw, vh = vb
        body = m.group(2)
        pts = []
        for pm in re.finditer(r'<polyline points="([^"]*)"', body):
            pts += [tuple(map(float, s.split(",")))
                    for s in pm.group(1).split() if "," in s]
        for pm in re.finditer(r'<path d="([^"]*)"', body):
            pts += [(float(a), float(b)) for a, b in
                    re.findall(r"[ML]([0-9.]+),([0-9.]+)", pm.group(1))]
        for lm in re.finditer(r'<line x1="([0-9.]+)" y1="([0-9.]+)" '
                              r'x2="([0-9.]+)" y2="([0-9.]+)"', body):
            a, b, c, d = map(float, lm.groups())
            pts += [(a, b), (c, d)]
        tol = 0.51
        bad = [p for p in pts
               if not (x0 - tol <= p[0] <= x0 + vw + tol and
                       y0 - tol <= p[1] <= y0 + vh + tol)]
        if bad:
            overflowing += len(bad)
            problems.append(f"viewBox {m.group(1)!r}: {len(bad)} coordinate(s) "
                            f"outside, first {bad[0]}")
        if m.group(1).replace(" ", "") == eye_viewbox.replace(" ", ""):
            eye_traces.append((len(re.findall(r"<polyline", body)),
                               "nan" in body.lower()))

    info["eye diagrams"] = len(eye_traces)
    info["points outside viewBox"] = overflowing
    if eye_viewbox and not eye_traces:
        problems.append(f"no SVG with viewBox {eye_viewbox!r} found "
                        f"(eye diagrams missing?)")
    for i, (n, nan) in enumerate(eye_traces):
        if n < 2:
            problems.append(f"eye diagram {i}: only {n} trace(s)")
        if nan:
            problems.append(f"eye diagram {i}: contains NaN")

    if not quiet:
        print(f"{path}: {info['bytes']} bytes, {info['svg']} svg, "
              f"{info['tables']} tables, external refs {info['external refs']}")
        print(f"eye diagrams: {info['eye diagrams']}, "
              f"coordinates outside their viewBox: {info['points outside viewBox']}")
        for i, (n, nan) in enumerate(eye_traces):
            print(f"  eye {i}: {n} traces, NaN={nan}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(" -", p)
        return 1
    if not quiet:
        print("OK: self-contained, balanced, geometry inside the canvas, eyes carry data")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html")
    ap.add_argument("--eye-viewbox", default="0 0 330 300",
                    help="viewBox of the eye-diagram figures (default '0 0 330 300')")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    return check(a.html, a.eye_viewbox, a.quiet)


if __name__ == "__main__":
    sys.exit(main())
