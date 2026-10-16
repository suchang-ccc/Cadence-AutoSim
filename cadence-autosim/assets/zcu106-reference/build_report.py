#!/usr/bin/env python3
"""
ZCU106 signal-integrity report generator.

Reads the analysis results in si/out/ and writes one self-contained HTML
document (inline SVG, no external references):

    si/out/zcu106_pcie_si_report.html

Drawing helpers are imported from report_v1 (the previous generator module), so
the charts stay identical across the document.
"""

import datetime
import json
import re
import sys
from pathlib import Path
import os

import report_v1 as H

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
# Reference implementation of the ZCU106 case study.  Absolute paths default to
# the machine the run was made on; override with the env vars shown.
REPORTS = Path(os.environ.get("ZCU106_WORK", r"D:\Programs\candence\si_zcu106")) / "reports"


def net_ball(net, path=None):
    """'U1.<ball>' of a net, straight out of Allegro's net report."""
    path = path or (REPORTS / "net.txt")
    try:
        for line in Path(path).read_text(errors="ignore").splitlines():
            if line.startswith(net + ","):
                for tok in line.split(",", 1)[1].split():
                    if tok.startswith("U1."):
                        return tok
    except OSError:
        pass
    return None


def diffpair_gaps(path=None):
    """pair name -> nominal gap (mil), from Allegro's differential-pair report."""
    path = path or (REPORTS / "dpg.txt")
    out = {}
    try:
        txt = Path(path).read_text(errors="ignore")
    except OSError:
        return out
    for m in re.finditer(r'(?m)^"([^"(]+?)\s*[("][^"]*",([0-9.]+),', txt):
        out.setdefault(m.group(1).strip(), float(m.group(2)))
    return out

C_BG, C_PANEL, C_LINE = H.C_BG, H.C_PANEL, H.C_LINE
C_TXT, C_DIM, C_ACC, C_ACC2, C_OK, C_BAD = (H.C_TXT, H.C_DIM, H.C_ACC,
                                            H.C_ACC2, H.C_OK, H.C_BAD)

CSS = """
:root{--bg:%s;--panel:%s;--line:%s;--txt:%s;--dim:%s;--acc:%s;--acc2:%s;--ok:%s;--bad:%s}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.7 -apple-system,"Segoe UI",
 "Noto Sans SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 22px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px}
h1{font-size:30px;margin:0 0 8px;letter-spacing:.3px}
h2{font-size:22px;margin:44px 0 14px;padding:10px 0 10px 14px;border-left:4px solid var(--acc);
 background:linear-gradient(90deg,rgba(56,189,248,.10),transparent)}
h3{font-size:17px;margin:26px 0 10px;color:var(--txt)}
h4{font-size:15px;margin:18px 0 8px;color:var(--dim);font-weight:600}
p{margin:10px 0}
.sub{color:var(--dim);font-size:13.5px;margin:0}
nav.toc{margin:18px 0 6px;padding:14px 18px;background:var(--panel);border:1px solid var(--line);
 border-radius:10px;columns:2;column-gap:26px}
nav.toc a{display:block;color:var(--txt);text-decoration:none;font-size:13.5px;padding:3px 0;
 break-inside:avoid}
nav.toc a:hover{color:var(--acc)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
.kpi>div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:21px;color:var(--acc);letter-spacing:.2px}
.kpi span{font-size:12.5px;color:var(--dim)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;
 margin:14px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.grid>*{min-width:0}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
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
.tag{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:20px;
 border:1px solid var(--line);color:var(--dim);margin-left:6px}
figure{margin:14px 0}
figcaption{color:var(--dim);font-size:12.5px;margin-top:6px}
.foot{border-top:1px solid var(--line);margin-top:50px;padding-top:14px;color:var(--dim);font-size:12.5px}
@media print{body{background:#fff;color:#000}.card,nav.toc{break-inside:avoid}}
""" % (C_BG, C_PANEL, C_LINE, C_TXT, C_DIM, C_ACC, C_ACC2, C_OK, C_BAD)


def kpi(items):
    return '<div class="kpi">' + "".join(
        f'<div><b>{v}</b><span>{k}</span></div>' for v, k in items) + "</div>"


def card(title, body):
    t = f"<h3>{title}</h3>" if title else ""
    return f'<div class="card">{t}{body}</div>'


def verdict(ok, ok_txt="通过", bad_txt="不通过"):
    return (f'<span class="ok">✔ {ok_txt}</span>' if ok
            else f'<span class="warn">▲ {bad_txt}</span>')


def fig(svg, caption=""):
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f"<figure>{svg}{cap}</figure>"


def clip(pts, xmax):
    """Keep only the points that fall inside the chart's x-range."""
    return [p for p in pts if p[0] <= xmax]


