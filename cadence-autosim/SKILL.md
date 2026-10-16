---
name: cadence-autosim
description: Automate Cadence Allegro/OrCAD X signal-integrity analysis end to end - board geometry extraction, bem2d field solving, tlsim channel simulation, PCIe/DDR compliance checks and a self-contained HTML report - and audit the result against its sources. Use when analysing a PCB link (PCIe, DDR, Ethernet, SerDes) with Cadence tools on the command line, when a vendor channel-simulation tool is unavailable, or when an existing Cadence-based SI report has to be verified.
---

# Cadence AutoSim

Drive Cadence Allegro / OrCAD X PCB SI from the command line to build a **traceable** SI
report for a board link: measured geometry in, field-solved interconnect + vendor models
in the middle, loss/RL/eye/mask results and a self-contained HTML report out.

The whole workflow exists to make every number answerable to a source: a file, a command
line and a line number. Do not add a number to a report that cannot be traced that way.

## Workflow

Full command list, inputs/outputs and decision points: [references/workflow.md](references/workflow.md).
Read it before starting a new board — the order matters and skipping a step silently
empties a report section.

1. **Environment** — `scripts/spb_env.ps1` sets `CDSROOT`/`SPBROOT`/`TELENV`/`HOME`; the
   SI tools need it (see [references/environment.md](references/environment.md)).
