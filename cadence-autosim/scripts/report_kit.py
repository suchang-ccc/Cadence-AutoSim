#!/usr/bin/env python3
"""Self-contained HTML report helpers for SI results (inline SVG, no external refs).

The report a SI job hands over should be one file that opens anywhere, with every
number traceable to a JSON result.  This module provides the assembly helpers and
the two figures that matter for link work:

* `line_chart` - loss / return loss / impedance profiles;
* `eye_svg`    - an **NRZ eye folded on one UI and aligned to the received eye
  centre**.  Folding on two UI (the intuitive choice) draws the same eye twice and,
  because the fold is anchored at the record start rather than at the eye centre,
  cuts the second copy in half - the figure then reads as a three-level signal
  that does not exist.  The alignment here shifts the window so the data
  transition sits on the plot edges and the opening in the middle.

Typical use::

    import report_kit as K
    body = K.card("Insertion loss", K.table(["lane", "IL@4GHz"], rows))
    Path("report.html").write_text(K.page("Board X - SI", body), encoding="utf-8")

`python report_kit.py --demo out.html` writes a sample report with synthetic data.
"""

import html
import json
import math
from pathlib import Path

C_BG = "#0f1620"
C_PANEL = "#16202c"
C_LINE = "#2a3a4c"
C_TXT = "#dbe6f0"
C_DIM = "#8aa0b4"
C_ACC = "#38bdf8"
C_ACC2 = "#f59e0b"
C_OK = "#34d399"
C_BAD = "#f87171"

CSS = """
:root{--bg:%s;--panel:%s;--line:%s;--txt:%s;--dim:%s;--acc:%s;--acc2:%s;--ok:%s;--bad:%s}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
 font:15px/1.7 -apple-system,"Segoe UI","Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px}
h1{font-size:30px;margin:0 0 8px}
h2{font-size:22px;margin:44px 0 14px;padding:10px 0 10px 14px;border-left:4px solid var(--acc);
 background:linear-gradient(90deg,rgba(56,189,248,.10),transparent)}
h3{font-size:17px;margin:26px 0 10px}
p{margin:10px 0}
.sub{color:var(--dim);font-size:13.5px;margin:0}
nav.toc{margin:18px 0 6px;padding:14px 18px;background:var(--panel);border:1px solid var(--line);
 border-radius:10px;columns:2;column-gap:26px}
nav.toc a{display:block;color:var(--txt);text-decoration:none;font-size:13.5px;padding:3px 0}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
.kpi>div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:21px;color:var(--acc)}
.kpi span{font-size:12.5px;color:var(--dim)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;
 margin:14px 0;overflow:hidden}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid>*{min-width:0}
table{border-collapse:collapse;width:100%%;margin:12px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:7px 10px;text-align:left;vertical-align:top}
th{background:rgba(56,189,248,.10);font-weight:600}
tr:nth-child(even) td{background:rgba(255,255,255,.02)}
code{background:rgba(255,255,255,.06);padding:1px 5px;border-radius:4px;
 font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
ul,ol{margin:10px 0 10px 22px;padding:0}
li{margin:5px 0}
.dim{color:var(--dim);font-size:13px}
.ok{color:var(--ok);font-weight:600}
.warn{color:var(--acc2);font-weight:600}
.bad{color:var(--bad);font-weight:600}
figure{margin:14px 0}
figcaption{color:var(--dim);font-size:12.5px;margin-top:6px}
svg.chart{max-width:100%%;height:auto}
.foot{border-top:1px solid var(--line);margin-top:50px;padding-top:14px;color:var(--dim);font-size:12.5px}
@media print{body{background:#fff;color:#000}.card,nav.toc{break-inside:avoid}}
""" % (C_BG, C_PANEL, C_LINE, C_TXT, C_DIM, C_ACC, C_ACC2, C_OK, C_BAD)


# --------------------------------------------------------------------------
def esc(s):
    return html.escape(str(s))


def card(title, body):
    t = f"<h3>{title}</h3>" if title else ""
    return f'<div class="card">{t}{body}</div>'


def fig(svg, caption=""):
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f"<figure>{svg}{cap}</figure>"


def kpi(items):
    return '<div class="kpi">' + "".join(
        f"<div><b>{v}</b><span>{k}</span></div>" for v, k in items) + "</div>"


def verdict(ok, ok_txt="pass", bad_txt="fail"):
    return (f'<span class="ok">pass</span>' if ok else f'<span class="warn">{bad_txt}</span>')


def clip(pts, xmax):
    """Keep only the points inside the chart's x-range."""
    return [p for p in pts if p[0] <= xmax]


