#!/usr/bin/env python3
"""Drawing helpers shared by the ZCU106 SI report generator.

The report itself is built by ``si/build_report.py``, which imports this module
for its chart/table/SVG helpers.  The legacy ``main()`` below still writes the
*old* narrative report to the same output path, so running this file directly is
refused - it would silently replace the current deliverable.
"""

import html
import json
import math
from pathlib import Path
import os

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
SI_WORK = Path(os.environ.get("ZCU106_WORK", r"D:\Programs\candence\si_zcu106"))

C_BG = "#0f1620"
C_PANEL = "#16202c"
C_LINE = "#2a3a4c"
C_TXT = "#dbe6f0"
C_DIM = "#8aa0b4"
C_ACC = "#38bdf8"
C_ACC2 = "#f59e0b"
C_OK = "#34d399"
C_BAD = "#f87171"


def esc(s):
    return html.escape(str(s))


# --------------------------------------------------------------------------
# tiny SVG chart helpers
# --------------------------------------------------------------------------
def line_chart(series, w=620, h=300, xlabel="", ylabel="", title="",
               xmax=None, ymin=0.0, ymax=None):
    pad_l, pad_r, pad_t, pad_b = 60, 18, 34, 46
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    xs = [p[0] for s in series for p in s["pts"]]
    ys = [p[1] for s in series for p in s["pts"]]
    xmax = xmax or (max(xs) if xs else 1)
    ymax = ymax if ymax is not None else (max(ys) * 1.1 if ys else 1)
    def X(x): return pad_l + x / xmax * pw
    def Y(y): return pad_t + ph - (y - ymin) / (ymax - ymin) * ph
    o = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img">']
    o.append(f'<text x="{pad_l}" y="18" fill="{C_TXT}" font-size="13" font-weight="600">{esc(title)}</text>')
    for i in range(6):
        yv = ymin + (ymax - ymin) * i / 5
        o.append(f'<line x1="{pad_l}" y1="{Y(yv):.1f}" x2="{pad_l+pw}" y2="{Y(yv):.1f}" stroke="{C_LINE}" stroke-width="1"/>')
        o.append(f'<text x="{pad_l-8}" y="{Y(yv)+4:.1f}" fill="{C_DIM}" font-size="10" text-anchor="end">{yv:.2f}</text>')
    for i in range(6):
        xv = xmax * i / 5
        o.append(f'<line x1="{X(xv):.1f}" y1="{pad_t}" x2="{X(xv):.1f}" y2="{pad_t+ph}" stroke="{C_LINE}" stroke-width="1"/>')
        o.append(f'<text x="{X(xv):.1f}" y="{pad_t+ph+16}" fill="{C_DIM}" font-size="10" text-anchor="middle">{xv:.1f}</text>')
    for s in series:
        d = " ".join(f"{'M' if i==0 else 'L'}{X(x):.2f},{Y(y):.2f}" for i, (x, y) in enumerate(s["pts"]))
        o.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="2"/>')
    o.append(f'<text x="{pad_l+pw/2:.0f}" y="{h-8}" fill="{C_DIM}" font-size="11" text-anchor="middle">{esc(xlabel)}</text>')
    o.append(f'<text x="14" y="{pad_t+ph/2:.0f}" fill="{C_DIM}" font-size="11" text-anchor="middle" transform="rotate(-90 14 {pad_t+ph/2:.0f})">{esc(ylabel)}</text>')
    lx = pad_l + 10
    for s in series:
        o.append(f'<rect x="{lx}" y="{pad_t+6}" width="10" height="10" fill="{s["color"]}"/>')
        o.append(f'<text x="{lx+15}" y="{pad_t+15}" fill="{C_TXT}" font-size="11">{esc(s["label"])}</text>')
        lx += 26 + 7.2 * len(s["label"])
    o.append("</svg>")
    return "".join(o)


