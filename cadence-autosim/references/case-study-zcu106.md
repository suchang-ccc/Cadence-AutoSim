# Case study: ZCU106 (Xilinx ZU7EV) PCIe Gen2/Gen3 x4 + PL DDR4-2666

A complete run of this workflow, kept as a worked example and as a template for what the
stage JSON, the report and the audit look like.  Board data: `9217_1280937-HW-Z1-ZCU106`
Rev 1.0, up-revved 16.6 -> 24.1 with `dbdoctor`.

## What was analysed

* PCIe x4 on the PL GTH quad 224 - TX0..TX3 on balls `AD4/AD3`, `AE6/AE5`, `AG6/AG5`,
  `AH4/AH3` (`MGTHTXP/N3..0_224`), AC-coupled 100 nF, to the P3 CEM connector;
* PL DDR4-2666 point-to-point DQ0 / DQ8 / DQS0_T from U1 (`AF16`, `AA14`, `AH14`,
  HP-bank I/O, POD12) to Micron MT40A256M16GE-075E;
* stackup 16 layers, 61.19 mil total, FR408HR Er 3.4 / tanD 0.035 (1 MHz); the PCIe
  cavity is `03_SIG1`/`12_SIG4` with 2.89 + 0.6 + 3.0 mil between planes.

## Pipeline as run

| stage | script | output |
|---|---|---|
| field solves | `build_xs.py` (bem2d) | `xs_results.json` |
| frequency domain | `build_freq.py` | `freq_results.json` |
| Gen2 time domain | `build_si.py` (tlsim, ideal drivers) | `si_results.json` |
| Gen3 behaviour + TDR | `build_gen3.py` | `gen3_results.json` |
| official GTH models | `build_pcie_gth.py` (+ `pcie_link.py`, `s4p.py`) | `pcie_gth_results.json` |
| compliance | `build_compliance.py` | `compliance_results.json` |
| DDR4 | `build_ddr4.py` (+ `ibis2tlsim.py`) | `ddr4_results.json` |
| report | `build_report.py` (+ `report_v1.py` helpers) | `zcu106_pcie_si_report.html` |

## Headline results (after the audit fixes below)

* board-level insertion loss 0.75-1.05 dB at 4 GHz (100 ohm differential), worst margin
  over the 1-8 GHz mask **3.9 dB at 1 GHz**; return loss passes below 5.9 GHz and runs
  0.05-0.11 dB past the limit between 5.9 and 7.7 GHz - inside the lumped-model
  uncertainty, and the verdict flips to "pass" if the via capacitance is halved;
* chip-to-chip loss with the official GTH models 7.2-7.9 dB (Gen2/Gen3), of which only
  ~1 dB is the board; unequalised eyes 442-465 mV (5 GT/s) and 222-281 mV (8 GT/s, with
  the -3.5 dB preset);
* field-solver regression: 4.2 mil stripline -> 40.404 ohm, identical to the number the
  tool itself saved; differential pair 77.0 ohm at the 4.2 mil intra-pair gap;
* DDR4 write eye 579-597 mV with 40 ohm ODT to VDDQ against Vinl/Vinh = 0.76/0.92 V.

## The twelve defects the audit found

Numbers are from the delivered report and its stage JSON.

1. **TX de-emphasis implemented inverted** (`ffe()` used `post = -main*(1-r)`): repeated
   bits came out *higher* than the transition bit, contradicting the kit's own
   `Emphasis = 20log10(V_HIGH/V_LOW)`. Fixed sign moved Gen3 eye height from
   320/301/315/329 mV to 233/281/244/222 mV, and made the 8 GT/s eye plot collapse from
   four apparent levels to two.
2. **Four-conductor crosstalk geometry off by one width** (`xs = [0, w+g, w+g+s+w, w+g+s+w+g]`):
   conductors 3 and 4 ended up edge to edge, bem2d never returned, and the script silently
   fell back to a previous run's artefact. Fixed; the live solve now reproduces the
   recorded matrices digit for digit.
3. **Single-ended crosstalk quoted from a numerically thin solve**: `kc = 2.1e-5` while
   `kl = 2.1e-3` in the same (homogeneous) cross-section. The single-ended value is now
   taken from the inductor side (`kc = kl`) and both are recorded.
4. **Outer-layer escape modelled as a stripline**: the 22.3 mil TOP segment used the
   inner-layer closed form (12.8 ohm) instead of a microstrip solve (37.6 ohm).
5. **DDR4 comparison arms terminated differently**: the generic-IBIS arm went to ground,
   the vendor arm to VDDQ; the "difference" was mostly the termination. Both arms now use
   40 ohm to VDDQ (levels 1218-1229 / 574-585 mV vs 1203-1208 / 595-597 mV).
6. **Two numbers hard-coded in the renderer** ("eye height 258-303 mV", "456-595 mV") that
   no longer matched any JSON; both now computed from the results.
7. **A physically impossible claim**: "the usual 85 ohm target corresponds to ~8 mil
   spacing" - with that stackup and width, widening the gap can only reach the uncoupled
   limit 2x40.4 = 80.8 ohm.
8. **A sentence contradicting its own table** ("both arms sit at 1.20 V" next to a table
   showing 632 / -36 mV) - a consequence of defect 5.
9. **A degenerate criterion reported as a pass**: the 0.30 UI x 25 mV eye mask cannot be
   hit by a +/-160 mV signal, so "0 hits" is structural and identical for every lane.
10. **Eye figure folded on 2 UI**: the NRZ eye was drawn twice and the second copy cut in
    half by the window edge - it read as a three-level signal. Now 1 UI, aligned to the
    received eye centre.
11. **Reproduction list missing three of the scripts** the report consumes, with the
    generator silently dropping sections when a JSON is absent.
12. **A superseded generator writing the same output path**, so running the "old" report
    script would replace the deliverable with a stale narrative.

Additional things the audit had to mark **not verifiable** rather than guess: the 1 MHz
qualifier on tanD (the extraction report has no frequency column), a "727 mil first-to-last
load spread" for the fly-by address bus (appears only in the report text), the reference
plane of the shipped eye mask, and the inner-layer TX pair-to-pair spacing (that pair is
not registered as a differential pair in the tool's `dpg` report).
