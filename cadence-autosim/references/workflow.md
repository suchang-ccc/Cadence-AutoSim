# End-to-end workflow

Goal: turn a Cadence board plus vendor models into a report whose every number is
answerable to a file, a command line and a line number.  Steps are ordered because later
stages consume earlier JSON.

## 0. Decide the analysis scope first

Write down: which nets, which interface (rate, levels, termination), which reference
planes, and what would make the answer "fail".  The scope decides how much of the board
has to be extracted - analysing 18 nets is not the same as analysing 4 lanes.

## 1. Environment

```powershell
. .\scripts\spb_env.ps1
```

Check that `bem2d`, `signoise`, `tlsim` and `allegro_batch` resolve, and that a trivial
`allegro_batch` run works before building a pipeline on top of it.

## 2. Board data -> geometry

Convert the board if it is an older revision (`dbdoctor -outfile`, then `dbstat -v` to
confirm the version), then ask the tool for its own reports:

```
report -v x-section      # stackup: thicknesses, Er, tanD, nominal widths
report -v elw            # etch length per net, per layer, per width
report -v eln / ell      # etch length per net / per net and layer
report -v dpg            # differential pairs: nominal/actual gap + segment coordinates
report -v vialist_net    # vias per net
report -v net            # pins per net (which ball the net lands on)
report -v cmp            # BOM (part numbers, e.g. which DRAM)
```

Rename the board so the file name has no `.` before any `signoise` run.

Feed the extracted lengths/widths into the channel scripts as explicit segment lists
(`[(layer, width, length_mil), ...]`).  Never hand-type a length that the tool can report.

While here, resolve the die pins: `net` gives `U1.<ball>` and the vendor package model
maps ball -> pin function (`MGTHTXP3_224`, `IO_L17P_T2U_...`).  That mapping decides which
vendor kit applies; getting it wrong invalidates every eye in the report.

## 3. Field-solve the cross sections

`scripts/bem2d_solve.py`:

* one solve per distinct geometry actually used (each width on each layer, the
  intra-pair gaps, the pair-to-pair spacing you care about);
* regression first: re-solve a section the tool already solved (in `interconn.iml`) and
  compare `Zc/C/L/R/Td`.  If that does not match digit for digit, nothing downstream is
  trustworthy;
* derived quantities: `Z0` for single lines; `Zodd/Zeven/Zdiff = 2*Zodd` and `kc = |C12/C11|`,
  `kl = |L12/L11|` for pairs.  In a homogeneous medium `kc` must equal `kl` - if it does
  not, the solve is numerically thin, do not quote the crosstalk.

Also solve the **outer-layer** sections with the microstrip builder: an outer escape is
not a stripline, and using the stripline closed form on it understates its impedance by a
factor of two (10 mil outer trace: 37.6 ohm solved vs 12.8 ohm from the stripline form).

## 4. Channel models

Two consumers, two model shapes, same interconnect:

* **Frequency domain** (loss, return loss, compliance masks): cascade the per-unit-length
  RLGC with `alpha = 0.5*(Rac/Z0 + G*Z0)`, `Rac ∝ sqrt(f)`, `G = w*C*DF`, plus lumped vias
  and AC-coupling caps, into ABCD and then S-parameters.  Record the dissipation factor and
  show its sensitivity.
* **Time domain** (`tlsim`): `NTL` sections with an RLGC file (one `DATAPOINTS RLGC <name>`
  block per width, `FREQUENCY=0` and `FREQUENCY=INF`), the driver as `bdrvr` with a
  `DATAPOINTS BDRVR` model (vendor IBIS via `scripts/ibis_to_tlsim.py`, or a documented
  ideal source), the receiver as its real termination (get it right: a DDR4 POD12 receiver
  needs 40 ohm **to VDDQ**, not to ground - "to ground" hides the high level below the
  threshold and every eye looks broken for the wrong reason).

For a chip-to-chip link with vendor models, cascade in the S/Z domain instead
(`scripts/touchstone.py`): `Tx_die -> Tx_package -> board -> Rx_package -> Rx_die`, then
generate the impulse response by IFFT and convolve the stimulus - this is what an AMI
engine would do with the AMI models, minus the equalisation.

## 5. Results and compliance

* Per lane: insertion loss at each generation's Nyquist (1.25 / 2.5 / 4 / 8 GHz), return
  loss, and the eye at the receiver node.
* Compare against the mask files the tool ships (`share/topxp/sample_masks/*.maskfile`)
  rather than invented limits, and report the *worst margin* over the mask's frequency
  range - not a degenerate 0.0 dB.
* When a result is within the uncertainty of a lumped model (a return-loss dip of
  0.1 dB), say so and show the sensitivity: varying the lumped via capacitance by 2x
  flipped "fail" to "pass".

## 6. Report and audit

`scripts/report_kit.py` renders one self-contained HTML file (inline SVG, no external
references, no narrative about the process), `scripts/check_html_report.py` checks it
structurally.  Then audit the report against its sources with
[audit-checklist.md](audit-checklist.md) and hand over the audit table with the report.

## What this workflow deliberately does not do

* No statistical/BER eye or jitter budget - the eyes are deterministic (PRBS) eyes under
  ISI, and should be labelled that way.
* No 3-D via coupling, no DRAM-internal model, no card/riser beyond the connector, no
  IBIS-AMI equalisation unless a real AMI engine is available.
* No claim about the far end device beyond what its model provides.
