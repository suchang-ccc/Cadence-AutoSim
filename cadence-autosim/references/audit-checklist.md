# Auditing an SI report against its sources

An SI report is a chain of conversions, and every link in it can be silently wrong: a
hard-coded number that stopped matching its JSON, a model class applied to the wrong
layer, a script that fell back to a stale artefact, a mask applied at a reference plane
it was not written for.  The audit is what makes the report defensible.

Rule for the whole exercise: **no conclusion without a pointer** - file, command, line
number, value.  If it cannot be pointed at, mark it "not verifiable" instead of reasoning
it through.  Do not repair the analysis while auditing; record evidence and produce a
correction list at the end.

## Method

1. Read every number in the report and ask "where does this come from?" - the JSON, the
   script or the tool report.  Numbers that exist only in the renderer are already
   suspicious.
2. Re-run the cheap, deterministic steps (field solves, model-parameter dumps, structure
   checks) in a **scratch directory** so the delivered artefacts stay untouched, and
   compare digit for digit.
3. For each mismatch, record: item, source (file/command/line), raw value, reported value,
   impact, action.  One row per item.
4. Grade the corrections: must-fix (a number or conclusion does not survive), should-fix
   (convention, presentation, reproducibility) and annotate-only (assumption to disclose).
   Every must-fix carries its raw evidence so it can be re-checked after the fix.

## Check items

Geometry and stackup

- layer count, total thickness, Er/tanD and the *frequency* those were quoted at;
- cavity dimensions of the layers actually used by the signals, trace copper thickness;
- nominal stackup widths vs the widths the etch report measured - they differ, and the
  channel must use the measured ones;
- segment lengths and width breakdown per net: each hard-coded number in the channel
  scripts against the tool's `elw`/`eln` lines;
- via count per net against `vialist_net` (and whether the channel model includes them);
- die pins: ball -> pin function from the vendor package model, not from memory;
- differential-pair gaps and pair-to-pair distance: which layer were they measured on?
  `dpg` reports carry coordinates but no layer.

Field solving

- re-run the regression case; compare `Z0/C/L/R/Td` with the tool's own saved result;
- confirm the three input conventions (bare numbers, no negative x, y = dielectric
  accumulation) hold for the actual input files;
- `Zodd/Zeven/Zdiff` definitions and the sign/absolute-value convention of the coupling
  ratios; `kc == kl` sanity in a homogeneous dielectric;
- reproducibility of each sweep point, and whether any point was silently taken from a
  previous run's artefact.

Models and parameters

- vendor IBIS: model names, `C_comp`, `Vinl/Vinh`, table sizes, and the 20-80% edge taken
  from the vendor's own rising waveform (not the ramp `dV/dt`);
- package R/L/C: confirm these are the *self* terms of the ball in question, and that the
  file's units are what you assumed;
- kit version, topology order and port mapping against the kit's own port-map file;
- receiver thresholds vs the termination used (a threshold you never cross is a modelling
  error, not a design finding);
- for anything called "vendor", check the vendor model is actually in the signal path: a
  re-implemented reference equaliser is not the vendor's equaliser.

Simulation settings

- stimulus: UI, pattern and length, edge, amplitudes;
- terminations, including whether the two arms of a *comparison* use the same termination
  (otherwise the comparison measures the termination);
- engine quirks that were documented per project (which knob really sets the edge);
- analysis settings belong in the report, not only in the script.

Results and criteria

- spot-check reported values against the JSON curves;
- worst margin over the *whole* band, computed as `min(limit - value)`;
- mask semantics: units, reference plane, and whether the mask can physically be hit;
- sensitivity of any marginal verdict to the lumped elements;
- eye metrics: definition (percentile spread vs vertical opening), width saturation at
  1 UI, and whether the figure's fold hides or double-counts an eye.

Report artefact

- self-contained (no external references), balanced tags, chart geometry inside the
  canvas, eye figures carrying real traces;
- no numbers that exist only in the renderer;
- no process narrative ("this round", "the previous version") in a delivered document;
- nothing that still writes the deliverable from a superseded generator;
- reproduction instructions that actually cover every script the report consumes, and a
  build that fails loudly when an input is missing instead of silently dropping a section.

## Failure classes seen in practice

| Class | Example |
|---|---|
| stale hard-coded number | KPI said "258-303 mV" while the JSON said 300-303 |
| wrong model class | stripline formula used for an outer-layer escape (12.8 vs 37.6 ohm) |
| sign/order convention | TX de-emphasis implemented inverted; `bdrvr` DATA/ENABLE swapped |
| silent fallback to an old artefact | geometry bug made the solve fail, script reused a previous `.OUT` |
| internally inconsistent solve | multi-conductor `kc != kl` by 100x, single-ended crosstalk quoted anyway |
| unit / reference-plane ambiguity | a 25 mV eye mask applied to a +/-160 mV signal, plane unstated |
| comparison arms not matched | generic driver terminated to ground vs vendor driver to VDDQ |
| metric definition drift | "eye height" = percentile spread; "eye width" capped at 1 UI |
| figure convention | NRZ eye folded on 2 UI -> reads as a three-level signal |
| document integrity | a superseded generator writing the same output path |