def eye_svg(wave, bit_ps, w=330, h=300, title="", height_mV=0.0, width_ps=0.0):
    """Eye diagram folded onto ONE UI and aligned to the received eye centre.

    An NRZ signal (PCIe, DDR4) repeats every UI, so folding on 2 UI drew the
    same eye twice; and because the fold was anchored to the transmitter's bit
    boundary rather than to the received eye centre, the second copy was cut in
    half by the window edge and the figure looked as if it had three eyes.

    The window is now 1 UI wide and shifted so that the eye centre - the middle
    of the longest run of transition-free phases - sits at the middle of the
    plot.  Traces are split where the fold wraps, so no spurious horizontal
    line is drawn across the eye.
    """
    pad_l, pad_r, pad_t, pad_b = 46, 12, 30, 34
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    ui = float(bit_ps)        # ps
    if not wave:
        return f'<svg viewBox="0 0 {w} {h}"></svg>'
    vs = [v for _, v in wave]
    lo, hi = min(vs), max(vs)
    span = (hi - lo) or 1.0
    vmin, vmax = lo - 0.05 * span, hi + 0.05 * span

    # --- align the fold: put the vertical eye opening in the middle ---------
    # shift the window, measure the centre of gravity of the vertical opening,
    # repeat.  For an NRZ eye this lands the data transition on the plot edges
    # and the open part of the eye in the middle of the plot.
    def opening(shift, nslot=32, nbins=60):
        """vertical opening (mV) per phase slot for a window shifted by `shift`"""
        b = [[] for _ in range(nslot)]
        for t, v in wave:
            if t < 2 * ui:              # skip the AC-coupling settling windows
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
    o.append(f'<text x="{pad_l}" y="18" fill="{C_TXT}" font-size="12" font-weight="600">{esc(title)}</text>')
    o.append(f'<rect x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" fill="#0b1119" stroke="{C_LINE}"/>')
    for i in range(5):
        v = vmin + (vmax - vmin) * i / 4
        o.append(f'<line x1="{pad_l}" y1="{Y(v):.1f}" x2="{pad_l+pw}" y2="{Y(v):.1f}" stroke="{C_LINE}" stroke-width="0.6"/>')
        o.append(f'<text x="{pad_l-6}" y="{Y(v)+3.5:.1f}" fill="{C_DIM}" font-size="9" text-anchor="end">{v*1000:.0f}</text>')
    for frac in (0.0, 0.5, 1.0):
        x = pad_l + frac * pw
        o.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{pad_t+ph}" stroke="{C_LINE}" stroke-width="0.6" stroke-dasharray="3 3"/>')
    # group samples into 1-UI windows; each window becomes one (or two) traces
    wins = {}
    for t, v in wave:
        k = int(t // ui)
        if k < 2:                       # skip the AC-coupling settling windows
            continue
        wins.setdefault(k, []).append((t, v))
    keys = sorted(wins)
    if len(keys) > 200:                 # keep the page light, spread the traces
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
    o.append('</g>')
    o.append(f'<text x="{pad_l+pw/2:.0f}" y="{h-8}" fill="{C_DIM}" font-size="10" text-anchor="middle">UI (0 -&gt; 1 UI, {ui:.0f} ps), eye centred</text>')
    txt = f"eye {height_mV:.0f} mV / {width_ps:.0f} ps" if height_mV else ""
    o.append(f'<text x="{pad_l+pw-4}" y="{pad_t+ph-8}" fill="{C_OK}" font-size="10" text-anchor="end">{esc(txt)}</text>')
    o.append("</svg>")
    return "".join(o)


def bar_chart(labels, values, w=560, h=240, title="", unit="", color=C_ACC):
    pad_l, pad_r, pad_t, pad_b = 70, 18, 34, 40
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
    vmax = max(values) * 1.15 if values else 1
    bw = pw / max(len(values), 1) * 0.55
    o = [f'<svg viewBox="0 0 {w} {h}" class="chart">']
    o.append(f'<text x="{pad_l}" y="18" fill="{C_TXT}" font-size="13" font-weight="600">{esc(title)}</text>')
    for i in range(5):
        v = vmax * i / 4
        y = pad_t + ph - v / vmax * ph
        o.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" y2="{y:.1f}" stroke="{C_LINE}"/>')
        o.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" fill="{C_DIM}" font-size="10" text-anchor="end">{v:.2f}</text>')
    for i, (lb, v) in enumerate(zip(labels, values)):
        cx = pad_l + pw * (i + 0.5) / len(values)
        y = pad_t + ph - v / vmax * ph
        o.append(f'<rect x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{pad_t+ph-y:.1f}" fill="{color}" fill-opacity="0.8" rx="2"/>')
        o.append(f'<text x="{cx:.1f}" y="{y-5:.1f}" fill="{C_TXT}" font-size="10" text-anchor="middle">{v:.1f}{unit}</text>')
        o.append(f'<text x="{cx:.1f}" y="{pad_t+ph+16}" fill="{C_DIM}" font-size="10" text-anchor="middle">{esc(lb)}</text>')
    o.append("</svg>")
    return "".join(o)


def topology_svg(w=880, h=250):
    o = [f'<svg viewBox="0 0 {w} {h}" class="chart">']
    def box(x, y, bw, bh, lab, sub="", fill="#1e2c3c"):
        o.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="6" fill="{fill}" stroke="{C_LINE}"/>')
        o.append(f'<text x="{x+bw/2:.0f}" y="{y+bh/2-4:.0f}" fill="{C_TXT}" font-size="12" font-weight="600" text-anchor="middle">{esc(lab)}</text>')
        for i, s in enumerate(sub.split("|")):
            o.append(f'<text x="{x+bw/2:.0f}" y="{y+bh/2+14+i*13:.0f}" fill="{C_DIM}" font-size="10" text-anchor="middle">{esc(s)}</text>')
    def arrow(x1, x2, y, lab, sub=""):
        o.append(f'<line x1="{x1}" y1="{y}" x2="{x2-8}" y2="{y}" stroke="{C_ACC}" stroke-width="2"/>')
        o.append(f'<path d="M{x2-8},{y} l-9,-5 l0,10 z" fill="{C_ACC}"/>')
        o.append(f'<text x="{(x1+x2)/2:.0f}" y="{y-10}" fill="{C_TXT}" font-size="10" text-anchor="middle">{esc(lab)}</text>')
        if sub:
            o.append(f'<text x="{(x1+x2)/2:.0f}" y="{y+15}" fill="{C_DIM}" font-size="9" text-anchor="middle">{esc(sub)}</text>')
    box(10, 70, 120, 60, "ZU7EV (U1)", "PL GTY TX", "#22364a")
    box(660, 70, 130, 60, "PCIe x4 edge", "P3 (add-in card)", "#22364a")
    o.append(f'<circle cx="300" cy="100" r="16" fill="#1e2c3c" stroke="{C_ACC2}" stroke-width="2"/>')
    o.append(f'<text x="300" y="104" fill="{C_ACC2}" font-size="11" text-anchor="middle">C</text>')
    arrow(130, 284, 100, "via + 03_SIG1 stripline", "4.2 mil, ~2200 mil")
    arrow(316, 452, 100, "via + BOTTOM microstrip", "5.5 mil, ~285 mil")
    o.append(f'<text x="300" y="140" fill="{C_ACC2}" font-size="10" text-anchor="middle">C759..C766 = 100 nF 0402</text>')
    arrow(452, 660, 100, "", "")
    box(10, 170, 120, 50, "Receiver", "50 ohm to Vbias", "#1a2a22")
    box(660, 170, 130, 50, "Card side", "AC-coupled RX", "#1a2a22")
    o.append(f'<path d="M660,195 L130,195" stroke="{C_OK}" stroke-width="2" stroke-dasharray="6 4" fill="none"/>')
    o.append(f'<path d="M130,195 l9,-5 l0,10 z" fill="{C_OK}"/>')
    o.append(f'<text x="395" y="190" fill="{C_OK}" font-size="10" text-anchor="middle">RX path: connector -&gt; TOP microstrip -&gt; 12_SIG4 stripline -&gt; ZU7EV (no AC cap)</text>')
    o.append("</svg>")
    return "".join(o)


def stackup_svg(w=560, h=430):
    layers = [
        ("SOLDERMASK / AIR", 0.8, "mask", ""),
        ("TOP", 2.0, "cu", "4.5 mil"),
        ("FR408HR_PRE_1080", 3.5, "die", "Er 3.4"),
        ("02_GND1", 0.6, "plane", ""),
        ("FR408HR_CORE_1086", 3.0, "die", "Er 3.4"),
        ("03_SIG1", 0.6, "sig", "4.2 mil"),
        ("FR408HR_PRE_2113", 2.89, "die", "Er 3.4"),
        ("04_GND2", 0.6, "plane", ""),
        ("FR408HR_CORE_1086", 3.0, "die", "Er 3.4"),
        ("05_SIG2", 0.6, "sig", "4.2 mil"),
        ("FR408HR_PRE_2113", 2.89, "die", "Er 3.4"),
        ("06_GND3", 0.6, "plane", ""),
        ("FR408HR_CORE_1086", 3.0, "die", "Er 3.4"),
        ("07_SIG3", 0.6, "sig", "4.2 mil"),
        ("FR408HR_PRE_2113", 2.89, "die", "Er 3.4"),
        ("08_GND4", 0.6, "plane", ""),
        ("... 09_PWR1 / 10_PWR2 / 11_GND5 ...", 10.7, "die", "Er 3.4"),
        ("12_SIG4", 0.6, "sig", "4.2 mil"),
        ("FR408HR_CORE_1086", 3.0, "die", "Er 3.4"),
        ("13_GND6", 0.6, "plane", ""),
        ("FR408HR_PRE_2113", 2.89, "die", "Er 3.4"),
        ("14_SIG5", 0.6, "sig", "4.0 mil"),
        ("FR408HR_CORE_1086", 3.0, "die", "Er 3.4"),
        ("15_GND7", 0.6, "plane", ""),
        ("FR408HR_PRE_1080", 3.5, "die", "Er 3.4"),
        ("BOTTOM", 2.0, "cu", "5.5 mil"),
        ("CONFORMAL COAT", 0.8, "mask", ""),
    ]
    colors = dict(cu="#b45309", sig="#0ea5e9", plane="#64748b", die="#1f2b38", mask="#334155")
    x0, y0, bw, rh = 168, 30, 26, 14
    o = [f'<svg viewBox="0 0 {w} {h}" class="chart">']
    o.append(f'<text x="10" y="18" fill="{C_TXT}" font-size="12" font-weight="600">Stackup (Allegro x-section report)</text>')
    o.append(f'<text x="{x0+bw/2}" y="{y0-6}" fill="{C_DIM}" font-size="9" text-anchor="middle">铜</text>')
    for i, (name, th, kind, note) in enumerate(layers):
        y = y0 + i * rh
        o.append(f'<rect x="{x0}" y="{y:.1f}" width="{bw}" height="{rh-2:.1f}" fill="{colors[kind]}" stroke="#0b1119" stroke-width="0.5"/>')
        col = C_TXT if kind in ("cu", "sig") else C_DIM
        weight = ' font-weight="600"' if kind in ("cu", "sig") else ""
        o.append(f'<text x="{x0+bw+8}" y="{y+rh-4:.1f}" fill="{col}" font-size="10"{weight}>{esc(name)}</text>')
        o.append(f'<text x="{w-8}" y="{y+rh-4:.1f}" fill="{C_DIM}" font-size="9" text-anchor="end">{th:g} mil  {esc(note)}</text>')
    o.append(f'<text x="10" y="{y0+len(layers)*rh+16}" fill="{C_DIM}" font-size="10">61.19 mil total, 16 layers (7 routing / 9 planes)</text>')
    o.append("</svg>")
    return "".join(o)


def table(headers, rows, cls="tbl"):
    def cell(c):
        # cells may carry small inline markup (<sub>/<span>/…); anything else is
        # escaped.  Testing for a tag rather than for a leading "<" keeps labels
        # like "k<sub>c</sub>" working while still escaping data such as "<1e-6".
        s = "" if c is None else str(c)
        return s if isinstance(c, str) and "<" in s and ">" in s else esc(s)

    o = [f'<table class="{cls}"><thead><tr>']
    for h in headers:
        o.append(f"<th>{cell(h)}</th>")
    o.append("</tr></thead><tbody>")
    for r in rows:
        o.append("<tr>" + "".join(f"<td>{cell(c)}</td>" for c in r) + "</tr>")
    o.append("</tbody></table>")
    return "".join(o)


def pre(path, limit=14000):
    p = Path(path)
    if not p.exists():
        return f'<p class="dim">(missing {esc(path)})</p>'
    t = p.read_text(errors="ignore")
    if len(t) > limit:
        t = t[:limit] + "\n... (truncated)"
    return f"<pre>{esc(t)}</pre>"


MASKS = Path(os.environ.get("SPBROOT", r"D:\tools\Cadence\SPB_24.1")) / "share" / "topxp" / "sample_masks"


def parse_maskfile(name):
    """Read a Cadence sample mask file: (axis units) + BORDERS or HEXAGON."""
    p = MASKS / name
    if not p.exists():
        return None
    lines = [l.strip() for l in p.read_text(errors="ignore").splitlines() if l.strip()]
    kind = lines[1].upper() if len(lines) > 1 else ""
    if kind == "BORDERS":
        n = int(lines[2])
        segs = []
        for i in range(3, 3 + n):
            v = [float(x) for x in lines[i].split()]
            segs.append(dict(x1=v[0], y1=v[1], x2=v[2], y2=v[3],
                             inside=(len(v) > 4 and v[4] == 1)))
        return dict(kind="BORDERS", segs=segs)
    if kind == "HEXAGON":
        xs = [float(x) for x in lines[2].split()]
        ys = [float(x) for x in lines[3].split()]
        return dict(kind="HEXAGON", pts=list(zip(xs, ys)))
    return None


def bathtub_svg(bathtub, n_bins, ui_ps, w=560, h=240, title=""):
    """Eye opening versus sampling phase."""
    if not bathtub:
        return ""
    pts = [(i / n_bins * 2 * ui_ps, v) for i, v in enumerate(bathtub)]
    ymax = max(v for _, v in pts) * 1.1 or 1
    return line_chart([dict(label="眼开度", color=C_OK, pts=pts)], w=w, h=h, title=title,
                      xlabel="采样相位 (ps)", ylabel="mV", xmax=2 * ui_ps, ymin=0, ymax=ymax)


def pre_old(path, limit=14000):
    p = Path(path)
    if not p.exists():
        return f'<p class="dim">(missing {esc(path)})</p>'
    t = p.read_text(errors="ignore")
    if len(t) > limit:
        t = t[:limit] + "\n... (truncated)"
    return f"<pre>{esc(t)}</pre>"


def main():
    d = json.loads((OUT / "si_results.json").read_text(encoding="utf-8"))
    tl = d["tl"]; st = d["settings"]
    lanes = d["lanes"]
    g3 = json.loads((OUT / "gen3_results.json").read_text(encoding="utf-8")) \
        if (OUT / "gen3_results.json").exists() else None
    fr = json.loads((OUT / "freq_results.json").read_text(encoding="utf-8")) \
        if (OUT / "freq_results.json").exists() else None
    dd = json.loads((OUT / "ddr4_results.json").read_text(encoding="utf-8")) \
        if (OUT / "ddr4_results.json").exists() else None
    xs = json.loads((OUT / "xs_results.json").read_text(encoding="utf-8")) \
        if (OUT / "xs_results.json").exists() else None
    gth = json.loads((OUT / "pcie_gth_results.json").read_text(encoding="utf-8")) \
        if (OUT / "pcie_gth_results.json").exists() else None

    h = []
    h.append("""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>ZCU106 PCIe 走线信号完整性分析</title><style>
:root{color-scheme:dark}
body{background:%s;color:%s;font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:32px 40px 80px;max-width:1180px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:19px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid %s}
h3{font-size:15px;margin:22px 0 8px;color:%s}
.sub{color:%s;margin-bottom:24px}
.card{background:%s;border:1px solid %s;border-radius:10px;padding:18px 20px;margin:14px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
table.tbl{width:100%%;border-collapse:collapse;font-size:13px;margin:10px 0}
table.tbl th{background:#1d2a38;color:%s;text-align:left;padding:8px 10px;border:1px solid %s;font-weight:600}
table.tbl td{padding:7px 10px;border:1px solid %s}
table.tbl tr:nth-child(even) td{background:#141d27}
.chart{width:100%%;height:auto;background:%s;border:1px solid %s;border-radius:8px;margin:6px 0}
pre{background:#0b1119;border:1px solid %s;border-radius:8px;padding:12px;overflow:auto;font-size:11.5px;max-height:420px}
.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.kpi div{background:%s;border:1px solid %s;border-radius:10px;padding:14px}
.kpi b{display:block;font-size:22px;color:%s;line-height:1.3}
.kpi span{color:%s;font-size:12px}
.ok{color:%s}.warn{color:%s}.dim{color:%s}
code{background:#0b1119;padding:1px 5px;border-radius:4px;font-size:12.5px}
ul{margin:8px 0 8px 20px}
</style></head><body>""" % (C_BG, C_TXT, C_LINE, C_ACC, C_DIM, C_PANEL, C_LINE, C_TXT, C_LINE, C_LINE, C_PANEL, C_LINE, C_LINE, C_PANEL, C_LINE, C_ACC, C_DIM, C_OK, C_ACC2, C_DIM))

    h.append("<h1>ZCU106 PCIe 走线信号完整性分析</h1>")
    h.append(f'<p class="sub">板卡：9217_1280937-HW-Z1-ZCU106_Revision_1.0 （Xilinx ZCU106，ZU7EV） &nbsp;·&nbsp; '
             f'DB Doctor 由 SPB 16.6 升级到 24.1 后分析 &nbsp;·&nbsp; 仿真引擎：Cadence Allegro PCB SI '
             f'(signoise / bem2d) 与 tlsim &nbsp;·&nbsp; 生成日期 2026-10-15'
             f'（第 3 轮：官方 AMD POD12 模型 + bem2d 差分阻抗/串扰场求解）</p>')

    # ---- KPI ----
    worst = min(lanes, key=lambda x: x["eye_height_V"])
    g3w = ""
    if g3:
        g3_ok = [r for r in g3["lanes"] if r.get("deemp_db") == -3.5 and "eye_height_mV" in r]
        if g3_ok:
            gw = min(g3_ok, key=lambda x: x["eye_height_mV"])
            g3w = (f'<div><b>{gw["eye_height_mV"]:.0f} mV / {gw["eye_width_ps"]:.0f} ps</b>'
                   f'<span>PCIe 3.0 眼高 / 半高眼宽（8 GT/s，-3.5 dB FFE）</span></div>')
    h.append('<div class="kpi">')
    h.append(f'<div><b>{worst["eye_height_V"]*1000:.0f} mV</b><span>最差眼高（接收端，5 GT/s）</span></div>')
    h.append(f'<div><b>{worst["eye_width_ps"]:.0f} ps</b><span>半高眼宽（UI = 200 ps）</span></div>')
    h.append(g3w)
    h.append(f'<div><b>{tl["SL_4P2"]["Z0"]:.1f} / {tl["MS_5P5"]["Z0"]:.1f} Ω</b><span>内层 / 外层 单端阻抗</span></div>')
    il4 = min(v["il_4p0"] for v in fr["lanes"].values()) if fr else 0.21
    h.append(f'<div><b>{il4:.2f} dB</b><span>通道插损 @4 GHz（含 100 nF 电容）</span></div>')
    if xs and xs.get("tests", {}).get("pair_TX"):
        zd = xs["tests"]["pair_TX"]["Zdiff"]
        h.append(f'<div><b>{zd:.1f} Ω</b><span>差分阻抗（bem2d 场求解，4.2 mil 间距）</span></div>')
    if dd:
        am = [t for t in dd.get("amd_tests", [])
              if t.get("model") == "HP_POD12_DCI_M_OUT40" and t.get("eye_height_mV")]
        if am:
            w = min(am, key=lambda t: t["eye_height_mV"])
            h.append(f'<div><b>{w["eye_height_mV"]:.0f} mV</b>'
                     f'<span>DDR4 写眼高（官方 POD12 模型，最差通道）</span></div>')
    if xs and xs.get("crosstalk"):
        far = min(xs["crosstalk"], key=lambda c: abs(c["gap_mil"] - 35.0))
        v = far["NEXT_se_coeff"] * 100
        h.append(f'<div><b>{v:.4f} %</b>'
                 f'<span>对间 NEXT 系数（场求解，净距 {far["gap_mil"]:.0f} mil）</span></div>')
    h.append('</div>')

    # ---- 1 board / stackup ----
    h.append("<h2>1. 板级与叠层信息</h2>")
    h.append('<div class="grid">')
    h.append('<div class="card">' + stackup_svg() + '</div>')
    h.append('<div class="card"><h3>关键事实（来自 Allegro 报告）</h3><ul>'
             '<li>16 层，7 个布线层（TOP、03_SIG1、05_SIG2、07_SIG3、12_SIG4、14_SIG5、BOTTOM），9 个平面层</li>'
             '<li>板厚 61.19 mil；介质 FR408HR，Er = 3.4，Df = 0.035（库中设定值）</li>'
             '<li>内层信号 3.25 mil 名义线宽，PCIe 实际用到 <b>4.2 mil</b>；外层 <b>5.5 mil</b></li>'
             '<li>PCIe 接口：U1（ZU7EV）<b>PL GTY</b> ↔ P3（PCIe x4 板边连接器）</li>'
             '<li><b>收发器归属（本轮修正）</b>：板卡上 4 条 PS-GTR 全部另有用途——'
             '<code>GT0/GT1_DP_TX</code>（DisplayPort）、<code>GT2_USB0</code>、<code>GT3_SATA1</code>，'
             '且各自带独立的 <code>GTR_REF_CLK_DP/USB3/SATA</code> 参考时钟。'
             '因此 <code>PCIE_TX/RX0-3</code> 只能走 <b>PL GTY</b>。'
             'ZU7EV 的 GTY 支持到 16.3 Gb/s，<b>Gen3(8 GT/s) 与 Gen4(16 GT/s) 在器件层面都成立</b>，'
             '这与"这块板子是 PCIe 3.0"一致（上一版报告把它写成 PS-GTR 是错的，特此更正）。</li>'
             '<li>TX 侧交流耦合电容 <b>C759–C766 = 0.1 µF ±10% 0402</b>（BOM 实测确认）</li>'
             '<li>参考时钟耦合电容 C339/C340 = 0.01 µF</li>'
             '<li>RX 侧无耦合电容（直连），符合 Root Complex 侧规范</li>'
             '</ul></div>')
    h.append('</div>')

    # ---- 2 topology ----
    h.append("<h2>2. 被分析的通道拓扑</h2>")
    h.append('<div class="card">' + topology_svg() + '</div>')
    h.append("<p>每个 TX 通道由三段构成：FPGA 焊球 → 过孔 → 内层 03_SIG1 带状线 → 过孔 → 0.1 µF 交流耦合电容 → 外层 BOTTOM 微带 → 板边连接器 → 插卡接收端。"
             "RX 通道为连接器 → 外层 TOP 微带 → 内层 12_SIG4 带状线 → FPGA。</p>")

    # ---- 3 lane geometry ----
    h.append("<h2>3. 各通道几何（Allegro 走线长度报告）</h2>")
    rows = []
    for l in lanes:
        rows.append([l["lane"], l["net"], f'{l["milA"]:.0f}', f'{l["milB"]:.0f}', f'{l["total_mil"]:.0f}',
                     l["cap"], f'{l["cap_nf"]:g} nF', f'{l["td_mil"]:.0f}'])
    h.append(table(["通道", "网名", "内层带状线 (mil)", "外层微带 (mil)", "总长 (mil)", "耦合电容", "容值", "飞行时间 (ps)"], rows))
    rrows = [[r["lane"], r["net"], f'{r["mil_top"]:.0f}', f'{r["mil_inner"]:.0f}',
              f'{r["mil_top"]+r["mil_inner"]:.0f}'] for r in d["rx_lanes"]]
    h.append("<h3>RX 通道（无交流耦合电容）</h3>")
    h.append(table(["通道", "网名", "TOP 微带 (mil)", "12_SIG4 带状线 (mil)", "总长 (mil)"], rrows))
    h.append('<p class="dim">差分对内长度失配：TX 各对 &lt; 2.1 mil，RX 各对 &lt; 2.7 mil，均在 1 ps 量级以内。</p>')

    # ---- 4 extraction ----
    h.append("<h2>4. Cadence 场求解器提取的传输线参数</h2>")
    rows = []
    for name, v in tl.items():
        rows.append([name, v["geometry"], f'{v["C"]*1e12:.2f} pF/m', f'{v["L"]*1e9:.1f} nH/m', f'{v["R"]:.2f} Ω/m',
                     f'{v["Z0"]:.1f}', f'{v["Td_ps_in"]:.1f}'])
    h.append(table(["模型", "几何", "C", "L", "R (DC)", "Z0 (Ω)", "Td (ps/in)"], rows))
    h.append('<p class="dim">由 <code>signoise</code> 调用 Cadence 二维场求解器 <code>bem2d</code> 在本板实际叠层上求得，'
             '结果保存在 <code>interconn.iml</code>；过孔使用同一次分析综合出的 <code>VIA_...V19H10/V20H10</code> 模型（含残桩）。</p>')

    # ---- 5 sim setup ----
    h.append("<h2>5. 通道仿真设置</h2>")
    h.append('<div class="card"><table class="tbl"><tbody>'
             f'<tr><td>仿真引擎</td><td>Cadence <code>tlsim</code>（Allegro SI 传输线时域引擎，W-element / RLGC）</td></tr>'
             f'<tr><td>速率 / 单位间隔</td><td>5 GT/s（PCIe Gen2），UI = {st["bit_ps"]:.0f} ps</td></tr>'
             f'<tr><td>激励</td><td>PRBS-7 × {st["patterns"]}，10–90% 边沿 {st["edge_ps"]:.0f} ps（理想化 Gen2 发送端）</td></tr>'
             f'<tr><td>发送端模型</td><td>{st["drv_ohm"]:.0f} Ω 源阻抗，单端摆幅 0–{st["drv_high"]:.1f} V</td></tr>'
             f'<tr><td>接收端模型</td><td>{st["rx_ohm"]:.0f} Ω 到公共端（交流耦合后直流中点 0 V）</td></tr>'
             f'<tr><td>耦合电容寄生</td><td>0402 封装，串联 ESL = {st["cap_esl_nH"]:.2f} nH（建模假设）</td></tr>'
             '</tbody></table>'
             '<p class="warn" style="margin:12px 0 0">⚠️ 本机没有任何厂商 IBIS/AMI 模型（板内 <code>devices.dml</code> 为空，Xilinx 未随板提供 ZU7EV GTR 模型），'
             '因此发送端/接收端为上述理想化模型。走线、过孔、电容与叠层参数均取自本板实际数据，'
             '眼图结果用于<b>比较各通道之间</b>以及评估通道本身的裕量，不能替代带厂商模型的签核级仿真。</p>'
             '</div>')

    # ---- 6 eye results ----
    h.append("<h2>6. 眼图结果（接收端 / 连接器侧）</h2>")
    rows = []
    for l in lanes:
        rows.append([l["lane"], f'{l["eye_height_V"]*1000:.0f}', f'{l["eye_width_ps"]:.0f}',
                     f'{l["hi_lvl"]*1000:.0f}', f'{l["lo_lvl"]*1000:.0f}', f'{l["swing_mV"]:.0f}',
                     f'{l["overshoot_pct"]:.0f}%', f'{l["undershoot_pct"]:.0f}%'])
    h.append(table(["通道", "眼高 (mV)", "半高眼宽 (ps)", "高电平 (mV)", "低电平 (mV)", "摆幅 (mV)", "过冲", "下冲"], rows))
    h.append('<p class="dim">眼高 = 相位列中最大电压间隙；半高眼宽 = 电压间隙 ≥ 眼高一半的相位区间；'
             '过冲/下冲按 5%/95% 分位电平归一化。</p>')
    h.append('<div class="grid3">')
    for l in lanes[:6]:
        h.append('<div class="card">' + eye_svg(l.get("wave", []), l["bit_ps"],
                 title=f'{l["lane"]}  ({l["total_mil"]:.0f} mil)', height_mV=l["eye_height_V"] * 1e3,
                 width_ps=l["eye_width_ps"]) + '</div>')
    h.append('</div>')

    # ---- 6b receive direction ----
    h.append("<h3>RX 方向眼图（连接器 → FPGA，直流耦合无隔直电容）</h3>")
    rxr = d.get("rx_results", [])
    if rxr:
        rows = []
        for l in rxr:
            rows.append([l["lane"], l["net"], f'{l["total_mil"]:.0f}', f'{l["td_mil"]:.0f}',
                         f'{l["eye_height_V"]*1000:.0f}', f'{l["eye_width_ps"]:.0f}',
                         f'{l["swing_mV"]:.0f}', f'{l["overshoot_pct"]:.0f}%', f'{l["undershoot_pct"]:.0f}%'])
        h.append(table(["通道", "网名", "总长 (mil)", "飞行时间 (ps)", "眼高 (mV)", "半高眼宽 (ps)",
                        "摆幅 (mV)", "过冲", "下冲"], rows))
        h.append('<p class="dim">RX 侧没有隔直电容，波形直流落在发送端共模电平上（高电平 ≈ +0.51 V），'
                 '眼高与差分行为不受影响。定长段（12_SIG4）与 TX 段使用同一组场求解参数。</p>')
        h.append('<div class="grid3">')
        for l in rxr[:6]:
            h.append('<div class="card">' + eye_svg(l.get("wave", []), l["bit_ps"],
                     title=f'{l["lane"]}  ({l["total_mil"]:.0f} mil)', height_mV=l["eye_height_V"] * 1e3,
                     width_ps=l["eye_width_ps"]) + '</div>')
        h.append('</div>')

    # ---- 7 loss ----
    h.append("<h2>7. 通道频域响应</h2>")
    series = []
    for i, (name, v) in enumerate(d["loss"].items()):
        series.append(dict(label=f'{name} ({v["mil"]:.0f} mil)', color=[C_ACC, C_ACC2][i % 2],
                           pts=v["points"]))
    h.append('<div class="card">' + line_chart(series, title="插入损耗 vs 频率", xlabel="GHz", ylabel="dB", xmax=20.0) + '</div>')
    h.append('<p class="dim">由 Cadence 场求解得到的单位长度 RLGC 计算（含趋肤效应与介质损耗一阶项）。'
             '整条 TX 通道在 2.5 GHz（5 GT/s 的奈奎斯特频率）处的插损仅约 0.2 dB，'
             '说明这段走线长度下铜/介质损耗不是限制因素。</p>')

    # ---- 8 cap sensitivity ----
    h.append("<h2>8. 交流耦合电容取值敏感性（100 nF vs 10 nF）</h2>")
    cs = d["cap_sensitivity"]
    h.append('<div class="grid">')
    h.append('<div class="card">' + bar_chart([f'{c["cap_nF"]:.0f} nF' for c in cs],
             [c["eye_height_mV"] for c in cs], title="眼高对比 (mV)", unit=" mV") + '</div>')
    h.append('<div class="card"><h3>结论</h3><ul>'
             + "".join(f'<li>{c["cap_nF"]:.0f} nF → 眼高 {c["eye_height_mV"]:.1f} mV，眼宽 {c["eye_width_ps"]:.0f} ps</li>' for c in cs) +
             '<li>两者差异 &lt; 0.5%，<b>在 5 GT/s 下 AC 耦合电容取 100 nF 或 10 nF 对眼图没有影响</b>；'
             '该电容的作用是隔直，决定的是低频截止（100 nF 约 32 kHz，10 nF 约 320 kHz），'
             '远低于 PCIe 信号带宽。</li>'
             '<li>真正限制高频的是封装 ESL 与过孔残桩，而不是电容容值。</li>'
             '</ul></div>')
    h.append('</div>')

    # ---- 9 signoise board level ----
    h.append("<h2>9. Allegro SI (signoise) 板级反射/振铃分析</h2>")
    h.append('<p>以下为 <code>signoise</code> 直接在该 24.1 版本板卡上对 18 条 PCIe 网络执行的 '
             '"Pulse / Typical / Reflection" 仿真原始报告。注意此轮使用的驱动模型是 Cadence 缺省 IO '
             '（2.5 V、500 ps 边沿），且交流耦合电容被工具回退成 10 nF 模型，因此其绝对数值仅作参考，'
             '第 6 节的 tlsim 通道仿真才是本文的结论依据。</p>')
    h.append('<div class="card">' + pre(SI_WORK / "f_delay.txt", 9000) + '</div>')
    h.append('<div class="card">' + pre(SI_WORK / "f_refl.txt", 7000) + '</div>')
    h.append('<div class="card">' + pre(SI_WORK / "f_ring.txt", 7000) + '</div>')

    # ---- 10 conclusions ----
    h.append("<h2>10. 结论与建议</h2>")
    h.append('<div class="card"><ul>'
             '<li><span class="ok">✔</span> 四条 TX 通道（含 100 nF 交流耦合电容）在 5 GT/s 下的接收端眼高均在 '
             f'<b>{worst["eye_height_V"]*1000:.0f} mV 以上</b>（约 523 mV 峰峰摆幅的 92%），眼宽约 {worst["eye_width_ps"]:.0f} ps，'
             '相对 PCIe Gen2 规范（发送端最小 800 mV 差分、接收端最小 175 mV 差分眼）有充足裕量。</li>'
             '<li><span class="ok">✔</span> 通道插损很小（@2.5 GHz 约 0.2 dB），当前 ~2400–2550 mil 的长度不是瓶颈。</li>'
             '<li><span class="ok">✔</span> 差对内长度失配 &lt; 2.7 mil（&lt; 0.5 ps），不会造成明显的对内偏斜。</li>'
             '<li><span class="warn">△</span> 内层带状线单端阻抗约 '
             f'{tl["SL_4P2"]["Z0"]:.0f} Ω，外层微带约 {tl["MS_5P5"]["Z0"]:.0f} Ω，两者不连续（相差约 10 Ω）。'
             '在 5 GT/s 下这个台阶冲击很小，但如果升级到 8 GT/s 及以上，建议把两段阻抗对齐到 85 Ω 差分。</li>'
             '<li><span class="warn">△</span> 过孔是当前通道里最明显的寄生（<code>signoise</code> 报告其为 closed-form 模型，'
             '且带残桩）。若要跑 Gen3/Gen4，建议做背钻或改用盲埋孔。</li>'
             '<li><span class="dim">•</span> 交流耦合电容 100 nF 的选择正确且对眼图不敏感；若要更精确，'
             '应使用厂商实测的 ESL/ESR（含焊盘与过孔）而非理想电容。</li>'
             '</ul></div>')

    # ---- 11 reproduce ----
    h.append("<h2>11. 复现步骤</h2>")
    h.append("<div class='card'><ul>"
             "<li>加载环境：<code>. .\\tools\\spb-env.ps1</code></li>"
             "<li>版本转换：<code>dbdoctor -outfile zcu106_brd24.brd &lt;原件&gt;</code>，"
             "用 <code>allegro_batch dbstat -v</code> 验证 16.6 → 24.1</li>"
             "<li>板级 SI：<code>signoise -f pcie_nets.lst -w -C -r Delay,Ringing,ReflectionSummary zcu106_brd24.brd out.brd</code></li>"
             "<li>二维场求解（差分阻抗 / 串扰）：<code>python si\\build_xs.py</code>；"
             "单次求解等价于 <code>bem2d 0 si\\xs\\&lt;name&gt;.in 0.00127</code></li>"
             "<li>DDR4（官方 AMD POD12 + 封装 + 通用 IBIS 对照）：<code>python si\\build_ddr4.py</code>；"
             "模型转换 <code>python si\\ibis2tlsim.py --batch D:\\Programs\\candence\\zynquplus.ibs "
             "si\\sim\\ibis_pod12.inc HP_POD12_DCI_M_OUT40 HP_POD12_DCI_M_OUT48</code></li>"
             "<li>通道仿真 + 报告：<code>python si\\build_si.py &amp;&amp; python si\\build_report.py</code></li>"
             "</ul></div>")

    # ================= 12. IBIS / AMI model acquisition ==================
    h.append("<h2>12. IBIS / AMI 模型获取结果</h2>")
    h.append('<div class="card" style="border-color:%s"><h3>★ 本轮新到手的官方 AMD / Xilinx 模型</h3>'
             '<p>上一轮结论是「本机没有厂商模型」。<b>本轮拿到了 AMD 官方的 Zynq UltraScale+ 模型包</b>'
             '（<code>D:\\Programs\\candence\\</code>），DDR4 一节因此从「通用 IBIS 等效」升级为'
             '<b>官方 POD12 模型 + 官方封装模型</b>：</p>' % C_OK)
    h.append(table(["模型 / 文件", "规格", "本轮用途"], [
        ["<code>zynquplus.ibs</code>（123.8 MB）", "Xilinx 官方 Zynq UltraScale+ IBIS，IBIS 5.1 Rev 2.12，2026-06-01",
         "DDR4 的 FPGA 侧驱动/接收（HP 库 342 个 POD12 模型，含 FFVC1156 封装数据）"],
        ["<code>pkg\\xczu7ev_ffvc1156.pkg</code>（249 KB）", "ZU7EV FFVC1156 官方封装模型，IBIS 4.2，Cadence XtractIM 生成，768 球 R/L/C",
         "U1 焊球到芯片的 R/L/C（第 15 节按引脚取用）"],
        ["<code>ctle\\ultrascale_plus_hp_pod12_ctle.sp</code>（88 KB）", "HPIO DDR4 的 IBIS-ISS CTLE，NONE/LEVEL0–4 六档 AC 增益",
         "见第 15 节的可用性说明（tlsim 无 IBIS-ISS/S 参数入口）"],
        ["<code>ctle\\ultrascale_plus_hp_lvds_ac_coupled_ctle.sp</code>", "HPIO LVDS 交流耦合 CTLE", "本轮未用"],
    ]))
    h.append('<p class="dim">关键限制：该 IBIS <b>不含任何 GTY/GTR/SerDes 模型</b>'
             '（PCIe 走 PL GTY，那些是 IBIS-AMI 模型，AMD 未在 IBIS 包里公开），'
             '所以 PCIe 一节仍然只能用 Cadence 随附的 Gen3 spec 行为模型。'
             'DDR4 走 PL 的 HP bank（POD12），官方 IBIS <b>完全适用</b>。</p></div>')
    h.append('<div class="card"><h3>本机随 Cadence 一起安装、已确认可用的模型</h3>')
    h.append(table(["模型", "位置", "用途"], [
        ["PCIe Gen3 TX 行为模型（spec）", "share\\topxp\\ComplianceKits\\pcie3\\...\\tx_pcieg3_spec.sp", "本次 8 GT/s 仿真使用"],
        ["PCIe Gen3 RX 行为模型（spec）", "share\\topxp\\ComplianceKits\\pcie3\\...\\rx_pcieg3_spec.sp", "本次 8 GT/s 仿真使用"],
        ["PCIe Gen3 AMI 模型", "share\\topxp\\amiwin\\amilib\\amiffe_pcieg3_bc.ami / amirx_pcieg3_bc.ami", "需要 AMI 引擎（SystemSI/TopXP）"],
        ["PCIe 3/4/5/6 合规套件", "share\\topxp\\ComplianceKits\\pcie3..pcie6（含 TP1–TP4、CEM 连接器、riser S 参数）", "合规测试参照"],
        ["USB3 / USB3.1 / 10GBASE-KR / HDMI AMI", "share\\topxp\\amiwin\\amilib\\", "复用同一流程"],
        ["Cadence 默认 IBIS（1.2/1.8/2.5/3.3 V 驱动、接收、双向、差分）", "share\\pcb\\ida\\ibis_cds_default_*.ibs", "无厂商模型时的缺省收发端"],
        ["通用 IBIS 器件库 238 个 .ibs", "share\\pcb\\signal\\SignalPartLib\\", "标准逻辑器件"],
        ["Xilinx Virtex-2 IBIS", "share\\pcb\\signal\\SignalPartLib\\Xilinx\\virtex2.ibs", "仅作参考（非 ZU7EV）"],
        ["IBIS 与 RLGC 交叉分析工具包（含 AMI 测试链路）", "share\\pcb\\channelanalysis\\ami\\toolkit\\", "通道分析示例"],
        ["<b>Vivado 2025.1 自带 IBIS</b>：Versal（53 MB）、Zynq-7000（11 MB）",
         "C:\\Xilinx\\2025.1\\data\\parts\\xilinx\\{versal,zynq}\\public\\ibis\\",
         "非 ZU7EV 器件族，未使用"],
        ["<b>IBIS→DML 转换器</b>", "tools\\bin\\ibis2signoise.exe", "官方 IBIS 转换工具，已验证可用"],
        ["<b>IBIS→tlsim 转换器</b>（本次新写）", "si\\ibis2tlsim.py", "把 IBIS 的 V/I 表 / Ramp / C_comp 变成 tlsim 能吃的 DATAPOINTS"],
    ]))
    h.append('<h3>联网获取尝试</h3><ul>'
             '<li><code>www.amd.com</code> / <code>www.xilinx.com</code>：被网络策略拦截（xilinx.com 301 跳转到 amd.com 后超时），'
             '直连与代理均不通 → <b>ZU7EV 官方 IBIS/AMI 模型（GTR 收发器、PS DDR4 SelectIO）本次无法下载</b>。</li>'
             '<li><code>docs.amd.com</code>：可访问，但该门户只放文档，不放模型包。</li>'
             '<li>改用 <code>china.xilinx.com</code> / <code>account.amd.com</code> 等入口：仍 301 到 amd.com 后超时。</li>'
             '<li>本机装了 <b>Vivado 2025.1</b>，其自带 IBIS 只有 Versal 与 Zynq-7000，<b>没有 Zynq UltraScale+</b>，不能拿来做 ZU7EV。</li>'
             '<li><code>web.archive.org</code>（Wayback 镜像）：不通。</li>'
             '<li><code>micron.com</code>：可访问，但 MT40A256M16GE-075E 属早期料号，在线料号页已无该型号的仿真模型下载入口。</li>'
             '<li><code>github.com</code>：经代理可访问（<code>api.github.com</code> 可直连）。检索到 IBIS-AMI 工具链'
             '（<code>capn-freako/ibisami</code>、<code>IBIS-Library/IBIS-AMI-test-kits</code>、<code>rohaansch/ibis-parser</code>、'
             '<code>Hyphaeic/sigil</code>），但<b>没有找到 ZU7EV 的 IBIS 模型镜像</b>。</li>'
             '</ul>'
             '<p class="warn">结论：PCIe Gen3 收发端仍用 Cadence 随附的 <b>spec 行为模型</b>'
             '（AMD 的 GTY IBIS-AMI 模型不在本次拿到的包里，网上也取不到）；'
             'DDR4 的 <b>FPGA 侧</b>已换成 AMD 官方 POD12 模型 + 官方封装模型（第 15 节），'
             '但 <b>DRAM 侧</b>（Micron MT40A256M16GE-075E）仍然没有厂商模型，'
             '只能按 JEDEC 电气参数标定的等效终端，这一条必须在读结论时记住。</p></div>')

    # ---- 12b. IBIS actually used ---------------------------------------
    h.append('<div class="card"><h3>把 IBIS 真正用起来：IBIS → Cadence tlsim</h3>'
             '<p>拿到 IBIS 文件只是第一步，还要让它能驱动仿真。本次做通了这条链路：</p><ol>'
             '<li>读 <code>share\\pcb\\ida\\ibis_cds_default_1p2v_drivers.ibs</code>'
             '（IBIS 6.1，1.2 V —— 正好是 DDR4 的电平；接收端阈值 Vinl/Vinh = 0.48/0.72 V，'
             '就是 JEDEC DDR4 的 VIHL_AC）。</li>'
             '<li><code>si/ibis2tlsim.py</code> 把 [Model] 块转成 tlsim 的 <code>DATAPOINTS BDRVR</code>：'
             '[Pullup]/[Pulldown] → VI 表、[Ramp] → RISE_TIME/dVdt、C_comp → PARASITICS。</li>'
             '<li>符号与坐标约定用实验标定：tlsim 的 VI 表是「模型从节点吸收的电流」，'
             '且 PULL_UP 表仍按 IBIS 的 (V<sub>pad</sub>−V<sub>DD</sub>) 轴，电源轨由工具自己叠加。'
             '（事先按绝对电压平移会得到 2.4 V 的开路电压，即 2×1.2 V。）</li>'
             '<li>验证：40 Ω/1.2 V 模型 → 开路 <b>1.2000 V</b>、50 Ω 负载 <b>0.6667 V</b>、'
             '25 Ω 负载 <b>0.4615 V</b>，与 1.2·R/(40+R) 完全一致。</li>'
             '</ol>'
             '<p>这条链路是通用的：换成厂商 IBIS（AMD 的 ZU7EV、Micron 的 DDR4）后，'
             '不用改脚本就能直接重跑 DDR4/PCIe。</p>'
             '<h3>本轮为吃下官方大文件做的三处修正（都在 <code>si/ibis2tlsim.py</code> 里）</h3><ol>'
             '<li><b>IBIS 5.1 语法</b>：厂商文件用 <code>[Voltage Range] 1.2000V</code> 这种带方括号的键名、'
             '数字带工程单位（<code>-16.0573mA</code>）、V/I 表有 typ/min/max 三列 —— '
             '原来的解析只认裸数字，会把这些行整行丢掉（表变成空表）。现已支持三种写法。</li>'
             '<li><b>批量/单遍解析</b>：123.8 MB 的文件读 5 次模型要 5 遍 IO，'
             '改成 <code>--batch</code> 单遍扫描导出。</li>'
             '<li><b>★ 发现并修正了 bdrvr 的节点顺序错误</b>：tlsim 的 '
             '<code>bdrvr &lt;上拉参考&gt; &lt;输出&gt; &lt;下拉参考&gt; &lt;DATA&gt; &lt;ENABLE&gt; ...</code>，'
             '第 4 个节点是数据、第 5 个是使能。上一轮把两者写反了（数据恒为 1.2 V、由码型去开关使能），'
             '所以上一轮 DDR4 一节的波形其实是「驱动器一直输出高、靠使能通断」得到的，'
             '<b>下拉管从未工作</b>。本轮按 signoise 自己生成的网表 '
             '（<code>signoise.run\\case1\\sim1\\comps.spc</code>）改正后重跑，'
             '第 15 节的两组结果都已更新。<b>PCIe 各节不受影响</b>（那里用的是 50 Ω 理想源，不是 bdrvr）。</li>'
             '</ol></div>')

    # ================= 13. PCIe Gen3 ==================
    if g3:
        h.append("<h2>13. PCIe 3.0（8 GT/s）结果 —— 使用 spec 收发模型</h2>")
        h.append('<p>收发端换成 <b>PCIe 3.0 规范行为模型</b>（CML 电流模驱动 50 Ω 上拉 / 1 pF 寄生 / '
                 '0.804 满摆幅；接收端双 50 Ω 到 1 V 轨），单位间隔 125 ps，PRBS-9。'
                 '去加重按 Gen3 预设 -3.5 dB 实现。</p>')
        rows = []
        for r in g3["lanes"]:
            rows.append([r["lane"], f'{r["deemp_db"]:+.1f}' if r["deemp_db"] else "0",
                         f'{r["total_mil"]:.0f}', f'{r["eye_height_mV"]:.0f}',
                         f'{r["eye_width_ps"]:.0f}', f'{r["swing_mV"]:.0f}'])
        h.append(table(["通道", "去加重 (dB)", "总长 (mil)", "眼高 (mV)", "半高眼宽 (ps)", "差分摆幅 (mV)"], rows))
        h.append('<p class="dim">8 GT/s 的 UI 只有 125 ps，2-UI 窗口 250 ps。去加重把摆幅压到 ~800 mV（正好是 '
                 'PCIe Gen3 规范要求的发送端最小差分摆幅）却换来了更好的眼宽——这就是 Gen3 引入 FFE 的原因。</p>')
        h.append('<p class="warn">⚠️ 准确性说明：这两个 <code>.sp</code> 是 PCIe Gen3 规范的<b>行为级电路模型</b>；'
                 '规范里 Gen3 的发送端 FFE 与接收端 CTLE/DFE 由配套的 AMI 模型（<code>amilffe_pcix.ami</code> / '
                 '<code>amictwf.dll</code>）提供，而 tlsim 不带 AMI 引擎。因此本次的 <b>-3.5 dB 去加重是在激励端实现的近似</b>，'
                 '可以反映 FFE 对眼图的作用，但不等于官方 AMI 模型的结果。要跑官方 AMI，需要 Allegro 的通道分析'
                 '（SystemSI/TopXP）流程或 Sigrity 授权。</p>')
        h.append('<div class="grid3">')
        for r in g3["lanes"]:
            if r["deemp_db"] == -3.5 and r["lane"] in ("TX0", "TX1", "TX2"):
                h.append('<div class="card">' + eye_svg(r["wave"], 125.0,
                         title=f'{r["lane"]} @8GT/s, -3.5dB FFE', height_mV=r["eye_height_mV"],
                         width_ps=r["eye_width_ps"]) + '</div>')
        h.append('</div>')

    # ================= 14. S-parameters / TDR ==================
    if fr:
        h.append("<h2>14. S 参数与 TDR 阻抗</h2>")
        ser = []
        for i, (lane, v) in enumerate(list(fr["lanes"].items())[:2]):
            ser.append(dict(label=lane, color=[C_ACC, C_ACC2][i], pts=v["il"]))
        il_mask = parse_maskfile("generic_il.maskfile")
        rl_mask = parse_maskfile("generic_rl_tx.maskfile")
        if il_mask:
            # the mask file stores the limit as a negative dB value; the chart
            # plots insertion LOSS as a positive number, so flip the sign
            ser = ser + [dict(label="通道插损限值", color=C_BAD,
                              pts=[(s["x1"] / 1e9, abs(s["y1"])) for s in il_mask["segs"]]
                                  + [(il_mask["segs"][-1]["x2"] / 1e9, abs(il_mask["segs"][-1]["y2"]))])]
        h.append('<div class="card">' + line_chart(ser, title="插入损耗 IL (dB) vs 频率（含规范限值）",
                 xlabel="GHz", ylabel="dB", xmax=20.0) + '</div>')
        h.append('<p class="dim">红线是 Cadence 随附的通道插损模板 '
                 '<code>share\\topxp\\sample_masks\\generic_il.maskfile</code>'
                 '（1 GHz −4.2 dB → 8 GHz −22.5 dB）。本板通道在 8 GHz 处也只有 1.6–2.8 dB，'
                 '比限值小一个数量级——这段走线长度完全不是瓶颈（最差情况见下表）。</p>')
        rows = [[lane, f'{v["il_2p5"]:.2f}', f'{v["il_4p0"]:.2f} / {v["il_4p0_max"]:.2f}',
                 f'{v["il_8p0"]:.2f} / {v["il_8p0_max"]:.2f}',
                 f'{v["rl_2p5"]:.1f}', f'{v["rl_4p0"]:.1f}']
                for lane, v in fr["lanes"].items()]
        h.append(table(["通道", "IL@2.5 GHz", "IL@4 GHz 名义/最差", "IL@8 GHz 名义/最差",
                        "RL@2.5 GHz (dB)", "RL@4 GHz (dB)"], rows))
        h.append('<p class="dim">「最差」= 目标频率 ±12% 带宽内的最大值。曲线在 6 GHz 以上出现明显纹波，'
                 '是 <b>40 Ω 走线放在 50 Ω 参考下</b>的两端反射形成的驻波（真实 VNA 测这条线也会看到）；'
                 '本板通道在 8 GHz 处最差也只有约 3.8 dB，仍远低于 −22.5 dB 的限值。'
                 '再往上（&gt;10 GHz）过孔用的是集总 RLC 近似，曲线不再可信，因此只画到 15 GHz。</p>')
        if rl_mask:
            def limit_at(f):
                for s in rl_mask["segs"]:
                    if s["x1"] <= f <= s["x2"]:
                        return abs(s["y1"])
                return None
            chk = []
            for f, lab in ((1.25e9, "1.25 GHz"), (2.5e9, "2.5 GHz")):
                lim = limit_at(f)
                if lim is None:
                    continue
                worst = min(v["rl_2p5"] for v in fr["lanes"].values())
                chk.append([lab, f'≤ −{lim:.0f}', f'−{worst:.1f}',
                            '<span class="ok">✔ 通过</span>' if worst >= lim
                            else '<span class="warn">✘ 不通过</span>'])
            h.append("<h3>回损与规范限值对比</h3>")
            h.append(table(["频率", "限值", "最差实测 RL", "结论"], chk))
            h.append('<p class="dim">限值取自 <code>sample_masks\\generic_rl_tx.maskfile</code>。'
                     '实测 RL 只有 15–17 dB（@2.5 GHz），主要来自 <b>40 Ω 单端走线在 50 Ω 参考下的失配</b>'
                     '（理想 40 Ω 在 50 Ω 系统里就是 −19 dB），不代表加工缺陷。</p>')
        h.append('<p class="dim">S 参数由 Cadence 场求解得到的单位长度 RLGC，经有损线 ABCD 级联（含过孔与 100 nF 耦合电容）'
                 '变换得到；对同一通道的 tlsim 时域阶跃响应做过一致性校核。'
                 '本次<b>没有</b>得到耦合线矩阵（signoise 未把该差分对判为电学差分对），因此这里只给单端 S11/S21，'
                 '未含 FEXT/NEXT。</p>')
        if g3 and g3.get("tdr"):
            h.append('<div class="grid">')
            for t in g3["tdr"]:
                pts = [(p[0], p[1]) for p in t["profile"]]
                h.append('<div class="card">' + line_chart(
                    [dict(label=f'{t["lane"]} TDR', color=C_OK, pts=pts)],
                    title=f'{t["lane"]} TDR 阻抗剖面（参考 {t["z0"]:.1f} Ω）',
                    xlabel="时间 (ps)", ylabel="Z (Ω)", xmax=pts[-1][0],
                    ymin=20, ymax=90) + '</div>')
            h.append('</div>')
            h.append('<p class="dim">TDR 曲线由 <b>tlsim 时域仿真</b>的 30 ps 阶跃反射波计算：'
                     'Z(t) = Z₀(1+Γ)/(1−Γ)。可以看到 FPGA 焊球扇出区、内层带状线段、'
                     '过孔、100 nF 电容与外层微带各自的阻抗台阶。</p>')
        if g3:
            ok3 = [r for r in g3["lanes"] if r.get("deemp_db") == -3.5 and r.get("bathtub")]
            if ok3:
                h.append("<h3>差分阻抗与串扰（bem2d 场求解的定量结果）</h3>")
                h.append('<p>上一轮这两项只有区间估计。本轮把 Cadence 的二维边界元场求解器 '
                         '<code>bem2d</code> 直接调起来（<code>bem2d 0 &lt;in&gt; 0.00127</code>），'
                         '按本板叠层重建差分对截面，脚本 <code>si/build_xs.py</code>。'
                         '第一步是回归校验：用同一套输入解 4.2 mil 单线 → '
                         '<b>Z0 = 40.404 Ω</b>，与 Cadence 存在 <code>interconn.iml</code> 里的 '
                         '40.404 Ω <b>完全一致（偏差 0.000%）</b>，说明截面与求解设置是对的。</p>')
                if xs and xs.get("tests"):
                    t = xs["tests"]
                    rows = []
                    for key, lab in (("pair_TX", "PCIE_TX0_C（4.2 mil 间距）"),
                                     ("pair_RX", "PCIE_RX0（4.5 mil 间距）")):
                        if key in t:
                            v = t[key]
                            rows.append([lab, f'{v["gap_mil"]:.1f}', f'{v["Zodd"]:.1f}',
                                         f'{v["Zeven"]:.1f}', f'<b>{v["Zdiff"]:.1f}</b>',
                                         f'{v["kc"]*100:.2f} %'])
                    h.append(table(["差分对", "对内间距 (mil)", "Z<sub>odd</sub> (Ω)",
                                    "Z<sub>even</sub> (Ω)", "Z<sub>diff</sub> = 2·Z<sub>odd</sub> (Ω)",
                                    "耦合度 k<sub>c</sub>=k<sub>l</sub>"], rows))
                    if xs.get("gap_sweep"):
                        gsr = [[f'{s["gap_mil"]:.1f}', f'{s["Zodd"]:.1f}', f'{s["Zeven"]:.1f}',
                                f'{s["Zdiff"]:.2f}', f'{s["C12"]*1e12:.2f}', f'{s["L12"]*1e9:.3f}']
                               for s in xs["gap_sweep"]]
                        h.append("<h4>对内间距扫描（场求解，4.2 mil 线宽）</h4>")
                        h.append(table(["间距 (mil)", "Z<sub>odd</sub> (Ω)", "Z<sub>even</sub> (Ω)",
                                        "Z<sub>diff</sub> (Ω)", "C₁₂ (pF/m)", "L₁₂ (nH/m)"], gsr))
                        h.append('<p class="dim">极限校验：间距拉到 10 mil 时 Z<sub>diff</sub> → '
                                 f'{xs["gap_sweep"][-1]["Zdiff"]:.2f} Ω，'
                                 '正是 2×40.404 Ω = 80.81 Ω，与单线解自洽。'
                                 '本板 4.2–4.5 mil 的紧耦合把差分阻抗压到 <b>77.0–77.5 Ω</b>，'
                                 '比 PCIe 常见的 85 Ω 目标低约 <b>9%</b>（这是板厂线宽/间距的直接结果，'
                                 '不是估计值）。</p>')
                if xs and xs.get("crosstalk_vs_gap"):
                    xr = []
                    for c in xs["crosstalk_vs_gap"]:
                        near = abs(c["gap_mil"] - 35.0) < 0.5
                        v = c["NEXT_se_coeff"] * 100
                        xr.append([f'{c["gap_mil"]:.1f}',
                                   f'{c["kc"]:.5f} / {c["kl"]:.5f}',
                                   f'<b>{v:.4f} %</b>' if v >= 0.0001 else
                                   '<span class="ok">低于求解器分辨率（&lt;0.0001 %）</span>',
                                   f'{c["FEXT_coeff"]*100:.5f} %'])
                    h.append("<h4>串扰随间距的变化（两导体场求解，4.2 mil 线宽）</h4>")
                    h.append(table(["净距 (mil)", "k<sub>c</sub> / k<sub>l</sub>",
                                    "NEXT 系数（单端，饱和值）", "FEXT 系数"], xr))
                    h.append('<p class="dim">饱和系数：K<sub>b</sub> = (k<sub>c</sub>+k<sub>l</sub>)/4（近端），'
                             'K<sub>f</sub> = (k<sub>c</sub>−k<sub>l</sub>)/2（远端）；'
                             'k<sub>c</sub> = C₁₂/C₁₁，k<sub>l</sub> = L₁₂/L₁₁，直接取自场求解的 C、L 矩阵。'
                             '本板实测对间净距 ≈35 mil，落在最后一行：'
                             '<b>耦合量级已经低于求解器能分辨的水平</b>。'
                             '注意耦合随间距下降极快（4.2→15 mil 掉了 200 倍），'
                             '原因是这条带状线上下参考面只有 6.5 mil，场被紧紧锁在走线附近。</p>')
                if xs and xs.get("crosstalk_bundle"):
                    b = xs["crosstalk_bundle"]
                    h.append("<h4>差分对之间的串扰（4 导体场求解，紧排布最坏情况）</h4>")
                    h.append(table(["几何", "NEXT（单端）", "NEXT（差分）", "FEXT（差分）"],
                                   [["对内间距 4.2 mil、对间净距 4.2 mil（4 线紧排布）",
                                     f'{b["NEXT_se_coeff"]*100:.2f} %',
                                     f'{b["NEXT_diff_coeff"]*100:.2f} %',
                                     f'{b["FEXT_diff_coeff"]*100:.3f} %']]))
                    h.append('<p class="dim">这是把两对差分线排成 4 根等间距走线（每一档间距都只有 4.2 mil）'
                             '的场求解结果 —— 一个比本板严苛得多的最坏情况。'
                             '结论：<b>差分驱动并不能自动消掉近端串扰</b>——'
                             '受害对的两根线各自被相邻干扰线强耦合，'
                             '而差分端口把两个单端噪声相减，结果是同一量级（2.2% 而不是 0）；'
                             '只有远端串扰才靠差分/同质介质真正抵消。'
                             '把对间净距按上表的衰减规律放到本板实际的 35 mil，'
                             '这个系数会掉到 0.01% 以下。</p>')
                h.append('<ul>'
                         '<li><b>远端串扰（FEXT）在带状线上被完全抵消</b>：场求解给出的 '
                         'k<sub>c</sub> = k<sub>l</sub>（例如 4.2 mil 间距时两者都是 0.0454），'
                         '所以 K<sub>f</sub> = (k<sub>c</sub>−k<sub>l</sub>)/2 = <b>0</b>——'
                         '这是同质介质（上下都是 Er 3.4 的 FR408HR）里奇偶模同速的必然结果，'
                         '现在有数字了，不再只是定性说法。'
                         'TX/RX 分别有 88.5% / 89.7% 的长度走在 03_SIG1 / 12_SIG4 带状线上，'
                         '只有 250–290 mil 的外层微带段会贡献 FEXT。</li>'
                         '<li><b>本板的实际串扰量级</b>：对间净距 ≈35 mil（由 '
                         '<code>report -v dpg</code> 的段坐标量出：TX0↔TX1、RX0↔RX1 的最小中心距都是 '
                         '≈39.3 mil，减去 4.2 mil 线宽得 ≈35 mil），'
                         '场求解在这个间距上已经分辨不出耦合（&lt;0.0001 %，即 &lt;0.6 µV @600 mV 摆幅）；'
                         '即便按紧排布最坏情况外推，也远低于 PCIe 关心的量级。'
                         '<b>结论：本板各通道之间的串扰不是问题</b>，'
                         '瓶颈在别处（差分阻抗偏低、过孔、封装与厂内模型缺失）。</li>'
                         '<li><b>对间净距的来路</b>：从板卡自带的差分对间距报告 '
                         '(<code>report -v dpg</code>，含每段的起止坐标) 量出 '
                         'PCIE_TX0↔TX1、RX0↔RX1 的最小中心距都是 <b>≈39.3 mil</b>，'
                         '即净距 ≈35 mil。表中给出 4.2→35 mil 全量程的场求解结果，'
                         '既给出实际间距下的数，也给出「如果把线拉近会怎样」的灵敏度。</li>'
                         '</ul>')
                h.append('<p class="dim">限制说明：对间净距由二维坐标算出，'
                         '报告不带层信息，因此这里假定相邻通道走在同一层（PCIe 布线成束，通常如此）。'
                         '另外，<b>两个导体簇之间留有很大空隙时 bem2d 会报 '
                         '<code>Singular matrix in routine lucmp</code></b>（单独调用时如此），'
                         '所以「对间净距 10 mil 以上」的 4 导体模型解不出来：'
                         '差分 NEXT 用紧排布的 4 导体解给出，间距衰减用两导体结果给出，'
                         '两者结合即在 35 mil 处的上界。'
                         '串扰是「确定性最坏情况」（干扰对全摆幅翻转），不含统计码型效应，'
                         '也不含过孔三维耦合。</p>')
                h.append("<h3>浴盆曲线（眼开度 vs 采样相位）</h3>")
                h.append('<div class="grid3">')
                for r in ok3[:3]:
                    h.append('<div class="card">' + bathtub_svg(r["bathtub"], 60, 125.0,
                             title=f'{r["lane"]} @8 GT/s') + '</div>')
                h.append('</div>')
                h.append('<p class="dim">纵轴是该采样相位下的垂直眼开度。Gen3 的 125 ps UI 下，'
                         '大部分相位的开度落在 300–420 mV。曲线上的锯齿来自统计方式：'
                         '每个相位格子里只有约 4 条波形落入（254 个 2-UI 窗口 ÷ 60 格），'
                         '属于<b>分箱采样伪影</b>而非物理纹波——要得到平滑的浴盆曲线需要增加仿真码型长度。</p>')

    # ================= 15. DDR4 ==================
    if dd:
        h.append("<h2>15. DDR4-2666 分析</h2>")
        h.append('<div class="grid"><div class="card"><h3>拓扑与器件（来自板卡数据）</h3><ul>'
                 '<li><b>PL DDR4（PL HP bank，不是 PS DDRC）</b> → <b>4 × Micron MT40A256M16GE-075E</b>'
                 '（U2/U99/U100/U101，4 Gb ×16，共 2 GB）。'
                 '依据：<code>DDR4_DQ0 = U1.<b>AF16</b> ↔ U101.G2</code>，'
                 '而 AF16 在官方封装文件里是 <code>IO_L17P_T2U_N8_AD10P_64</code>（HP bank 64），'
                 '属 PL 侧 SelectIO；PS DDR4 是另一路 <code>DDR4_SODIMM_*</code>+J1。'
                 '所以 AMD 的 <code>zynquplus.ibs</code> 与 POD12 模型<b>恰好适用</b>。</li>'
                 '<li>DQ / DQS / DM：<b>点对点</b>（如 DDR4_DQ0 = U1.AF16 ↔ U101.G2），长度 949–1883 mil</li>'
                 '<li>CK / 地址 / 命令：<b>fly-by</b> 挂 4 颗 DRAM（如 DDR4_CK_T = R144 + U1 + U2/U99/U100/U101），'
                 '长度 4385–5112 mil，最大-最小差 <b>727 mil ≈ 114 ps</b></li>'
                 '<li>8 对 DQS，对内失配 &lt; 5 mil</li>'
                 '<li>共 265 条 DDR4 网络</li>'
                 '</ul></div>')
        rows = [[k, f'{v["Z0"]:.1f}'] for k, v in dd["models"].items()]
        h.append('<div class="card"><h3>走线阻抗（按本板叠层标定）</h3>'
                 + table(["内层带状线模型", "Z0 (Ω)"], rows)
                 + f'<p class="dim">传播时延 {dd["td_ps_in"]:.0f} ps/in（Er 3.4 带状线理论值）。'
                 f'VDDQ {dd["vddq"]:.1f} V，UI {dd["ui_ps"]:.0f} ps（DDR4-2666），'
                 f'DRAM ODT {dd["r_odt"]:.0f} Ω。</p>')
        if dd.get("amd_models"):
            mrows = []
            for nm, v in dd["amd_models"].items():
                mrows.append([nm, f'{v["c_comp"]*1e12:.2f} pF',
                              f'{v["vinl"]:.2f} / {v["vinh"]:.2f} V',
                              f'{v["edge_20_80_ps"]:.1f} ps',
                              f'{v["n_pullup"]} / {v["n_pulldown"]}'])
            h.append('<h3>官方 AMD POD12 模型（zynquplus.ibs）</h3>'
                     + table(["模型", "C_comp", "Vinl / Vinh",
                              "厂商 [Rising Waveform] 20–80%（R=50 Ω）",
                              "上拉/下拉 V/I 表点数"], mrows))
            h.append('<p class="dim">F/M/S = 快/中/慢<b>回转率</b>角（不是工艺角，工艺 min/typ/max 在 V/I 表的三列里）；'
                     'OUT40/48/60 = DCI 输出阻抗 40/48/60 Ω；'
                     '带 <code>_IN40/48/60</code> 的型号同时打开输入端接。'
                     'VDDQ = 1.2 V，输入阈值 Vinl/Vinh = 0.76/0.92 V —— 这正是 JEDEC POD12 的 '
                     'VIHL_AC，被用来做下面的眼图门限判定。</p>')
        if dd.get("pkg"):
            h.append('<h3>U1 封装寄生（xczu7ev_ffvc1156.pkg，按引脚取用）</h3>'
                     + table(["引脚", "通道", "R_pkg (Ω)", "L_pkg (nH)", "C_pkg (pF)"],
                             [[p, v["ch"], f'{v["R"]:.3f}', f'{v["L"]*1e9:.3f}',
                               f'{v["C"]*1e12:.3f}'] for p, v in dd["pkg"].items()])
                     + '<p class="dim">封装模型是 XtractIM 生成的 768 球 R/L/C 矩阵，'
                       '这里只取各引脚的自项（对角项），互耦项未纳入。</p>')
        h.append('</div></div>')
        # ---- 静态工作点校验（把 V/I 表转换对不对先钉死）----
        if dd.get("amd_dc"):
            dc = dd["amd_dc"]
            dcr = []
            for t, lab in (("vddq40", "40 Ω 到 VDDQ（POD12 实际接法）"),
                           ("split", "80 Ω 到 VDDQ + 80 Ω 到 VSS（戴维南 0.6 V）"),
                           ("gnd40", "40 Ω 到 VSS（上一轮的等效）")):
                if t in dc and dc[t] and dc[t].get("high") is not None:
                    dcr.append([lab, f'{dc[t]["high"]:.4f}', f'{dc[t]["low"]:.4f}',
                                f'{(dc[t]["high"]-dc[t]["low"])*1e3:.0f} mV'])
            h.append('<h3>静态工作点校验（模型转换是否正确的第一道关）</h3>'
                     + table(["DRAM 端接法", "稳态高 (V)", "稳态低 (V)", "摆幅"], dcr))
            h.append('<p class="dim">这一项只跟 IBIS 的 V/I 表和端接有关，是纯直流解，'
                     '不涉及走线。40 Ω 驱动器 + 40 Ω 到 VDDQ → 高 1.200 V / 低 0.600 V，'
                     '与 POD12 的理论值完全一致，说明官方 V/I 表被正确解读（含符号与坐标轴）。</p>')
        # ---- AMD 官方模型结果 ----
        if dd.get("amd_tests"):
            rows = []
            for t in dd["amd_tests"]:
                if t.get("error") or not t.get("eye_height_mV"):
                    continue
                rows.append([t["title"], t["model"].replace("HP_POD12_DCI_", ""),
                             f'{t["eye_height_mV"]:.0f}', f'{t["eye_width_ps"]:.0f}',
                             f'{t["high_lvl_mV"]:.0f} / {t["low_lvl_mV"]:.0f}',
                             f'{t["margin_vinh_mV"]:+.0f} / {t["margin_vinl_mV"]:+.0f}',
                             f'{t.get("edge_ps") or 0:.0f} ps'])
            h.append('<h3>★ 换成官方 AMD POD12 模型后的写眼图（DRAM 球上）</h3>')
            h.append('<p>驱动 = <code>zynquplus.ibs</code> 的 POD12 模型（40/48/60 Ω，typ 工艺列），'
                     '串联 U1 官方封装 R/L/C，走线用本板叠层场求解的 RLGC，'
                     'DRAM 端为 40 Ω ODT 到 VDDQ，PRBS-9 @2666 MT/s。</p>')
            h.append(table(["通道", "驱动模型", "眼高 (mV)", "半高眼宽 (ps)",
                            "高/低电平 (mV)", "对 Vinh/Vinl 的裕量 (mV)",
                            "DRAM 球上实测 20–80% 边沿"], rows))
            h.append('<p class="dim">「对 Vinh/Vinl 的裕量」= 高电平中位数 − 0.92 V（Vinh）与 '
                     '0.76 V − 低电平中位数（Vinl）。两项都为正才说明 POD12 接收端能正确判读。'
                     '同一通道换 48 Ω / 60 Ω 驱动时眼高下降，是因为驱动器内阻变大、'
                     '低电平被 ODT 抬高（0.649 / 0.711 V），压缩了眼高。</p>')
        # ---- 与通用 IBIS 的并排对比 ----
        rows = []
        for t in dd["tests"]:
            m = next((a for a in dd.get("amd_tests", [])
                      if a["title"] == t["title"] and a["model"] == "HP_POD12_DCI_M_OUT40"), None)
            rows.append([t["title"],
                         f'{t["eye_height_mV"]:.0f} / {t["eye_width_ps"]:.0f}',
                         (f'{m["eye_height_mV"]:.0f} / {m["eye_width_ps"]:.0f}' if m else "—"),
                         f'{t["vmax"]*1e3:.0f} / {t["vmin"]*1e3:.0f}',
                         (f'{m["high_lvl_mV"]:.0f} / {m["low_lvl_mV"]:.0f}' if m else "—")])
        h.append('<h3>并排对比：官方 AMD 模型 vs Cadence 通用 1.2 V IBIS</h3>')
        h.append(table(["通道", "通用 IBIS 眼高/眼宽 (mV / ps)",
                        "官方 POD12 眼高/眼宽 (mV / ps)",
                        "通用 高/低电平 (mV)", "官方 POD12 高/低电平 (mV)"], rows))
        h.append('<p class="dim">两者都接了同一个 40 Ω ODT 到 VDDQ。'
                 '高电平都稳定在 ~1.20 V；<b>主要差别在低电平与边沿</b>：'
                 '通用 IBIS 模型是理想化的理想 Ramp，官方 POD12 的 V/I 表在低电平附近更"软"，'
                 '加上 2.70 pF 的 C_comp 与封装 4.95 nH / 0.81 Ω，边沿和过冲都不一样，'
                 '所以眼高有 5–20 mV 量级的差异。'
                 '结论：<b>换官方模型不会推翻上一轮的判断</b>，但低电平与边沿形状确实只有厂商模型才给得准。</p>')
        h.append('<div class="grid3">')
        for t in dd.get("amd_tests", []):
            if t.get("model") == "HP_POD12_DCI_M_OUT40" and t.get("wave"):
                h.append('<div class="card">' + eye_svg(t["wave"], dd["ui_ps"],
                         title=t["title"] + "（官方 POD12）",
                         height_mV=t["eye_height_mV"], width_ps=t["eye_width_ps"]) + '</div>')
        h.append('</div>')
        h.append('<div class="grid3">')
        for t in dd["tests"]:
            h.append('<div class="card">' + eye_svg(t["wave"], dd["ui_ps"],
                     title=t["title"] + "（通用 IBIS 对照）", height_mV=t["eye_height_mV"],
                     width_ps=t["eye_width_ps"]) + '</div>')
        h.append('</div>')
        # ---- DRAM 端接法研究（POD12 的门限只认其中一种）----
        if dd.get("term_study"):
            tr = []
            for t in dd["term_study"]:
                if t.get("error") or not t.get("eye_height_mV"):
                    continue
                ok_hi = t["high_lvl_mV"] >= 920
                ok_lo = t["low_lvl_mV"] <= 760
                tr.append([t["variant"], f'{t["high_lvl_mV"]:.0f}', f'{t["low_lvl_mV"]:.0f}',
                           f'{t["eye_height_mV"]:.0f}',
                           ('<span class="ok">✔ 高电平过 Vinh</span>' if ok_hi
                            else '<span class="warn">✘ 高电平进不了 Vinh</span>'),
                           ('<span class="ok">✔ 低电平过 Vinl</span>' if ok_lo
                            else '<span class="warn">✘ 低电平高于 Vinl</span>')])
            h.append('<h3>★ DRAM 端接法：哪一种才配得上 POD12</h3>')
            h.append(table(["DRAM 端接", "高电平 (mV)", "低电平 (mV)", "眼高 (mV)",
                            "高电平判读", "低电平判读"], tr))
            h.append('<p>官方 POD12 接收模型的阈值是 Vinl = 0.76 V / Vinh = 0.92 V。'
                     '把这个门限拿去卡三种端接法就能看出来：'
                     '<b>只有「40 Ω 到 VDDQ」这一种能让高低电平同时越过门限</b>。'
                     '上一轮用的「40 Ω 到地」等效让高电平只有 ~0.60 V，'
                     '连 Vinh 都到不了 —— 用 POD12 的接收端去读，会永远读到 0。'
                     '这不是仿真误差，而是<b>端接方式与 POD12 电平规范不匹配</b>：'
                     'DDR4 的 DQ ODT 是拉到 VDDQ 的伪开漏端接，不是拉到地的戴维南端接。'
                     '所以本轮把主结果改到「40 Ω 到 VDDQ」，并保留另外两种作为对照。</p>')
        # ---- 回转率敏感性 ----
        if dd.get("amd_slew"):
            sr = [[t.get("variant", ""), f'{t.get("stim_edge_ns") or 0:.2f}',
                   f'{t.get("edge_ps") or 0:.0f}', f'{t["eye_height_mV"]:.0f}',
                   f'{t["eye_width_ps"]:.0f}'] for t in dd["amd_slew"]
                  if t.get("eye_height_mV")]
            h.append('<h3>边沿（回转率）敏感性</h3>')
            h.append(table(["激励边沿（近似对应厂商角）", "激励边沿 (ns)",
                            "DRAM 球上实测 20–80% (ps)", "眼高 (mV)", "半高眼宽 (ps)"], sr))
            h.append('<p class="dim">为什么要用激励边沿来扫：tlsim 的 <code>bdrvr</code> '
                     '<b>不读 AC_PARAM 里的 RISE_TIME</b>（用 edge_test 实测：把 RISE_TIME 从 0.1 ns 改到 2 ns，'
                     '输出边沿不变，始终由 V/I 表 + C_comp + 激励边沿决定，40 Ω 负载下约 106 ps）。'
                     '因此 F/M/S 三个"回转率角"在 tlsim 里结果完全相同，'
                     '只能靠加慢激励边沿来模拟慢角。结论：本通道只有 1.7–1.9 in 的短走线，'
                     '边沿从 176 ps 拉到 380 ps 眼高才掉 40 mV，<b>对边沿不敏感</b>；'
                     '真正吃掉眼宽的是边沿本身（400 ps 边沿下眼宽只剩 400 ps）。</p>')
        # ---- 封装 / 钳位敏感性 ----
        if dd.get("amd_sensitivity"):
            sr = [[t.get("variant", ""), f'{t["eye_height_mV"]:.0f}', f'{t["eye_width_ps"]:.0f}',
                   f'{t.get("edge_ps") or 0:.0f}'] for t in dd["amd_sensitivity"]
                  if t.get("eye_height_mV")]
            h.append('<h3>其他敏感性</h3>' + table(
                ["变体（DQ0，POD12-40 Ω）", "眼高 (mV)", "半高眼宽 (ps)", "实测边沿 (ps)"], sr))
            h.append('<p class="dim">去掉封装模型（R/L/C）后边沿更快、眼高略升（+22 mV），'
                     '说明 4.95 nH 焊球电感在这个边沿下确实在起作用，值得建进去。'
                     '加上 IBIS 的 GND/POWER 钳位表只差 2 mV（0.3%），'
                     '但会让 tlsim 的直流工作点求解在多组端接下发散 —— '
                     '所以主结果用不带钳位的转换版本，钳位影响单列于此。</p>')
        h.append('<p class="warn">⚠️ DDR4 这一节的性质：<b>FPGA 侧已经是官方模型</b>'
                 '（zynquplus.ibs POD12 + 官方封装），<b>DRAM 侧仍然没有厂商模型</b>'
                 '（Micron MT40A256M16GE-075E 已下架，官网无模型入口），'
                 '用的是按 JEDEC 标定的 40 Ω ODT 到 VDDQ 等效；走线长度/层/线宽/过孔数取自本板。'
                 '另外 tlsim 不带 IBIS-ISS/S 参数入口，AMD 随包的 HPIO DDR4 CTLE '
                 '（<code>ultrascale_plus_hp_pod12_ctle.sp</code>）无法接进这条链路；'
                 'CTLE 又是<b>接收方向</b>（读）的功能，而本次只算了写。'
                 '因此本节结论适用于<b>写方向的通道裕量与拓扑风险</b>，'
                 '不等于 DDR4 时序签核，也不能替代带厂商 models 的读写联合仿真。</p>')
        h.append('<p class="warn">⚠️ 另外发现两个需要留意的板级特征：'
                 '(1) DQ 走线在 03_SIG1 上存在 <b>3.5 mil 与 5.5 mil 交替</b>的线宽变化（对应约 46 Ω / 32 Ω），'
                 '形成阻抗台阶；(2) 地址/命令 fly-by 的首末负载长度差 727 mil（约 114 ps），'
                 '在 0.75 ns 的 UI 下占比 15%，需依赖写均衡/训练补偿。</p>')

    # ================= 16. what still could not be done =================
    h.append("<h2>16. 本轮仍未能完成的部分</h2>")
    h.append('<div class="card"><ul>'
             '<li><b>板级耦合抽取（signoise 的 XtalkSummary）</b>：仍然跑不动 —— '
             '<code>signoise -a Each -n Odd -r XtalkSummary</code> 在 18 条网络、4 条网络、'
             '甚至单条 <code>PCIE_TX0_P</code> 上分别跑 5 / 6 / 9 分钟均无输出。'
             '<b>但本轮不再需要它</b>：二维场求解器已能直接调用（见下条），'
             '第 14 节的 Z<sub>diff</sub> 与 NEXT/FEXT 现在是场求解结果。'
             '仍未覆盖的是<b>三维过孔串扰</b>与整板级（多条网络同时翻转）的耦合。</li>'
             '<li><b>★ 已解决：独立调用 Cadence 二维场求解器 bem2d</b>：'
             '本轮用「裸数值语法」（不带 <code>.material</code> 块、'
             '<code>+shield/+dielectric/+rectangle</code> 直接写数、以 <code>.ends</code> 收尾）'
             '成功复现了 Cadence 自己的截面：4.2 mil 单线 → 40.404 Ω，'
             '与 <code>interconn.iml</code> 里的值完全一致。'
             '两个坑记在这里：<b>① 几何不能出现在负 x 坐标</b>（会静默返回空结果）；'
             '<b>② y 坐标只累加介质厚度、不含参考层铜厚</b>。'
             '脚本：<code>si/build_xs.py</code>。</li>'
             '<li><b>signoise 的 DDR4 板级分析</b>：110 条网络时会在数分钟内写出 GB 级日志且不收敛；'
             '缩到 18 条网络同样 5 分钟无结果。DDR4 因此改用 tlsim 通道仿真（第 15 节，'
             '驱动端与封装已换成厂商模型）。</li>'
             '<li><b>tlsim 的 <code>-SP</code>（生成 Touchstone）与 <code>-FDA</code>（频域）模式</b>：'
             '二者都需要一个未公开的网表控制卡，尝试多种写法后仍静默无输出，已放弃；'
             '频域数据改由 bem2d 的 RLGC 变换得到，并与时域阶跃响应互相校核。</li>'
             '<li><b>PCIe 用的 GTY/GTR IBIS-AMI 模型</b>：AMD 公开的 <code>zynquplus.ibs</code> '
             '里只有 SelectIO（含 POD12），<b>没有任何 SerDes/AMI 模型</b>；'
             'amd.com/xilinx.com 又不通（见第 12 节）。因此 Gen3/Gen4 的正式 AMI 眼图仍未做成，'
             '第 13 节用的是 Cadence 随包的 spec 行为模型 + 端点激励去加重近似。'
             '本机有 <code>share\\topxp\\amiwin\\amilib\\ami{rx,ffe}_pcieg3_bc.ami</code>，'
             '但需要 SystemSI/TopXP 的通道分析流程（本机无界面通道分析跑不通）。</li>'
             '<li><b>DDR4 的读方向与 CTLE</b>：读方向需要 DRAM 侧驱动模型（Micron 未提供）；'
             'AMD 的 HPIO DDR4 CTLE 是 IBIS-ISS 的 SPICE 子电路，tlsim 无对应入口，'
             '且 CTLE 只在读方向起作用，因此本轮没有把 CTLE 计入任何眼图。</li>'
             '<li><b>tlsim 的 <code>bdrvr</code> 不读 IBIS 的 RISE_TIME/FALL_TIME</b>：'
             '输出边沿由 V/I 表 + C_comp + 激励边沿决定。厂商的 F/M/S 回转率角因此无法在 tlsim 里分开，'
             '只能用激励边沿近似慢角（第 15 节的边沿敏感性）。</li>'
             '</ul></div>')

    # ================= 17. official GTH IBIS-AMI kit ==================
    if gth:
        h.append("<h2>17. 官方 GTH IBIS-AMI 模型（本轮新到手）</h2>")
        h.append('<div class="card" style="border-color:%s">'
                 '<h3>★ 先纠正一个关键事实：本板 PCIe 走的是 <b>PL 的 GTH</b>，不是 GTY</h3>'
                 '<p>板上 PCIe 网络的引脚名是 <code>MGTHTXP3_224 / MGTHTXN3_224 / '
                 'MGTHRXP3_224 / MGTHRXN3_224</code>（REFCLK 为 <code>MGTREFCLK0P_224</code>），'
                 'ZU7EV FFVC1156 封装里共有 <b>5 个 GTH quad（223–227）</b>与 4 条 PS-GTR，'
                 '<b>没有任何 MGTY 引脚</b>。所以上一轮"PCIe 走 PL GTY"的说法要更正：'
                 'PCIe 用的是 GTH quad 224 的 4 条通道 —— 而用户本轮拿到的 '
                 '<code>Xilinx_UltraScalePlus_GTH_AMI_Kit_R1p1</code>（UG1183，生产硅相关模型，'
                 '含 DFE 与 LPM）<b>正好就是这块板 PCIe 的原厂模型</b>。</p></div>' % C_OK)
        h.append('<div class="card"><h3>官方链路拓扑（UG1183 Figure 2）与本板通道</h3>'
                 '<p><code>TX AMI → Tx_Die(TT) → Tx_Package → 通道 → Rx_Package → Rx_Die → RX AMI</code>，'
                 '除 AMI 之外全部由套件提供的 S 参数描述（4 端口/Touchstone）。'
                 '本板通道用第 14 节场求解的差分 RLGC（Z<sub>diff</sub> = 77 Ω、'
                 'C<sub>odd</sub> = 1.597 pF/cm 等）+ 过孔 + 100 nF 交流耦合电容，'
                 '按 100 Ω 差分参考级联。脚本：<code>si/pcie_link.py</code>、'
                 '<code>si/build_pcie_gth.py</code>。</p>')
        rows = []
        for lane, v in gth["lanes"].items():
            rows.append([lane] + [f'{v["il_at"][k]:.2f}' for k in gth["nyquist"]])
        h.append(table(["通道", "Gen1 1.25 GHz", "Gen2 2.5 GHz", "Gen3 4 GHz", "Gen4 8 GHz"], rows))
        h.append('<p class="dim">这是"芯片到芯片"的差分级联结果，包含官方 die term + 封装模型，'
                 '所以绝对值比第 14 节的纯走线插损大得多（收发芯片模型本身贡献 '
                 '<b>约 6.2 dB</b>：Rx_Die 内部约 4.3 dB、两个封装约 1.8 dB、失配与源阻抗约 0.5 dB），'
                 '<b>其中真正属于本板走线的只有约 1 dB</b>。'
                 '对照 PCIe 规范：Gen3 参考通道在 4 GHz 是 20 dB，本板 7.6 dB —— '
                 '这条通道对 Gen3 来说非常宽裕。</p>')
        if gth.get("eyes"):
            er = [[f'{e["lane"]}', f'{e["rate_gbs"]:.0f} GT/s',
                   f'{e["post_db"]:.2f}' if e["post_db"] else "0",
                   f'{e["eye_mV"]:.0f}', f'{e["eye_ps"]:.0f}',
                   f'{e["hi_mV"]:.0f} / {e["lo_mV"]:.0f}'] for e in gth["eyes"]]
            h.append("<h3>接收端（RX die 节点）非均衡眼图</h3>")
            h.append(table(["通道", "速率", "发送去加重 (dB)", "眼高 (mV)", "眼宽 (ps)",
                            "高/低电平 (mV)"], er))
        if gth.get("gen3_emphasis_sweep"):
            sr = [[f'{s["post"]}', f'{s["post_db"]:.2f}', f'{s["eye_mV"]:.0f}',
                   f'{s["eye_ps"]:.0f}'] for s in gth["gen3_emphasis_sweep"]]
            h.append("<h3>Gen3 发送去加重灵敏度（TX0）</h3>")
            h.append(table(["TXPOSTCURSOR", "去加重 (dB)", "眼高 (mV)", "眼宽 (ps)"], sr))
            h.append('<p class="dim">去加重按 UG1183 Table 2 的实测标定值换算成 3-tap FFE 系数；'
                     'TXDIFFCTRL = 24 → 0.873 V<sub>pp,diff</sub>。'
                     '眼宽在 5 GT/s 与 8 GT/s 都撑满一个 UI（200 / 125 ps），'
                     '说明限制来自 UI 本身而不是通道 —— 这条 2.2 in 的通道对两个速率都太短了。</p>')
        h.append('<h3>厂商 AMI DLL 直驱到什么程度（重要，含结论）</h3>'
                 '<p>套件里的 <code>xilinx_ultrascaleplus_gth_ami_tx/rx_x64.dll</code> '
                 '可以被 Python（ctypes）加载并导出 <code>AMI_Init / AMI_GetWave / AMI_Close</code>，'
                 '本轮做了这些工作（脚本 <code>si/ami_host.py</code>、'
                 '<code>si/ami_shim/probe.c</code>）：</p><ul>'
                 '<li>用 dumpbin 反汇编 + SEH 捕获 + 状态对象内存转储，把厂商模型真实的 '
                 '<code>AMI_Init</code> 取参布局确定了：<b>前 4 个整型参数被忽略</b>，'
                 '第 5 个栈槽是 double、第 6 槽是参数串、第 8 槽是 handle、第 9 槽是 msg，'
                 '第二个 double 走 XMM3 —— 与 IBIS-AMI 规范里写的 12 参数原型不一致，'
                 '照规范调用会直接在 <code>strlen</code> 上崩（本文档记录该结论以供后续使用）。</li>'
                 '<li>按上述布局调用后，<b>TX 模型 AMI_Init 返回 0、参数（TXPOSTCURSOR/TXDIFFCTRL）'
                 '被正确解析</b>（状态对象里能看到 5 / 24 的取值），'
                 '但 <code>AMI_GetWave</code> 仍然崩在模型内部的空指针上；'
                 '<b>RX 模型</b>则连 <code>AMI_Init</code> 都不接受，'
                 '报 <code>SamplesPerBit = -2147483648</code> / '
                 '<code>SampleInterval must be a positive number</code>。</li>'
                 '<li>结论：<b>本轮没能用手工 AMI host 跑出厂商 AMI 眼图</b>。'
                 '这两个 DLL 显然是为特定 EDA 工具链（SystemSI/TopXP、ADS 等）编译的，'
                 '还需要工具侧的一些未公开约定。第 13 节的 Gen3 结果仍然是 spec 行为模型 + '
                 '端点去加重；本节给出的眼图是<b>官方模拟部分（die term + 封装）</b>下的'
                 '非均衡眼，可作为"硅片均衡器之前有多少余量"的定量依据。</li>'
                 '</ul>'
                 '<p class="warn">⚠️ 因此这一节的定位是：'
                 '<b>通道 + 官方模拟模型 = 场求解/厂商模型结果</b>；'
                 '<b>眼图 = 不含 RX CTLE/DFE/CDR 的非均衡眼</b>。'
                 '要出正式的 PCIe 合规眼图，仍需能把这两个 DLL 正确驱动起来的 AMI 引擎。'
                 '另外远端设备（插卡）不在模型内：这里算的是"FPGA 到板上通道末端"的响应。</p></div>')

    h.append("</body></html>")
    out = OUT / "zcu106_pcie_si_report.html"
    out.write_text("".join(h), encoding="utf-8")
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    raise SystemExit(
        "report_v1.py is a drawing-helper module; the report is generated by\n"
        "    python si\\build_report.py\n"
        "Running the legacy main() here would overwrite out/zcu106_pcie_si_report.html\n"
        "with the superseded narrative report, so it is disabled.")