def table(headers, rows, cls="tbl"):
    """HTML table.  Cells carrying inline markup (<sub>/<span>/<code>) pass through;
    everything else is escaped, so data such as "<1e-6" stays literal."""
    def cell(c):
        s = "" if c is None else str(c)
        return s if isinstance(c, str) and "<" in s and ">" in s else esc(s)

    o = [f'<table class="{cls}"><thead><tr>']
    o += [f"<th>{cell(h)}</th>" for h in headers]
    o.append("</tr></thead><tbody>")
    for r in rows:
        o.append("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>")
    o.append("</tbody></table>")
    return "".join(o)


def load(path):
    """Read one of the stage JSON files the report is built from."""
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def note(*parts):
    """Small grey explanatory paragraph."""
    return '<p class="dim">' + "".join(parts) + "</p>"


# --------------------------------------------------------------------------
def line_chart(series, w=620, h=300, xlabel="", ylabel="", title="",
               xmax=None, ymin=0.0, ymax=None):
    """series: [{'label': str, 'color': '#rrggbb', 'pts': [(x, y), ...]}]"""
    pad_l, pad_r, pad_t, pad_b = 60, 18, 34, 46
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    xs = [p[0] for s in series for p in s["pts"]]
    ys = [p[1] for s in series for p in s["pts"]]
    xmax = xmax or (max(xs) if xs else 1)
    ymax = ymax if ymax is not None else (max(ys) * 1.1 if ys else 1)

    def X(x): return pad_l + x / xmax * pw
    def Y(y): return pad_t + ph - (y - ymin) / (ymax - ymin) * ph

    o = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    o.append(f'<text x="{pad_l}" y="18" fill="{C_TXT}" font-size="13" '
             f'font-weight="600">{esc(title)}</text>')
    for i in range(6):
        yv = ymin + (ymax - ymin) * i / 5
        o.append(f'<line x1="{pad_l}" y1="{Y(yv):.1f}" x2="{pad_l+pw}" y2="{Y(yv):.1f}" '
                 f'stroke="{C_LINE}" stroke-width="1"/>')
        o.append(f'<text x="{pad_l-8}" y="{Y(yv)+4:.1f}" fill="{C_DIM}" font-size="10" '
                 f'text-anchor="end">{yv:.2f}</text>')
    for i in range(6):
        xv = xmax * i / 5
        o.append(f'<line x1="{X(xv):.1f}" y1="{pad_t}" x2="{X(xv):.1f}" y2="{pad_t+ph}" '
                 f'stroke="{C_LINE}" stroke-width="1"/>')
        o.append(f'<text x="{X(xv):.1f}" y="{pad_t+ph+16}" fill="{C_DIM}" font-size="10" '
                 f'text-anchor="middle">{xv:.1f}</text>')
    for s in series:
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.2f},{Y(y):.2f}"
                     for i, (x, y) in enumerate(s["pts"]))
        o.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="2"/>')
    o.append(f'<text x="{pad_l+pw/2:.0f}" y="{h-8}" fill="{C_DIM}" font-size="11" '
             f'text-anchor="middle">{esc(xlabel)}</text>')
    o.append(f'<text x="14" y="{pad_t+ph/2:.0f}" fill="{C_DIM}" font-size="11" '
             f'text-anchor="middle" transform="rotate(-90 14 {pad_t+ph/2:.0f})">'
             f'{esc(ylabel)}</text>')
    lx = pad_l + 10
    for s in series:
        o.append(f'<rect x="{lx}" y="{pad_t+6}" width="10" height="10" fill="{s["color"]}"/>')
        o.append(f'<text x="{lx+14}" y="{pad_t+15}" fill="{C_DIM}" font-size="10">'
                 f'{esc(s["label"])}</text>')
        lx += 24 + 7 * len(s["label"])
    o.append("</svg>")
    return "".join(o)