2. **Board data** — get geometry from Allegro's own reports (`report -v x-section/elw/eln/dpg/
   vialist_net/net/cmp`), never by eyeballing the layout. Etch lengths and widths become the
   hard-coded segment lists of the channel models.
3. **Cross sections** — solve every distinct geometry with `bem2d`
   (`scripts/bem2d_solve.py`) and *regress* one case against a section the tool itself
   already solved (usually stored in its `interconn.iml`) before trusting the rest.
4. **Channel** — cascade the field-solved RLGC + vias + AC caps into S-parameters for
   frequency-domain work (`IL`/`RL`, compliance masks) and into `tlsim` netlists for
   time-domain eyes. Vendor IBIS/AMI models plug in here (`scripts/ibis_to_tlsim.py`,
   `scripts/touchstone.py`).
5. **Compliance** — compare against the mask files the tool ships with, keeping the
   mask's own reference plane in mind; state it in the report.
6. **Report** — `scripts/report_kit.py` renders JSON results as one self-contained HTML
   file (inline SVG, no external references) and `scripts/check_html_report.py` checks it
   structurally before delivery.

## Non-obvious invariants that decide whether this works

Detail and the evidence behind each item: [references/cadence-cli-invariants.md](references/cadence-cli-invariants.md).

- `bem2d` needs the **bare-number** cross-section syntax (`+shield(...)`/`+dielectric(...)`
  with plain numbers, terminated by `.ends`). Geometry must not reach negative x - it
  returns an empty result silently. The y axis accumulates *dielectric* thickness only;
  reference-plane copper thickness is a separate argument.
- Every field-solve or simulation run must be **reproducible in a scratch directory**;
  never let a script fall back to a stale artefact silently - print a warning instead.
  (A silent fallback once put a superseded number into a delivered report.)
- `signoise` needs `-f -r -o -w -C` all present or it exits without output, and board
  file names must not contain a `.`. Do not redirect its stdout to a file.
- `tlsim` `NTL` line statements need their `+L=` continuation on its own line; the engine
  has no expression-source parser and ignores IBIS `RISE_TIME` (edges come from the V/I
  tables, `C_comp` and the *stimulus* edge - verify this per project, do not assume).
- The `bdrvr` node order is
  `bdrvr <pullup_ref> <out> <pulldown_ref> <DATA> <ENABLE> <pclamp_ref> <gclamp_ref>`.
  Getting DATA/ENABLE backwards still produces a plausible-looking waveform.
- Vendor AMI DLLs (Xilinx GTH kit, Cadence `amictwf*`) generally do **not** follow the
  IBIS-AMI 12-argument `AMI_Init` prototype and cannot be initialised from a hand-written
  host. Say so in the report and label the eyes "unequalised, at the RX die node" instead
  of implying an AMI sign-off.
- An NRZ eye diagram folds on **one** UI, aligned so the data transition sits on the plot
  edges. Folding on 2 UI draws the same eye twice and, if the fold is anchored to the
  transmitter instead of the received eye centre, cuts the second copy in half - the
  figure then looks like a three-level (PAM4) signal that is not there.
- State the reference impedance / reference plane of every loss and eye number, and the
  *definition* of every eye metric (an eye height that is a P90-P10 spread is not an eye
  opening; an eye width that saturates at 1 UI carries no margin information).

## Deliverables

Produce all four, and keep them mutually consistent:

- `out/*.json` - one file per analysis stage, holding the raw curves and scalars the
  report reads (never hard-code a result into the renderer).
- `out/<board>_si_report.html` - self-contained: inline SVG, zero external references,
  no narrative about the process itself.
- An **audit table**: item / source (file, command, line) / raw value / reported value /
  agrees / impact / action. [references/audit-checklist.md](references/audit-checklist.md)
  lists the check items and the failure classes worth hunting.
- A correction list graded must-fix / should-fix / annotate, each entry carrying its
  original evidence.

A worked example with real numbers, including the twelve defects that an audit of it
found: [references/case-study-zcu106.md](references/case-study-zcu106.md).

## Scripts

| Script | Purpose |
|---|---|
| `scripts/spb_env.ps1` | dot-source to set the Cadence environment for a session |
| `scripts/bem2d_solve.py` | build bare-number cross sections (stripline/microstrip/coupled), solve with `bem2d`, parse RLGC/Zc, derive Z0/Zdiff/kc/kl |
| `scripts/touchstone.py` | read Touchstone sNp, port re-ordering, S/Z/ABCD conversion, mixed-mode SDD, multi-port cascade |
| `scripts/ibis_to_tlsim.py` | convert vendor IBIS `[Model]` blocks into the `DATAPOINTS BDRVR` form `tlsim` consumes |
| `scripts/report_kit.py` | drawing/table/CSS helpers and a `page()` assembler for the self-contained HTML report (incl. the 1-UI eye diagram) |
| `scripts/check_html_report.py` | structural check of the produced HTML: external refs, tag balance, chart bounds, eye traces |

Every script takes `--help` and ships a `--selftest`/`--demo` that runs without a board.
Run those before pointing them at a real design.

## Reference implementation

`assets/zcu106-reference/` is the working pipeline behind
[references/case-study-zcu106.md](references/case-study-zcu106.md) - the fastest way to see
what each stage produces and how a real board's numbers are wired together:

| Script | Stage |
|---|---|
| `build_xs.py` | bem2d field solves: single-line regression, differential pairs, intra-pair and pair-to-pair sweeps, crosstalk bundles |
| `build_freq.py` | per-unit RLGC -> ABCD -> IL/RL curves |
| `build_si.py` | Gen2 time-domain channel in tlsim (vias, AC cap, PRBS) |
| `build_gen3.py` | Gen3 spec behaviour model + TDR profile |
| `build_pcie_gth.py`, `pcie_link.py`, `s4p.py` | vendor GTH die/package/channel cascade and eyes |
| `build_compliance.py` | IL / RL / eye-mask verdicts from the tool's own mask files |
| `build_ddr4.py`, `ibis2tlsim.py` | POD12 write channel from vendor IBIS + package model, edge self-check |
| `build_report.py`, `report_v1.py` | report assembly from the stage JSON (helpers; see note) |

Copy the folder next to your project, point `SPBROOT`, `ZCU106_WORK` and `AMD_MODELS` /
`GTH_KIT` at your install/workspace (every absolute path is env-overridable), replace the
board's extracted numbers, and run the stages in the order of
[references/workflow.md](references/workflow.md).  `report_v1.py` is that project's legacy
helper module - `scripts/report_kit.py` supersedes it for new work, and its `main()` is
disabled because it would overwrite the report with a superseded version.