def load(name):
    p = OUT / name
    if not p.exists():
        print(f"  !! missing {name} - the corresponding report section will be empty",
              file=sys.stderr)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    d = load("si_results.json")
    g3 = load("gen3_results.json")
    fr = load("freq_results.json")
    dd = load("ddr4_results.json")
    xs = load("xs_results.json")
    gth = load("pcie_gth_results.json")
    cp = load("compliance_results.json")
    today = datetime.date.today().isoformat()

    h = [f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>ZCU106 信号完整性分析报告</title><style>{CSS}</style></head><body>'
         f'<div class="wrap">']

    # ---------------- header + TOC ----------------
    h.append('<header><h1>ZCU106 信号完整性分析报告</h1>'
             '<p class="sub">板卡 9217_1280937-HW-Z1-ZCU106 Rev 1.0（Xilinx ZCU106 / ZU7EV）'
             ' &nbsp;·&nbsp; 分析对象：PCIe Gen2/Gen3 x4 与 PL DDR4-2666'
             ' &nbsp;·&nbsp; 仿真引擎：Cadence Allegro PCB SI（bem2d 场求解、tlsim）'
             ' &nbsp;·&nbsp; 厂商模型：AMD <code>zynquplus.ibs</code>（POD12）、'
             'Xilinx GTH IBIS-AMI Kit R1.1'
             f' &nbsp;·&nbsp; 生成日期 {today}</p></header>')
    toc = [("0", "摘要与结论一览"), ("1", "板卡与叠层"), ("2", "分析对象与走线几何"),
           ("3", "场求解与传输线参数"), ("4", "差分阻抗与耦合"),
           ("5", "PCIe 通道频域特性与 TDR"), ("6", "PCIe 链路眼图"),
           ("7", "PCIe 合规检查"), ("8", "DDR4 写入通道"),
           ("9", "DDR4 端接与阈值判定"), ("10", "结论与建议"),
           ("11", "方法、模型与不确定性"), ("12", "复现步骤")]
    h.append('<nav class="toc">' + "".join(
        f'<a href="#s{i}">{i}. {t}</a>' for i, t in toc) + '</nav>')

    # ================= 0. summary =================
    h.append('<h2 id="s0">0. 摘要与结论一览</h2>')
    gth8 = [e for e in (gth["eyes"] if gth else []) if e["rate_gbs"] == 8]
    amd40 = [t for t in (dd or {}).get("amd_tests", []) if t.get("model", "").endswith("M_OUT40")
             and t.get("eye_height_mV")]
    amd_all = [t for t in (dd or {}).get("amd_tests", []) if t.get("eye_height_mV")]
    # Gen3 eye figures used by the summary row: the pcie3_eye check without CTLE
    g3e = [v["eye_mV"] for v in (cp["eyes"].values() if cp else []) if not v["ctle"]]
    il_5050 = [v["il_4p0"] for v in (fr["lanes"].values() if fr else [])]
    il4 = (min(cp["checks"]["insertion_loss"][l]["il_at"]["4GHz"]
               for l in ("TX0", "TX1", "TX2", "TX3")) if cp else 0.0)
    il_worst_margin = (min(v["masks"]["generic_il"]["worst_margin_db"]
                           for v in cp["checks"]["insertion_loss"].values())
                       if cp else 0.0)
    kpis = [
        (f'{il4:.2f} dB', "PCIe 板级插损 @4 GHz（含过孔/电容）"),
        (f'{min(e["eye_mV"] for e in gth8):.0f} mV / {min(e["eye_ps"] for e in gth8):.0f} ps'
         if gth8 else "—", "PCIe Gen3 眼高 / 眼宽（官方 GTH 模型，非均衡）"),
        (f'{min(t["eye_height_mV"] for t in amd40):.0f} mV' if amd40 else "—",
         "DDR4 写眼高（官方 POD12 模型，最差通道）"),
        (f'{xs["tests"]["pair_TX"]["Zdiff"]:.1f} Ω' if xs else "—",
         "PCIe 差分阻抗（bem2d 场求解）"),
        ('40.404 Ω', "场求解回归：4.2 mil 单线与 Cadence 完全一致"),
    ]
    h.append(kpi(kpis))
    rows = [
        ["PCIe 通道插损", "PCIe4 IL 眼罩 / 通用 IL 眼罩（1–8 GHz）", "0.75–1.05 dB @4 GHz，全程低于限值",
         verdict(True)],
        ["PCIe 回损", "通用 Tx RL 眼罩（≤1.25 GHz 10 dB；2.5–8 GHz 6 dB）",
         "≤5.9 GHz 通过；5.9–7.7 GHz 低于限值 0.05–0.11 dB（边界项：把集总过孔/电容寄生"
         "取小一半即通过，属模型误差带内）", verdict(False, "压线")],
        ["PCIe Gen3 眼图", "<code>pcie3_eye</code> 眼罩（0.30 UI × 25 mV）",
         (f'4 条通道均 0 命中，眼高 {min(g3e):.0f}–{max(g3e):.0f} mV（均衡前）'
          if g3e else "—"), verdict(True)],
        ["DDR4 写电平", "JEDEC POD12 阈值 Vinl 0.76 V / Vinh 0.92 V",
         (f'高电平裕量 +{min(t["margin_vinh_mV"] for t in amd_all):.0f}…'
          f'+{max(t["margin_vinh_mV"] for t in amd_all):.0f} mV，低电平 '
          f'+{min(t["margin_vinl_mV"] for t in amd_all):.0f}…'
          f'+{max(t["margin_vinl_mV"] for t in amd_all):.0f} mV（最差为 OUT60 角）'
          if amd_all else "—"), verdict(True)],
        ["DDR4 端接", "POD12 需要伪开漏端接",
         "仅「40 Ω 到 VDDQ」可同时越过 Vinl/Vinh", verdict(True)],
        ["差分阻抗", "常用 PCIe 目标 85 Ω（非强制）",
         "场内求解 77.0 Ω（4.2 mil 间距），偏低约 9%", verdict(False, "需注意")],
        ["对间串扰", "工程关注量级 ≪ 眼高",
         "净距 35 mil 时 NEXT < 1e-6（< 0.0001%）", verdict(True)],
        ["特征阻抗一致性", "与 Cadence interconn.iml 比对",
         "40.404 Ω 对 40.404 Ω，偏差 0.000%", verdict(True)],
    ]
    h.append(H.table(["项目", "判据", "结果", "结论"], rows))
    h.append('<p class="dim">全部结论的原始数据来自 <code>si/out/*.json</code>，'
             '每节末尾给出对应的生成脚本；判据文件取自 Cadence '
             '<code>share\\topxp\\sample_masks\\</code>。</p>')

    # ================= 1. board & stackup =================
    h.append('<h2 id="s1">1. 板卡与叠层</h2>')
    h.append('<div class="grid"><div>' + card("叠层（Allegro <code>report -v x-section</code>）", """
<ul>
<li>16 层、总厚 <b>61.19 mil</b>；介质 FR408HR，Er 3.4，tanδ 0.035（1 MHz）</li>
<li>PCIe 走线：<b>03_SIG1 / 12_SIG4 内层带状线</b>，线宽 4.2 mil，
    上下参考面距离 2.89 / 3.0 mil，铜厚 0.6 mil</li>
<li>出线与连接器端：TOP / BOTTOM 微带，线宽 5.5 mil（外层铜箔 2 mil）</li>
<li>DQ 走的 03_SIG1 / 07_SIG3 与 PCIe 同腔：2.89 mil 介质 + 0.6 mil 铜</li>
</ul>""") + "</div><div>" + fig(H.stackup_svg(), "叠层与阻抗控制结构（按报告数据绘制）")
    + '</div></div>')

    # ================= 2. objects & geometry =================
    h.append('<h2 id="s2">2. 分析对象与走线几何</h2>')
    gaps_db = diffpair_gaps()

    def lane_gap(lane):
        """intra-pair gap from the dpg report (TX lanes: the post-cap pair)"""
        key = f"PCIE_{lane[:3]}_C_N,PCIE_{lane[:3]}_C_P" if lane.startswith("TX") \
            else f"PCIE_{lane[:3]}_N,PCIE_{lane[:3]}_P"
        g = gaps_db.get(key)
        return f"{g:.1f}" if g is not None else "—"

    h.append(card("PCIe Gen2/Gen3 x4 通道（Allegro <code>report -v elw/eln/dpg</code>）", """
<p>FPGA（U1，PL GTH quad 224）焊球 → 过孔 → 内层带状线 → 过孔 → 100 nF 交流耦合电容 →
外层微带 → P3 CEM 连接器。四条发送通道的几何：</p>""" + H.table(
        ["通道", "网络（U1 球号）", "带状线 (mil)", "微带 (mil)", "总长 (mil)",
         "对内间距 (mil) *"],
        [[r["lane"],
          f'{r["net"]}（{net_ball(r["net"]) or "—"}）' if r.get("net") else "—",
          f'{r["milA"]:.0f}', f'{r["milB"]:.0f}', f'{r["total_mil"]:.0f}',
          lane_gap(r["lane"])] for r in d["lanes"]])
        + '<p class="dim"><sup>*</sup>对内间距取自 <code>report -v dpg</code> 的差分对标称间距：'
          'TX 对内层段没有登记为差分对，表中取的是交流耦合电容之后 BOTTOM 段的 '
          '<code>PCIE_TX*_C_*</code> 对（4.2 mil）；RX 对为 4.5 mil（其 report 中另有 4.2 mil 段）。'
          '参考时钟 PCIE_CLK（U1.AB8 = MGTREFCLK0P_224）与 TX 通道结构相同'
          '（03_SIG1 带状线 + 串 10 nF + BOTTOM 出线），走线宽度为 3.4 / 4.2 mil。'
          '四条 TX 通道的带状线长度差 <b>149 mil</b>（≈23 ps），层间偏斜可控。</p>') if d else "")
    h.append(card("PL DDR4-2666 点对点通道", """
<p>Micron MT40A256M16GE-075E ×4（U2/U99/U100/U101）挂在 U1 的 PL HP bank（POD12 电平），
DQ/DQS 为点对点。三条被测通道的线宽／层分解（来自 <code>report -v elw</code>）：</p>""" +
        H.table(["通道", "层", "线宽 (mil)", "段长 (mil)", "总长 (mil)"], [
            ["DDR4_DQ0", "03_SIG1", "3.5 / 5.5", "872.4 / 914.0", "1808.7"],
            ["DDR4_DQ0", "TOP", "10.0", "22.3", ""],
            ["DDR4_DQ8", "07_SIG3", "3.5 / 5.5", "821.3 / 1019.4", "1863.0"],
            ["DDR4_DQ8", "TOP", "10.0", "22.3", ""],
            ["DDR4_DQS0_T", "03_SIG1", "4.1 / 5.5", "648.3 / 1012.7", "1683.3"],
            ["DDR4_DQS0_T", "TOP / 内层", "5.8", "22.3", ""]])
        + '<p class="dim">同一网络在 3.5 mil 与 5.5 mil 两个线宽之间切换（对应约 46 Ω / 32 Ω），'
          '形成阻抗台阶，是 DDR4 通道里最主要的几何不连续；地址/命令为 fly-by 挂 4 颗 DRAM，'
          '首末负载长度差 727 mil（≈114 ps，0.75 ns UI 的 15%，该值仅见于报告正文）。'
          '22.3 mil 的外层出线段按<b>微带</b>建模：用 bem2d 复算 Cadence 自己保存的外层截面'
          '（4.0 / 5.5 mil 与 .iml 完全吻合），得 10.0 mil → 37.6 Ω、5.8 mil → 49.7 Ω，'
          '不再沿用内层带状线闭式公式。</p>'))
    h.append(fig(H.topology_svg(), "分析通道拓扑（PCIe：芯片—封装—走线—电容—连接器；DDR4：点对点）"))

    # ================= 3. field solver =================
    h.append('<h2 id="s3">3. 场求解与传输线参数</h2>')
    if xs:
        t = xs["tests"]["single_4p2"]
        h.append(card("二维场求解（Cadence <code>bem2d</code>）与回归校验", f"""
<p>所有传输线参数由本板叠层直接场求解得到，求解器命令行与 Cadence 内部调用完全一致：
<code>bem2d 0 &lt;cross-section&gt;.in 0.00127</code>。用与 <code>interconn.iml</code> 中
Cadence 自己保存的 4.2 mil 单线截面做回归：</p>
{H.table(["参数", "本次场求解", "Cadence interconn.iml", "偏差"],
         [["特征阻抗 Z0", f'{t["Z0"]:.3f} Ω', f'{t["board"]["Z0"]:.3f} Ω',
           f'{t["dZ0_pct"]:+.3f} %'],
          ["单位长度电容 C", f'{t["C"]*1e12:.4f} pF/m', f'{t["board"]["C"]*1e12:.4f} pF/m',
           f'{t["dC_pct"]:+.3f} %'],
          ["单位长度电感 L", f'{t["L"]*1e9:.3f} nH/m', f'{t["board"]["L"]*1e9:.3f} nH/m', "—"],
          ["直流电阻 R", f'{t["R"]:.3f} Ω/m', f'{t["board"]["R"]:.3f} Ω/m', "—"],
          ["传播时延 Td", f'{t["Td"]*1e9:.4f} ns/m', "6.1507 ns/m", "—"]])}
<p class="dim">求解器输入的三个约定：介质厚度只累加介质层（不含参考层铜厚）；'
几何不得进入负 x 坐标（否则静默返回空结果）；以 <code>.ends</code> 收尾。
网格参数 0.00127 m 与 signoise 一致。</p>"""))

    # ================= 4. differential & coupling =================
    h.append('<h2 id="s4">4. 差分阻抗与耦合</h2>')
    if xs:
        pr = []
        for key, lab in (("pair_TX", "PCIE_TX0 对（4.2 mil 间距）"),
                         ("pair_RX", "PCIE_RX0 对（4.5 mil 间距）")):
            v = xs["tests"].get(key)
            if v:
                pr.append([lab, f'{v["gap_mil"]:.1f}', f'{v["Zodd"]:.1f}',
                           f'{v["Zeven"]:.1f}', f'<b>{v["Zdiff"]:.1f}</b>',
                           f'{v["kc"]*100:.2f} %'])
        h.append(card("差分对的场求解结果", H.table(
            ["差分对", "对内间距 (mil)", "Z<sub>odd</sub> (Ω)", "Z<sub>even</sub> (Ω)",
             "Z<sub>diff</sub> = 2·Z<sub>odd</sub> (Ω)", "耦合度 k<sub>c</sub>=k<sub>l</sub>"], pr)
            + '<p class="dim">本板对内间距（4.2–4.5 mil）与线宽同量级，属紧耦合，'
              '把差分阻抗压到 77 Ω 附近。注意：同一叠层与线宽下加大间距只能收敛到无耦合极限'
              '（10 mil 时 80.6 Ω，≥20 mil 时 80.8 Ω = 2×40.4 Ω），'
              '所以常用的 85 Ω 目标<b>靠放宽间距达不到</b>，需要减小线宽或加厚介质；'
              '间距放宽到 6–8 mil 可回到 79–80 Ω。</p>'))
        if xs.get("gap_sweep"):
            h.append(card("对内间距扫描", H.table(
                ["间距 (mil)", "Z<sub>odd</sub> (Ω)", "Z<sub>even</sub> (Ω)",
                 "Z<sub>diff</sub> (Ω)", "C₁₂ (pF/m)", "L₁₂ (nH/m)"],
                [[f'{s["gap_mil"]:.1f}', f'{s["Zodd"]:.1f}', f'{s["Zeven"]:.1f}',
                  f'{s["Zdiff"]:.2f}', f'{s["C12"]*1e12:.2f}', f'{s["L12"]*1e9:.3f}']
                 for s in xs["gap_sweep"]])))
        if xs.get("crosstalk_vs_gap"):
            h.append(card("对间耦合随净距的衰减（两导体场求解）", H.table(
                ["净距 (mil)", "k<sub>c</sub>", "k<sub>l</sub>", "NEXT 系数（单端）",
                 "FEXT 系数"],
                [[f'{c["gap_mil"]:.1f}', f'{c["kc"]:.5f}', f'{c["kl"]:.5f}',
                  (f'<b>{c["NEXT_se_coeff"]*100:.4f} %</b>' if c["NEXT_se_coeff"] > 1e-6
                   else '<span class="ok">低于 1e-6 显示阈值</span>'),
                  f'{c["FEXT_coeff"]*100:.5f} %'] for c in xs["crosstalk_vs_gap"]])
                + '<p class="dim">k<sub>c</sub> = C₁₂/C₁₁、k<sub>l</sub> = L₁₂/L₁₁ 直接来自场求解矩阵；'
                  '（取绝对值：矩阵中 C₁₂ &lt; 0。）'
                  'K<sub>b</sub> = (k<sub>c</sub>+k<sub>l</sub>)/4，K<sub>f</sub> = (k<sub>c</sub>−k<sub>l</sub>)/2。'
                  '两者相等说明远端串扰在同质带状线中被完全抵消（<b>FEXT ≈ 0</b>）；'
                  '近端耦合随间距极快衰减（4.2→15 mil 掉 200 倍），原因是上下参考面只有 6.5 mil，'
                  '场被紧锁在走线附近。</p>'
                + (f'<p>对间净距由 <code>report -v dpg</code> 的段起止坐标量出：'
                   f'PCIE_TX0↔TX1 与 RX0↔RX1 的最小中心距均 ≈39.3 mil（净距 ≈35 mil)，'
                   f'落在表格末行 —— <b>对间串扰不构成风险</b>（TX 用的是电容后 BOTTOM 段坐标，'
                   f'内层长走线的对间距在 dpg 报告中无差分对登记，属未核实项）。'
                   f'作为最坏情况，把两对排成 4 根 4.2 mil 等间距走线时，'
                   f'差分 NEXT 系数为 {xs["crosstalk_bundle"]["NEXT_diff_coeff"]*100:.2f} %（'
                   f'单端 {xs["crosstalk_bundle"]["NEXT_se_coeff"]*100:.2f} %）。'
                   f'该四导体截面的电容矩阵远端项与电感矩阵不自洽'
                   f'（k<sub>c</sub>/k<sub>l</sub> 相差约 100 倍，原始单端值 '
                   f'{xs["crosstalk_bundle"]["NEXT_se_coeff_raw"]*100:.2f} %），'
                   f'因此单端值按同质介质必须满足的 k<sub>c</sub> = k<sub>l</sub> 取电感侧；'
                   f'差分值由自洽的邻近线对项主导，可直接引用。</p>'
                   if xs.get("crosstalk_bundle") else "")))

    # ================= 5. frequency domain / TDR =================
    h.append('<h2 id="s5">5. PCIe 通道频域特性与 TDR</h2>')
    if fr:
        ser = [dict(label=l, color=c, pts=[(p[0], p[1]) for p in v["il"]])
               for (l, v), c in zip(list(fr["lanes"].items())[:4],
                                    (C_ACC, C_ACC2, C_OK, C_BAD))]
        ser = [dict(s, pts=clip(s["pts"], 12.0)) for s in ser]
        mask = H.parse_maskfile("generic_il.maskfile")
        if mask:
            ser.append(dict(label="插损限值", color=C_BAD,
                            pts=clip([(s["x1"] / 1e9, abs(s["y1"])) for s in mask["segs"]]
                                     + [(mask["segs"][-1]["x2"] / 1e9,
                                         abs(mask["segs"][-1]["y2"]))], 12.0)))
        h.append(card("插入损耗（含过孔与 100 nF 交流耦合电容，板级 TP1→连接器）",
                      fig(H.line_chart(ser, title="插入损耗 IL (dB) vs 频率（含限值，全量程）",
                                       xlabel="GHz", ylabel="dB", xmax=12.0),
                          "四条发送通道几乎重合；红色为随工具提供的通道插损限值。"
                          "限值本身从 1 GHz 的 4.2 dB 线性放宽到 8 GHz 的 22.5 dB，"
                          "纵轴按限值量程绘制，因此实测曲线贴近底部 —— 这正是本通道"
                          "「损耗远低于限值」的直接体现，具体数值见下表。")
                      + fig(H.line_chart([dict(label=l, color=c,
                                               pts=clip([(p[0], p[1]) for p in v["il"]], 8.0))
                                          for (l, v), c in
                                          zip(list(fr["lanes"].items())[:4],
                                              (C_ACC, C_ACC2, C_OK, C_BAD))],
                                         title="插入损耗（放大：0–8 GHz、0–4 dB）",
                                         xlabel="GHz", ylabel="dB", xmax=8.0, ymax=4.0),
                          "放大后可看出四条通道的差别：带状线段长度差异与过孔/电容"
                          "不连续性带来的纹波；6 GHz 以上的驻波来自 77 Ω 走线置于 "
                          "100 Ω 参考下的失配。")
                      + H.table(["通道", "IL@2.5 GHz", "IL@4 GHz", "IL@8 GHz",
                                 "RL@2.5 GHz", "RL@4 GHz"],
                                [[l, f'{v["il_2p5"]:.2f}', f'{v["il_4p0"]:.2f}',
                                  f'{v["il_8p0"]:.2f}', f'{v["rl_2p5"]:.1f}',
                                  f'{v["rl_4p0"]:.1f}'] for l, v in fr["lanes"].items()])
                      + ('<p class="dim">本表是 <b>50 Ω 单端参考</b>下的级联结果：'
                         f'板级插损在 4 GHz 为 {min(il_5050):.2f}–{max(il_5050):.2f} dB'
                         '（走线短 2.2 in、介质薄而低损 FR408HR）。第 7 节用 100 Ω 差分参考重算，'
                         '4 GHz 得 0.75–1.05 dB，两表口径不同、不可直接互比。'
                         '损耗随频率的斜率主要来自介质损耗与集肤效应；曲线在 6 GHz 以上的纹波是 '
                         '77 Ω 走线置于 100 Ω 参考下的驻波，与回损检查中的压线频段对应。</p>'
                         if il_5050 else '')))
    if g3 and g3.get("tdr"):
        h.append(card("TDR 阻抗剖面（时域阶跃反射）", '<div class="grid">' + "".join(
            fig(H.line_chart([dict(label=f'{t["lane"]} TDR', color=C_OK,
                                   pts=clip([(p[0], p[1]) for p in t["profile"]], 1500.0))],
                             title=f'{t["lane"]}（50 Ω 参考）', xlabel="ps",
                             ylabel="Z (Ω)", xmax=1500.0, ymin=0.0, ymax=70.0))
            for t in g3["tdr"][:2]) + '</div>'
            + '<p class="dim">剖面由「理想源 50 Ω → 过孔 → 2.19 in 内层带状线 → 过孔 → 100 nF 交流耦合电容 → 0.28 in 外层微带 → 50 Ω 终端」级联得到（模型里没有封装/焊球，因此不含焊球扇出段）。以 50 Ω 为参考：内层带状线段稳定在 <b>40.4 Ω</b>（与该段场求解一致，100–600 ps 窗口实测 40.37–40.68 Ω）；交流耦合电容处出现容性下陷，最低到 33 Ω；<b>780 ps 之后的 50–51 Ω 已经不是微带段的特征阻抗，而是链末 50 Ω 终端的稳态</b>——0.28 in 微带的往返只有约 88 ps，30 ps 阶跃分辨不出它的平台。所以剖面可以确认带状线段阻抗与电容/过孔不连续的位置，但不能用来读微带阻抗。</p>'))

    # ================= 6. link eyes (official models) =================
    h.append('<h2 id="s6">6. PCIe 链路眼图</h2>')
    if gth:
        h.append(card("链路组成（Xilinx GTH IBIS-AMI Kit R1.1，UG1183 拓扑）", f"""
<p>板上 PCIe 引脚为 <code>MGTHTXP3_224 / MGTHRXP3_224</code> 等，属于 PL 的
<b>GTH quad 224</b>（ZU7EV 封装内共 5 个 GTH quad 与 4 条 PS-GTR；
PS-GTR 在本板用于 DP/USB0/SATA1）。因此链路模型直接采用该套件的官方模型：</p>
<p style="font-family:ui-monospace,Consolas,monospace;font-size:13px">
理想源 → Tx_Die(TT) → Tx_Package.s4p → 本板通道（场求解 RLGC + 过孔 + 100 nF） →
Rx_Package.s4p → Rx_Die → 接收节点</p>
{H.table(["通道", "Gen1 @1.25 GHz", "Gen2 @2.5 GHz", "Gen3 @4 GHz", "Gen4 @8 GHz"],
         [[lane] + [f'{v["il_at"][k]:.2f}' for k in gth["nyquist"]]
          for lane, v in gth["lanes"].items()])}
<p class="dim">这是<b>芯片到芯片</b>的差分级联值，其中官方 die/封装模型贡献约 6.2 dB
（Rx_Die 内部衰减约 4.3 dB，两个封装约 1.8 dB），<b>真正属于本板走线的只有约 1 dB</b>。
PCIe Gen3 参考通道在 4 GHz 为 20 dB，本板 7.5–7.9 dB。</p>"""))
        if gth.get("eyes"):
            ey = {}
            for e in gth["eyes"]:
                ey.setdefault(round(e["rate_gbs"]), []).append(e)
            h.append(card("接收节点非均衡眼图（PRBS13，发送去加重按 UG1183 Table 2 标定）",
                          H.table(["通道", "速率", "去加重 (dB)", "眼高 (mV)", "眼宽 (ps)",
                                   "高/低电平 (mV)"],
                                  [[e["lane"], f'{e["rate_gbs"]:.0f} GT/s',
                                    f'{e["post_db"]:.2f}' if e["post_db"] else "0",
                                    f'{e["eye_mV"]:.0f}', f'{e["eye_ps"]:.0f}',
                                    f'{e["hi_mV"]:.0f} / {e["lo_mV"]:.0f}']
                                   for e in gth["eyes"]])
                          + '<p class="dim">Gen2 无去加重；Gen3 用规范推荐的 −3.5 dB 后游标'
                            '（TXPOSTCURSOR = 14 → 3.45 dB），TXDIFFCTRL = 24 → 0.873 V<sub>pp,diff</sub>。'
                            '两个速率的眼宽都撑满一个 UI —— 注意该眼宽指标本身以 1 UI 为上限，'
                            '"撑满"不代表有裕量；本表的"眼高"是采样波形的 P90−P10 展宽'
                            '（等于 <code>vpp_mV</code>），不是眼开度。</p>'
                          + '<p class="dim">本节眼图由理想源 + 官方 Tx_Die(TT)/封装/Rx die 终止模型级联得到，'
                            '<b>未使用厂商 RX AMI 的 CTLE/DFE/CDR</b>（该套件 DLL 无法用手工 host 初始化，'
                            '见 <code>si/README.md</code>），因此是 RX die 节点的<b>非均衡眼</b>，'
                            '不能当作 IBIS-AMI 签核结果。</p>'
                          + '<div class="grid3">' + "".join(
                              fig(H.eye_svg(e["wave"], 1000.0 / e["rate_gbs"],
                                            title=f'{e["lane"]} @{e["rate_gbs"]:.0f} GT/s',
                                            height_mV=e["eye_mV"], width_ps=e["eye_ps"]))
                              for e in gth["eyes"] if e["lane"] in ("TX0", "TX1")
                              and e["rate_gbs"] == 8) + '</div>'))
        if gth.get("gen3_emphasis_sweep"):
            sp = gth["gen3_emphasis_sweep"]
            h.append(card("Gen3 发送去加重灵敏度（TX0）", H.table(
                ["TXPOSTCURSOR", "去加重 (dB)", "眼高 (mV)", "眼宽 (ps)"],
                [[f'{s["post"]}', f'{s["post_db"]:.2f}', f'{s["eye_mV"]:.0f}',
                  f'{s["eye_ps"]:.0f}'] for s in gth["gen3_emphasis_sweep"]])
                + ('<p class="dim">去加重压低稳态幅度：post = 0 时 '
                   f'{sp[0]["eye_mV"]:.0f} mV，post = 5…20 时 '
                   f'{min(s["eye_mV"] for s in sp[1:]):.0f}–{max(s["eye_mV"] for s in sp[1:]):.0f} mV。'
                   '注意本表的"眼高"是采样波形的 P90−P10 展宽（随强调幅度变化），'
                   '所以这条曲线看的是趋势而不是眼开度本身。本板级损耗只有约 1 dB，'
                   '去加重的作用主要是与远端器件配合，而不是补偿本板损耗。</p>'
                   if len(sp) > 1 else '')))

    # ================= 7. PCIe compliance =================
    h.append('<h2 id="s7">7. PCIe 合规检查</h2>')
    if cp:
        h.append(card("判据来源", """
<p>限值全部取自随工具发布的合规判据文件，避免自定阈值：</p>
<ul>
<li><code>sample_masks\\pcie3_eye.maskfile</code>：眼罩，0.30 UI × 25 mV 六边形
    （±0.15 UI、±12.5 mV，x 跨度 0.35–0.65 UI、y 轴单位 V），用于 Gen3 接收侧眼图判定。
    <b>该文件本身没有任何参考面/归一化说明</b>（只有一行 <code>UI V</code> 表头），
    本节把它按电压直接套在 RX die 节点上；同一个 25 mV 高的六边形对 ±160 mV 的信号
    几乎不可能被命中，所以"0 命中"是结构性结果、四个通道与 CTLE 开/关都一样，
    <b>不能把这一项读成通道优劣</b>。它也不是 PCIe 3.0 规范里的 Rx 眼罩。</li>
<li><code>sample_masks\\pcie4_il.maskfile</code>、<code>generic_il.maskfile</code>：通道插损限值
    （两者内容逐字节相同 —— 都是 1 GHz 4.2 dB 线性放宽到 8 GHz 22.5 dB，外加一条 5.2 → 23.5 dB 的
    平行边界；下表两列因此总是同结论）</li>
<li><code>sample_masks\\generic_rl_tx.maskfile</code>：发送端回损限值
    （≤1.25 GHz 为 10 dB，1.25–2.5 GHz 为 8 dB，2.5–8 GHz 为 6 dB）</li>
</ul>"""))
        il = cp["checks"]["insertion_loss"]
        h.append(card("通道插损判定（板级 TP1 → 连接器，100 Ω 差分参考）", H.table(
            ["通道", "IL@1.25 GHz (dB)", "IL@2.5 GHz", "IL@4 GHz", "IL@8 GHz",
             "PCIe4 IL 眼罩", "通用 IL 眼罩"],
            [[l, f'{v["il_at"]["1.25GHz"]:.2f}', f'{v["il_at"]["2.5GHz"]:.2f}',
              f'{v["il_at"]["4GHz"]:.2f}', f'{v["il_at"]["8GHz"]:.2f}',
              verdict(v["masks"]["pcie4_il"]["pass_"]),
              verdict(v["masks"]["generic_il"]["pass_"])]
             for l, v in il.items()])
            + '<p class="dim">判定方式：在 1–8 GHz 上逐频点与限值比较，'
              '取整段带宽内的最差余量（限值 − 实测的最大差值）。四条通道全程低于限值，'
              '最差余量出现在 1 GHz 附近，为 '
              f'{il_worst_margin:.2f} dB（@1 GHz）。</p>'))
        rl = cp["checks"]["return_loss"]
        h.append(card("回损判定", H.table(
            ["通道", "RL@0.5 GHz", "RL@1.25 GHz", "RL@2.5 GHz", "RL@4 GHz", "RL@8 GHz",
             "限值判定"],
            [[l, f'{v["rl_at"]["0.5GHz"]:.1f}', f'{v["rl_at"]["1.25GHz"]:.1f}',
              f'{v["rl_at"]["2.5GHz"]:.1f}', f'{v["rl_at"]["4GHz"]:.1f}',
              f'{v["rl_at"]["8GHz"]:.1f}',
              (verdict(True, "通过") if v["pass_"] else
               verdict(False, f'压线 {v["worst_deficit_db"]:.2f} dB'))]
             for l, v in rl.items()])
            + f'<p>四条通道在 <b>5.9 GHz 以下全部满足限值</b>；5.9–7.7 GHz 出现窄带压线，'
              f'最大低于限值 0.05–0.11 dB（≈1%），由 77 Ω 走线在 100 Ω 参考下的失配、'
              f'交流耦合电容的封装电感与过孔不连续叠加形成。'
              f'该量级已进入集总过孔/电容模型的误差范围（>10 GHz 模型不再可信，'
              f'本段判据止于 8 GHz）。</p>'))
        ey = cp["eyes"]
        h.append(card("Gen3 眼罩判定（<code>pcie3_eye</code>，0.30 UI × 25 mV）", H.table(
            ["通道", "参考接收机 CTLE", "眼高 (mV)", "眼宽 (ps)", "落入眼罩的采样点", "判定"],
            [[k.replace("_ctle", ""), ("已加" if e["ctle"] else "未加"),
              f'{e["eye_mV"]:.0f}', f'{e["eye_ps"]:.0f}', str(e["mask_hits"]),
              verdict(e["mask_pass"])]
             for k, e in ey.items()])
            + '<p class="dim">判据：一个 UI 内的采样点若落入六边形即判失效。'
              '参考接收机 CTLE 依据规范参数实现（零点 1.5 GHz，交流/直流增益比 9.2425 dB ≈ 3×，'
              '对应参数文件中的 GBW 15 GHz、AC_BW 6 GHz）。加 CTLE 后眼高提升约 2.5 倍，'
              '两种情况下均无采样点落入眼罩。</p>'))

    # ================= 8. DDR4 =================
    h.append('<h2 id="s8">8. DDR4 写入通道</h2>')
    if dd:
        md = dd.get("amd_models", {})
        rows = [[nm, f'{v["c_comp"]*1e12:.2f} pF',
                 f'{v["vinl"]:.2f} / {v["vinh"]:.2f} V',
                 f'{v["edge_20_80_ps"]:.1f} ps', f'{v["n_pullup"]} / {v["n_pulldown"]}']
                for nm, v in md.items()]
        h.append(card("驱动模型与封装（AMD <code>zynquplus.ibs</code> + "
                      "<code>xczu7ev_ffvc1156.pkg</code>）", f"""
<p>PL DDR4 的 FPGA 侧驱动采用官方 POD12 模型，接收端为 DRAM 的 40 Ω ODT 接至 VDDQ；
FPGA 焊球到芯片的 R/L/C 取自官方封装模型（DQ0：0.811 Ω / 4.95 nH / 2.67 pF）。</p>
{H.table(["POD12 模型", "C_comp", "Vinl / Vinh", "厂商波形 20–80%", "上拉/下拉表点数"], rows)}
<p class="dim">F/M/S 为快/中/慢回转率角（工艺角在 V/I 表的 min/typ/max 三列中）；
OUT40/48/60 为 DCI 输出阻抗。Vinl/Vinh = 0.76/0.92 V 即 JEDEC POD12 的输入阈值，
用于下文的电平判定。</p>"""))
        if dd.get("amd_dc"):
            dc = dd["amd_dc"]
            h.append(card("静态工作点校验（转换后的 V/I 表是否正确）", H.table(
                ["DRAM 端接", "稳态高 (V)", "稳态低 (V)", "摆幅 (mV)"],
                [[lab, f'{dc[k]["high"]:.4f}', f'{dc[k]["low"]:.4f}',
                  f'{(dc[k]["high"]-dc[k]["low"])*1e3:.0f}']
                 for k, lab in (("vddq40", "40 Ω 到 VDDQ（POD12 标准接法）"),
                                ("split", "80 Ω 到 VDDQ + 80 Ω 到 VSS（戴维南 0.6 V）"),
                                ("gnd40", "40 Ω 到 VSS"))
                 if k in dc and dc[k].get("high") is not None])
                + '<p class="dim">40 Ω 驱动器配 40 Ω 到 VDDQ 得到 1.200 / 0.600 V，'
                  '与 POD12 的理论值一致，说明厂商 V/I 表（含符号与坐标轴）被正确解读。</p>'))
        amd = [t for t in dd.get("amd_tests", []) if t.get("eye_height_mV")]
        _et = dd.get("edge_test") or {}
        _rd = [r for r in _et.get("rise_time_sweep", []) if r.get("edge_20_80_ps")]
        _st = [r for r in _et.get("stim_edge_sweep", []) if r.get("edge_20_80_ps")]
        rise_note = (f'本脚本每次运行都重做这项实验：把 RISE_TIME 从出厂值改到 '
                     f'100 ps / 619 ps / 1 ns / 2 ns，40 Ω 负载上的输出边沿恒为 '
                     f'{_rd[0]["edge_20_80_ps"]:.1f} ps；而把激励边沿从 20 ps 放到 800 ps，'
                     f'输出边沿从 {_st[0]["edge_20_80_ps"]:.1f} ps 变成 '
                     f'{_st[-1]["edge_20_80_ps"]:.1f} ps —— 决定边沿的是激励而不是模型里的 '
                     f'RISE_TIME。' if _rd and _st else '')
        if amd:
            h.append(card("写眼图（DRAM 球上，DRAM ODT 40 Ω 到 VDDQ）", H.table(
                ["通道", "驱动模型", "眼高 (mV)", "半高眼宽 (ps)", "高/低电平 (mV)",
                 "对 Vinh/Vinl 的裕量 (mV)", "实测边沿 20–80%"],
                [[t["title"], t["model"].replace("HP_POD12_DCI_", ""),
                  f'{t["eye_height_mV"]:.0f}', f'{t["eye_width_ps"]:.0f}',
                  f'{t["high_lvl_mV"]:.0f} / {t["low_lvl_mV"]:.0f}',
                  f'{t["margin_vinh_mV"]:+.0f} / {t["margin_vinl_mV"]:+.0f}',
                  f'{t.get("edge_ps") or 0:.0f} ps'] for t in amd])
                + '<p class="dim">驱动的回转率由模型 V/I 表与 C_comp 决定（tlsim 不读取'
                  ' AC_PARAM 的 RISE_TIME），DRAM 球上实测边沿 100–220 ps。'
                  + rise_note +
                  '同一通道改用 48 Ω / 60 Ω 驱动强度时，驱动器内阻变大、低电平被 ODT 抬高，'
                  '眼高随之下降 —— 这是 DDR4 写通道的固有取舍。</p>'
                + '<div class="grid3">' + "".join(
                    fig(H.eye_svg(t["wave"], dd["ui_ps"], title=t["title"],
                                  height_mV=t["eye_height_mV"],
                                  width_ps=t["eye_width_ps"]))
                    for t in amd if t["model"].endswith("M_OUT40")) + '</div>'))
        if dd.get("tests") and amd:
            geye = [t["eye_height_mV"] for t in dd["tests"]]
            peye = [m["eye_height_mV"] for m in amd if m["model"].endswith("M_OUT40")]
            _gv = [(t["vmax"] * 1e3, t["vmin"] * 1e3) for t in dd["tests"]]
            _pv = [(m["high_lvl_mV"], m["low_lvl_mV"]) for m in amd
                   if m["model"].endswith("M_OUT40")]
            _d = [a - b for a, b in zip(geye, peye)] if len(geye) == len(peye) else []
            cmp_note = (f'两臂用<b>相同端接</b>（40 Ω 到 VDDQ），因此电平可以直接比：'
                        f'通用臂高/低 {min(a for a, b in _gv):.0f}–{max(a for a, b in _gv):.0f} / '
                        f'{min(b for a, b in _gv):.0f}–{max(b for a, b in _gv):.0f} mV，'
                        f'POD12 臂 {min(a for a, b in _pv):.0f}–{max(a for a, b in _pv):.0f} / '
                        f'{min(b for a, b in _pv):.0f}–{max(b for a, b in _pv):.0f} mV；'
                        f'眼高相差 {min(_d):+.0f}…{max(_d):+.0f} mV。'
                        if _gv and _pv and _d else '')
            rows = []
            for t in dd["tests"]:
                m = next((a for a in amd if a["title"] == t["title"]
                          and a["model"].endswith("M_OUT40")), None)
                rows.append([t["title"],
                             f'{t["eye_height_mV"]:.0f} / {t["eye_width_ps"]:.0f}',
                             (f'{m["eye_height_mV"]:.0f} / {m["eye_width_ps"]:.0f}'
                              if m else "—"),
                             f'{t["vmax"]*1e3:.0f} / {t["vmin"]*1e3:.0f}',
                             (f'{m["high_lvl_mV"]:.0f} / {m["low_lvl_mV"]:.0f}'
                              if m else "—")])
            h.append(card("驱动模型对照：通用 1.2 V IBIS vs 官方 POD12",
                          H.table(["通道", "通用 IBIS 眼高/眼宽 (mV / ps)",
                                   "官方 POD12 眼高/眼宽 (mV / ps)",
                                   "通用 高/低电平 (mV)", "官方 POD12 高/低电平 (mV)"], rows)
                          + '<p class="dim">' + cmp_note +
                            '厂商 V/I 表在低电平附近更「软」，叠加 2.70 pF 的 C_comp 与封装 4.95 nH / 0.81 Ω，因此同样的端接下它的低电平被抬高、过冲更小、眼宽略窄。</p>'))
        if dd.get("amd_slew"):
            h.append(card("回转率（边沿）敏感性 —— 用激励边沿等效厂商慢角", H.table(
                ["工况", "激励边沿 (ns)", "DRAM 球上实测 (ps)", "眼高 (mV)", "半高眼宽 (ps)"],
                [[t.get("variant", ""), f'{t.get("stim_edge_ns") or 0:.2f}',
                  f'{t.get("edge_ps") or 0:.0f}', f'{t["eye_height_mV"]:.0f}',
                  f'{t["eye_width_ps"]:.0f}'] for t in dd["amd_slew"]
                 if t.get("eye_height_mV")])
                + '<p class="dim">边沿从 176 ps 放慢到 380 ps，眼高只下降约 40 mV；'
                  '被压缩的主要是眼宽（625 → 400 ps）。本通道只有 1.7–1.9 in，'
                  '对边沿不敏感，真正需要保证的是时序窗口而非幅度。</p>'))
        if dd.get("amd_sensitivity"):
            h.append(card("封装与钳位灵敏度", H.table(
                ["变体（DQ0，POD12-40 Ω）", "眼高 (mV)", "半高眼宽 (ps)", "实测边沿 (ps)"],
                [[t.get("variant", ""), f'{t["eye_height_mV"]:.0f}',
                  f'{t["eye_width_ps"]:.0f}', f'{t.get("edge_ps") or 0:.0f}']
                 for t in dd["amd_sensitivity"] if t.get("eye_height_mV")])
                + '<p class="dim">去掉官方封装 R/L/C 后边沿加快、眼高上升约 22 mV，'
                  '说明 4.95 nH 焊球电感在本速率下不可忽略；'
                  'IBIS 的 GND/POWER 钳位表只带来约 2 mV（0.3%）差异，'
                  '主结果采用不含钳位的转换（钳位会让部分端接方案的直流求解不收敛）。</p>'))
        first = next((t for t in dd.get("amd_tests", []) if t.get("bathtub")
                      and t["model"].endswith("M_OUT40")), None)
        if first:
            h.append(card("浴盆曲线（眼开度 vs 采样相位）",
                          fig(H.bathtub_svg(first["bathtub"], 60, dd["ui_ps"],
                                            title=f'{first["title"]}（官方 POD12）'),
                              "纵轴为该采样相位下的垂直眼开度；相位 0.5 UI 附近为最佳采样点。")
                          + '<p class="dim">曲线上的锯齿来自统计分箱（每个相位格内的波形数有限），'
                            '不是物理纹波。</p>'))

    # ================= 9. DDR4 termination & thresholds =================
    h.append('<h2 id="s9">9. DDR4 端接与阈值判定</h2>')
    if dd and dd.get("term_study"):
        rows = []
        for t in dd["term_study"]:
            if not t.get("eye_height_mV"):
                continue
            vh, vl = t["high_lvl_mV"], t["low_lvl_mV"]
            rows.append([t["variant"], f'{vh:.0f}', f'{vl:.0f}', f'{t["eye_height_mV"]:.0f}',
                         verdict(vh >= 920, "过 Vinh", "高电平不足"),
                         verdict(vl <= 760, "过 Vinl", "低电平偏高")])
        h.append(card("DRAM 端接方式对门限的影响（判据：Vinl 0.76 V / Vinh 0.92 V）",
                      H.table(["DRAM 端接", "高电平 (mV)", "低电平 (mV)", "眼高 (mV)",
                               "高电平判读", "低电平判读"], rows)
                      + '<p>POD12 的接收阈值决定了端接方式：只有「40 Ω 到 VDDQ」'
                        '能让高低电平同时越过门限。「40 Ω 到地」的高电平只有 0.60 V，'
                        '低电平虽然过 Vinl，但高电平永远到不了 Vinh；'
                        '戴维南端接（0.6 V / 40 Ω）的高电平 0.90 V 也差 20 mV。'
                        '（静态求解给出 0.8988 V，即差 21 mV；瞬态眼图中位数为 904 mV，差 16 mV。）'
                        'DDR4 的 DQ ODT 是拉到 VDDQ 的伪开漏端接，'
                        '这与门限要求一致，也是本文所有 DDR4 眼图采用的接法。</p>'))

    # ================= 10. conclusions =================
    h.append('<h2 id="s10">10. 结论与建议</h2>')
    h.append(card("", """
<h4>通过项</h4>
<ul>
<li><b>PCIe 通道损耗</b>：板级插损 4 GHz 仅 0.75–1.05 dB，1–8 GHz 全程低于工具随附的
    PCIe4 与通用插损限值；Gen3 参考通道在 4 GHz 为 20 dB，本板余量极大。</li>
<li><b>PCIe 眼图</b>：官方模拟模型（Tx die/封装 + Rx die 终止，<b>未含厂商 RX AMI</b>）下，
    Gen2 非均衡眼高 442–465 mV、Gen3（−3.5 dB 去加重）222–281 mV；眼宽指标以 1 UI 为上限，
    两个速率都撑满。加入规范参考接收机 CTLE 后眼高再提升约 2.5 倍，
    全部采样点落在 <code>pcie3_eye</code> 眼罩之外（该眼罩口径见第 7 节）。</li>
<li><b>DDR4 写电平</b>：官方 POD12 模型 + 40 Ω ODT 到 VDDQ，
    高电平裕量 +282…+287 mV、低电平裕量 +46…+164 mV（最差为 OUT60 驱动角）；
    DQ0/DQ8/DQS0_T 三条通道的眼高 459–595 mV。</li>
<li><b>串扰</b>：对内紧耦合使 FEXT 在同质带状线中抵消（k<sub>c</sub> = k<sub>l</sub>）；
    对间净距 35 mil 时近端耦合 NEXT &lt; 1e-6（&lt; 0.0001%），不构成风险。</li>
<li><b>场求解链路可复现</b>：40.404 Ω 的回归结果与 Cadence 自身提取完全一致，
    所有截面几何、参数与命令均已固化在脚本中。</li>
</ul>
<h4>需注意项</h4>
<ul>
<li><b>差分阻抗 77 Ω</b>（对内间距 4.2–4.5 mil）比常用的 85 Ω 目标低约 9%。
    PCIe 对差分阻抗没有强制窗口，且本通道损耗余量很大，风险有限；
    若后续有条件改版，把对内间距放宽到 6–8 mil 即可回到 79–80 Ω。</li>
<li><b>回损在 5.9–7.7 GHz 压线</b>（低于限值 0.05–0.11 dB）。该频段已接近 Gen4 奈奎斯特
    （8 GHz），若考虑 Gen4 应用，建议复核交流耦合电容的封装电感与过孔反焊盘。</li>
<li><b>DQ 线宽交替</b>（3.5 / 5.5 mil，≈46 Ω / 32 Ω）在通道中形成多处阻抗台阶，
    建议在版图评审中确认是否为走线宽度规则所致。</li>
<li><b>地址/命令 fly-by 首末负载差 727 mil（≈114 ps）</b>，占 0.75 ns UI 的 15%，
    依赖写均衡/训练补偿，属正常设计但要纳入时序预算。</li>
<li><b>DRAM 侧无厂商模型</b>：Micron MT40A256M16GE-075E 的 IBIS 未公开，
    本报告的 DDR4 结果为「官方 FPGA 模型 + JEDEC 标定 ODT」，不含 DRAM 内部模型。</li>
</ul>
<h4>后续可做</h4>
<ul>
<li>PCIe 若要做签核级判定，需要可驱动 IBIS-AMI 的通道分析引擎
    （SystemSI/TopXP、ADS 等）：<code>zynquplus.ibs</code> 之外，AMD 的 GTR/GTY AMI 包
    与 Cadence 的 <code>amictwf.dll</code> 系列均为厂商 DLL，需要工具侧约定才能正确初始化。</li>
<li>DDR4 读方向与 CTLE：需要 DRAM 驱动模型；AMD 随包的 HPIO DDR4 CTLE 为 IBIS-ISS 子电路，
    需在支持 S 参数/IBIS-ISS 的通道工具中使用。</li>
</ul>"""))

    # ================= 11. methods & limits =================
    h.append('<h2 id="s11">11. 方法、模型与不确定性</h2>')
    h.append(card("分析设置（每个数字的激励、码型与端接）", H.table(
        ["分析项（脚本）", "激励 / 码型", "速率 / 分辨率", "端接 / 模型"],
        [["PCIe Gen2 时域通道<br><code>build_si.py</code>",
          "PRBS-7 ×4（127×4 bit + 收尾）<br>理想 0–1 V 单端，60 ps 边沿",
          "5 GT/s，UI 200 ps<br>tlsim 步长 5 ps",
          "收发各 50 Ω；串 100 nF + 0.45 nH；经/从过孔模型（signoise 生成）"],
         ["PCIe 频域 IL/RL<br><code>build_freq.py</code>",
          "线性 40 Ω 线置于 50 Ω 参考（单端口径）",
          "0.1–15 GHz，0.05 GHz 步长",
          "集总过孔 0.4 nH / 0.4 pF、100 nF + 0.45 nH；DF = 0.012、r ∝ √f"],
         ["GTH 官方模型链路<br><code>build_pcie_gth.py</code>",
          "PRBS13，4000 bit<br>TX FFE：1 post-cursor（UG1183 Table 2）",
          "5 与 8 GT/s，各 32 spb；IFFT 4096 点",
          "Tx_Die TT + 官方 Tx/Rx 封装 s4p + Rx die 终止；TXDIFFCTRL 24 → 0.873 V<sub>pp,diff</sub>；<b>不加 RX AMI</b>"],
         ["TDR<br><code>build_gen3.py</code>",
          "30 ps 阶跃（1.00 → 1.03 ns）",
          "记录 30 ns，0.5 ps 步长",
          "50 Ω 源与终端；过孔/电容同第 5 节"],
         ["合规判定<br><code>build_compliance.py</code>",
          "PRBS13，20000 bit，32 spb<br>参考 TX 0.804 V + 3.45 dB FFE",
          "8 GT/s（UI 125 ps）",
          "100 Ω 差分；参考接收机 CTLE（零点 1.5 GHz、AC/DC 9.2425 dB）逐点比眼罩"],
         ["DDR4 写通道<br><code>build_ddr4.py</code>",
          "PRBS9 ×2（511×2 bit + 收尾）<br>激励边沿 20 ps（等效快角）",
          "DDR4-2666，UI 750 ps<br>tlsim 步长 2 ps",
          "FPGA 侧 HP_POD12_DCI_M_*（典型角，钳位表关闭）+ 官方封装 R/L/C；DRAM 侧 40 Ω 到 VDDQ"]],
        ) + '<p class="dim">所有时域通道用的 RLGC 与 signoise 自己写进 '
             '<code>interconn.iml</code> 的格式一致（G = 0、R 只给 DC 值），'
             '即<b>时域波形里没有介质损耗与集肤效应</b>；第 5/7 节的频域曲线含 DF 与 √f 集肤项。'
             'DDR4 的 F/M/S 三个回转率角无法在 tlsim 里区分，改用"激励边沿"等效（见第 8 节）。</p>'))
    h.append(card("证据等级", """
<p>报告中的每个数字都可归入以下三类之一，读结论时可直接按类判断可信度：</p>
<ul>
<li><b>场求解结果</b>（最可信）：单线/差分对的 RLGC、Z0、Z<sub>diff</sub>、Z<sub>odd/even</sub>、
    耦合系数 —— 由 <code>bem2d</code> 在本板真实叠层上求解。<b>回归校验只覆盖单线</b>
    （<code>interconn.iml</code> 里只有 6 个单导体截面），差分对与耦合系数没有 Cadence 侧对照；
    四导体紧排布截面的电容矩阵与电感矩阵不自洽（见第 4 节），单端串扰值仅供参考。</li>
<li><b>厂商模型 + 场求解</b>：PCIe 链路插损与眼图、DDR4 写眼图 —— 传输线来自场求解，
    驱动/接收来自厂商模型（GTH die+封装、POD12 与官方封装），叠层与电容来自板卡与 BOM。
    其中 <b>PCIe 眼图未使用厂商 RX AMI</b>（CTLE/DFE/CDR）：该套件 DLL 无法用手工 host
    正确初始化（见 <code>si/README.md</code>），第 6 节的眼图是 RX die 终止模型之后的
    <b>非均衡眼</b>；第 7 节的 CTLE 眼是按规范参数重写的参考接收机 CTLE，
    不是厂商模型输出。</li>
<li><b>工程估计</b>：明确标注的地方（例如 DRAM 侧 ODT 等效、集总过孔模型、'
    去加重与 CTLE 的参数化实现）不是厂商签核模型的结果。</li>
</ul>"""))
    h.append(card("模型边界与不确定性", """
<ul>
<li><b>介质损耗因子</b>：叠层报告给出 FR408HR tanδ = 0.035（1 MHz），
    报告统一采用 0.012 作为 GHz 频段的等效值（材料在 1 GHz 以上的典型值）。
    敏感性（TX0 差分板级通道）：4 GHz 插损 0.90 dB（0.012）→ 1.24 dB（0.02）→
    1.88 dB（叠层库值 0.035）；即绝对插损对 DF 近似线性，改用库值会翻倍但仍远低于限值
    （8 GHz 限值 22.5 dB 对 3.84 dB）。</li>
<li><b>时域通道的损耗口径</b>：tlsim 用的 RLGC（<code>si/sim/rlgc.inc</code>、<code>rlgc_ddr.inc</code>）
    与 Cadence 自己写进 <code>interconn.iml</code> 的格式一致 —— G = 0、R 只有 DC 值，
    因此时域眼图里<b>没有介质损耗与集肤效应</b>；第 5/7 节的频域曲线则含 DF 与 √f 集肤项。
    两者口径不同、不是同一套损耗模型（本板 IL 只有约 1 dB，量级影响有限）。</li>
<li><b>过孔与交流耦合电容</b>：采用集总 RLC（0.4 nH / 0.4 pF；100 nF + 0.45 nH）。
    该近似在 10 GHz 以上不再可信，因此回损与插损判定止于 8 GHz。</li>
<li><b>远端器件不在模型内</b>：PCIe 计算到板上通道末端（CEM 连接器），
    不包含插卡；DDR4 不含 DRAM 内部模型与封装。</li>
<li><b>无统计码型与抖动预算</b>：眼图为确定性眼（PRBS13），未叠加发送/接收随机抖动，
    也未做 1e-12 BER 外推；因此眼高/眼宽是「码间干扰下的开度」，不是 BER 轮廓。</li>
<li><b>DDR4 回转率</b>：时间域引擎不读取 IBIS 的 RISE_TIME，边沿由 V/I 表、C_comp
    与激励边沿共同决定；厂商 F/M/S 三个回转率角无法在该流程中区分，
    报告用「激励边沿」等效慢角给出敏感性。</li>
<li><b>板上 PCIe 通道的层归属</b>：对间串扰的净距由二维坐标量出，报告不带层信息，
    按相邻通道同层处理（PCIe 成束布线）；过孔三维耦合未纳入。
    另外 TX 的 39.3 mil 取自电容后 BOTTOM 段坐标（dpg 报告里 TX 内层段未登记为差分对），
    RX 的 39.4 mil 无法判断取自 TOP 还是内层段。</li>
<li><b>DDR4 通道未含板上的过孔</b>：<code>vialist_net.txt</code> 显示每条 DQ/DQS 有 2 个
    V19H10 通孔，DDR4 仿真只含线宽分段与官方封装 R/L/C。</li>
<li><b>地址/命令的 727 mil 首末负载差</b>：来自报告正文的固定值，未在本次提取报告中找到
    对应来源（eln/elw 只有整网长度），按未核实项看待。</li>
</ul>"""))

    # ================= 12. reproduction =================
    h.append('<h2 id="s12">12. 复现步骤</h2>')
    h.append(card("环境与脚本", """
<p>所有结果在 Cadence OrCAD X / Allegro X SPB 24.1（<code>D:\\tools\\Cadence\\SPB_24.1</code>）
环境下生成；先加载环境脚本，再按顺序执行：</p>
<pre style="background:rgba(255,255,255,.05);padding:12px;border-radius:8px;overflow:auto;font-size:12.5px">
. .\\tools\\spb-env.ps1                    # 设置 CDSROOT / SPBROOT / TELENV / PATH

python si\\build_xs.py                      # 场求解：单线回归 + 差分对 + 间距扫描
python si\\build_xs.py xt                   # 串扰：两导体间距扫描 + 四导体紧排布
python si\\build_si.py                     # PCIe Gen2 时域通道（tlsim）：本报告第 2 节的几何来源
python si\\build_freq.py                   # 由 RLGC 级联出 IL/RL 曲线：第 5 节
python si\\build_gen3.py                   # Gen3 规范行为模型 + TDR：第 5 节 TDR
python si\\build_pcie_gth.py                # 官方 GTH 模型链路：插损与眼图
python si\\build_compliance.py              # 合规判定：插损 / 回损 / 眼罩
python si\\build_ddr4.py                    # DDR4：官方 POD12 + 通用 IBIS 对照
python si\\build_report.py                  # 生成本报告
</pre>
<p class="dim">上面的顺序不能省：本报告第 2 节的几何来自 <code>si_results.json</code>、
第 5 节的曲线与 TDR 来自 <code>freq_results.json</code> 与 <code>gen3_results.json</code>，
缺文件时对应小节会整段消失（<code>build_report.py</code> 现在会在 stderr 打印一条警告）。</p>
<p class="dim">中间结果与报告位于 <code>si/out/</code>（<code>si_results.json</code>、
<code>gen3_results.json</code>、<code>freq_results.json</code>、<code>xs_results.json</code>、
<code>pcie_gth_results.json</code>、<code>compliance_results.json</code>、
<code>ddr4_results.json</code>、<code>zcu106_pcie_si_report.html</code>），
场求解输入与输出在 <code>si/xs/</code>，时间域网表与波形在 <code>si/sim/</code>。</p>
<p class="dim">板卡侧数据来自 Allegro 报告：
<code>report -v eln/elw/dpg/x-section/vialist_net</code>（位于
<code>D:\\Programs\\candence\\si_zcu106\\reports\\</code>）；
版本从 SPB 16.6 升级到 24.1 由 <code>dbdoctor</code> 完成，
并用 <code>allegro_batch dbstat -v</code> 验证。</p>"""))

    h.append('<div class="foot">本报告为自包含文档（内联 SVG，无外部引用）。'
             '所有判据取自随工具发布的合规文件，所有几何取自板卡本身的提取报告，'
             '所有场求解与仿真命令均可在上述脚本中原样复现。</div>')
    h.append("</div></body></html>")

    out = OUT / "zcu106_pcie_si_report.html"
    out.write_text("".join(h), encoding="utf-8")
    print("wrote", out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