def eye_svg(wave, bit_ps, w=330, h=300, title="", height_mV=0.0, width_ps=0.0):
    """Eye diagram folded onto ONE UI and aligned to the received eye centre.

    `wave` is [(time_ps, volts), ...]; `bit_ps` the UI in ps.  The first two UI are
    skipped so an AC-coupling transient has settled.  See the module docstring for
    why the fold is 1 UI and anchored to the eye centre rather than the record start.
    """
    pad_l, pad_r, pad_t, pad_b = 46, 12, 30, 34
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    ui = float(bit_ps)
    if not wave:
        return f'<svg viewBox="0 0 {w} {h}"></svg>'
    vs = [v for _, v in wave]
    lo, hi = min(vs), max(vs)
    span = (hi - lo) or 1.0
    vmin, vmax = lo - 0.05 * span, hi + 0.05 * span

    # --- align the fold: put the vertical eye opening in the middle ---------
    # shift the window, measure the centre of gravity of the vertical opening,
    # repeat.  This lands the data transition on the plot edges.
    def opening(shift, nslot=32, nbins=60):
        b = [[] for _ in range(nslot)]
        for t, v in wave:
            if t < 2 * ui:
                continue
            b[min(nslot - 1, int(((t / ui - shift) % 1.0) * nslot))].append(v)
        op = []
        for x in b:
            if len(x) < 5:
                op.append(0.0)
                continue
            hist = [0] * nbins
            for v in x:
                hist[min(nbins - 1, int((v - lo) / span * nbins))] += 1
            best = run = 0
            seen = False
            for k in hist:
                if k == 0:
                    run += 1
                else:
                    if run and seen:
                        best = max(best, run)
                    seen = True
                    run = 0
            op.append(best * span / nbins)
        return op

    centre = 0.0
    for _ in range(5):
        op = opening(centre)
        floor = min(op)
        wgt = [max(o - floor, 0.0) for o in op]
        if sum(wgt) <= 0.0:
            break
        sx = sy = 0.0
        for i, wv in enumerate(wgt):
            a = 2.0 * math.pi * i / len(op)
            sx += wv * math.cos(a)
            sy += wv * math.sin(a)
        c = (math.atan2(sy, sx) / (2.0 * math.pi)) % 1.0
        centre = (centre + (c - 0.5)) % 1.0

    def P(t): return ((t / ui - centre) % 1.0)      # 0..1 UI, eye centre at 0.5
    def X(p): return pad_l + p * pw
    def Y(v): return pad_t + ph - (v - vmin) / (vmax - vmin) * ph

    o = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    o.append(f'<text x="{pad_l}" y="18" fill="{C_TXT}" font-size="12" '
             f'font-weight="600">{esc(title)}</text>')
    o.append(f'<rect x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" '
             f'fill="#0b1119" stroke="{C_LINE}"/>')
    for i in range(5):
        v = vmin + (vmax - vmin) * i / 4
        o.append(f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{pad_l+pw}" y2="{Y(v):.1f}" '
                 f'stroke="{C_LINE}" stroke-width="0.6"/>')
        o.append(f'<text x="{pad_l-6}" y="{Y(v)+3.5:.1f}" fill="{C_DIM}" font-size="9" '
                 f'text-anchor="end">{v*1000:.0f}</text>')
    for frac in (0.0, 0.5, 1.0):
        x = pad_l + frac * pw
        o.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t+ph}" '
                 f'stroke="{C_LINE}" stroke-width="0.6" stroke-dasharray="3 3"/>')
    wins = {}
    for t, v in wave:
        k = int(t // ui)
        if k < 2:
            continue
        wins.setdefault(k, []).append((t, v))
    keys = sorted(wins)
    if len(keys) > 200:
        keys = keys[:: max(1, len(keys) // 200)]
    o.append(f'<g stroke="{C_ACC}" stroke-width="0.8" fill="none" stroke-opacity="0.35">')
    for k in keys:
        pts = wins[k]
        if len(pts) < 3:
            continue
        cur, prev_p = [], None
        for t, v in pts:
            p = P(t)
            if prev_p is not None and p < prev_p and len(cur) > 1:
                o.append('<polyline points="%s"/>'
                         % " ".join(f"{a:.1f},{b:.1f}" for a, b in cur))
                cur = []
            cur.append((X(p), Y(v)))
            prev_p = p
        if len(cur) > 1:
            o.append('<polyline points="%s"/>'
                     % " ".join(f"{a:.1f},{b:.1f}" for a, b in cur))
    o.append("</g>")
    o.append(f'<text x="{pad_l+pw/2:.0f}" y="{h-8}" fill="{C_DIM}" font-size="10" '
             f'text-anchor="middle">UI (0 -&gt; 1 UI, {ui:.0f} ps), eye centred</text>')
    txt = f"eye {height_mV:.0f} mV / {width_ps:.0f} ps" if height_mV else ""
    o.append(f'<text x="{pad_l+pw-4}" y="{pad_t+ph-8}" fill="{C_OK}" font-size="10" '
             f'text-anchor="end">{esc(txt)}</text>')
    o.append("</svg>")
    return "".join(o)


def stackup_svg(layers, w=560, h=None, title="Stackup"):
    """layers: [(name, thickness_mil, kind, note)] bottom-to-top, kind in
    {cu, sig, plane, die, mask}; returns a schematic cross-section."""
    rh = 14
    h = h or 30 + rh * len(layers) + 10
    colors = dict(cu="#b45309", sig="#0ea5e9", plane="#64748b", die="#1f2b38",
                  mask="#334155")
    x0, y0, bw = 168, 30, 26
    o = [f'<svg viewBox="0 0 {w} {h}" class="chart">']
    o.append(f'<text x="10" y="18" fill="{C_TXT}" font-size="12" '
             f'font-weight="600">{esc(title)}</text>')
    for i, (name, th, kind, note_) in enumerate(layers):
        y = y0 + i * rh
        o.append(f'<rect x="{x0}" y="{y:.1f}" width="{bw}" height="{rh-2:.1f}" '
                 f'fill="{colors.get(kind, "#334155")}" stroke="#0b1119" stroke-width="0.5"/>')
        col = C_TXT if kind in ("cu", "sig") else C_DIM
        weight = ' font-weight="600"' if kind in ("cu", "sig") else ""
        o.append(f'<text x="{x0+bw+8}" y="{y+rh-4:.1f}" fill="{col}" font-size="10"'
                 f'{weight}>{esc(name)}</text>')
        o.append(f'<text x="{w-8}" y="{y+rh-4:.1f}" fill="{C_DIM}" font-size="9" '
                 f'text-anchor="end">{th:g} mil  {esc(note_)}</text>')
    o.append("</svg>")
    return "".join(o)


def parse_maskfile(path):
    """Cadence mask file -> {'kind': 'hexagon'|'borders', 'segs': [...]} .

    Eye masks are `HEXAGON` (two rows: x vertices, y vertices, units from the
    header line) or `BORDERS` (x1 y1 x2 y2 segments).  The file's own reference
    plane/units are *not* declared beyond that header - state them in the report.
    """
    segs = []
    kind = None
    try:
        lines = Path(path).read_text(errors="ignore").splitlines()
    except OSError:
        return None
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("!") or s.startswith("#"):
            continue
        up = s.upper()
        if up.startswith(("UI", "HZ", "GHZ", "MHZ", "KHZ")):
            continue
        if up in ("HEXAGON", "POLYGON", "DIAMOND"):
            kind = up.lower()
            continue
        if up == "BORDERS":
            kind = "borders"
            continue
        try:
            v = [float(x) for x in s.split()]
        except ValueError:
            continue
        if kind == "borders" and len(v) >= 4:
            segs.append(v[:4])
        elif kind and len(v) > 1:
            segs.append(v)
    return dict(kind=kind, segs=segs)


def page(title, *body, subtitle="", toc=(), footer=""):
    """Assemble the self-contained HTML page (inline CSS, no external references)."""
    h = [f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>{esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">',
         f'<header><h1>{esc(title)}</h1>'
         + (f'<p class="sub">{subtitle}</p>' if subtitle else "") + "</header>"]
    if toc:
        h.append('<nav class="toc">' + "".join(
            f'<a href="#s{i}">{i}. {esc(t)}</a>' for i, t in enumerate(toc)) + "</nav>")
        for i, part in enumerate(body):
            h.append(f'<h2 id="s{i}">{i}. {esc(toc[i]) if i < len(toc) else ""}</h2>')
            h.append(part)
    else:
        h.extend(body)
    if footer:
        h.append(f'<div class="foot">{footer}</div>')
    h.append("</div></body></html>")
    return "".join(h)


# --------------------------------------------------------------------------
def _demo(out="demo_report.html"):
    """Render a sample report with synthetic data (no board needed)."""
    import random
    random.seed(7)
    freqs = [x / 10 for x in range(10, 81)]
    loss = [0.05 * f + 0.004 * f * f for f in freqs]
    body = [
        kpi([("0.90 dB", "IL @ 4 GHz"), ("42.5 ps", "Td"), ("77.0 ohm", "Zdiff")]),
        card("Insertion loss", fig(line_chart(
            [dict(label="lane 0", color=C_ACC, pts=list(zip(freqs, loss))),
             dict(label="limit", color=C_BAD, pts=[(1, 4.2), (8, 22.5)])],
            title="IL vs frequency", xlabel="GHz", ylabel="dB", xmax=8.0),
            "Synthetic data - the shape of a typical report figure.")),
        card("Eye (NRZ, 1 UI fold)", fig(eye_svg(
            [[i * 3.906, (0.4 if (i // 32) % 2 else -0.4) * (1 - 0.15 * (i % 32) / 32)]
             for i in range(900)], 125.0, title="demo lane @8 GT/s",
            height_mV=300, width_ps=125),
            "Folded on one UI with the opening centred.")),
        card("Results", table(["item", "value", "verdict"],
                              [["channel IL @4 GHz", "0.90 dB", verdict(True)],
                               ["return loss 6-7.7 GHz", "-0.10 dB margin", verdict(False)]])),
    ]
    Path(out).write_text(page("Demo SI report", *body,
                              subtitle="synthetic - generated by report_kit.py --demo",
                              toc=("Summary", "Loss", "Eye", "Results"),
                              footer="Self-contained: inline SVG, no external refs."),
                         encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        i = sys.argv.index("--demo")
        _demo(sys.argv[i + 1] if len(sys.argv) > i + 1 else "demo_report.html")
    else:
        print(__doc__)
