# Cadence CLI invariants

Behaviour that was established by experiment on SPB 24.1 (Allegro X / OrCAD X) and that
silently produces wrong or empty results when ignored.  Each item says what went wrong,
not just what to do.

## bem2d (2-D field solver)

* **Bare-number syntax.** A cross-section the solver accepts looks like

  ```
  .subckt SL_4P2
  +in1 0 out1 0
  .layerstack Layerstack
  +shield( 1.524e-05 1 0.035 )
  +dielectric( 7.3406e-05 3.4 0.035 )
  +dielectric( 1.524e-05 3.4 0.035 )
  +dielectric( 7.62e-05 3.4 0.035 )
  +shield( 1.524e-05 1 0.035 )
  .crosssection
  +rectangle ( 5.959e+07 0 7.3406e-05 0.00010668 8.8646e-05 )
  +Length=length
  .ends SL_4P2
  ```

  Material *names* (`.material` blocks) do not work through this entry point - write
  numbers.  Invocation is `bem2d 0 <file.in> 0.00127`; the last argument is the mesh
  size in metres (0.00127 is what signoise uses) and must match the board's mesh if you
  want to compare against the tool's own numbers.
* **Negative x returns nothing.** Any rectangle reaching x < 0 makes the solver produce
  an empty `.OUT` (or no file at all) *without an error message*.  Shift every geometry
  so the left-most edge is x = 0; the result is unchanged and it is what the tool itself
  stores.
* **y accumulates dielectric thickness only.** Reference-plane copper thickness is a
  separate argument on `+shield(...)` and does not move the conductors.  A trace
  "2.89 mil above the plane" has its bottom edge at y = 2.89 mil even though the plane
  is 0.6 mil thick.
* **Only R at DC is reported.** `.R 0` is the DC resistance; the frequency-dependent
  resistance and the dielectric loss (`.G`) are *not* in the output.  Any loss model used
  downstream has to supply them (see the dissipation-factor note below).
* **Four or more conductors spaced far apart may not solve.** Closely spaced bundles are
  fine; a cross-section with tens of mil of empty dielectric between conductor groups can
  hang or return nothing.  Two conductors solve out to at least 35 mil of gap.
* **Sanity-check any multi-conductor solve against physics.** In a homogeneous dielectric
  the capacitive and inductive coupling ratios must be equal (`kc == kl`).  A solve where
  `kc` and `kl` differ by 100x has a numerically useless capacitance matrix even though it
  "succeeded" - do not quote the single-ended crosstalk number from it.

## signoise (board-level extraction/simulation)

* **Give it all of `-f -r -o -w -C`.** With any option missing it exits without output
  and without an error.  `-f` takes the net list file.
* **Board file names must not contain a `.`** - the `.` is encoded as `##` internally, the
  net names in `-f` then fail to match, and the report is silently empty.
* **Do not redirect stdout to a file.** It can write gigabytes of sparse output; capture
  or discard it instead.
* It **overwrites `devices.dml`** in the working directory.  Copy the one you care about
  first if you need it.
* The board-level crosstalk (`XtalkSummary`) path can run for many minutes with no output
  on a 20-net extraction - budget for it or cover crosstalk with field-solved sections
  instead.
* The RLGC it saves for each section (in `interconn.iml`, together with the geometry it
  solved) is the best regression reference you will get: re-solve the same section with
  `bem2d` and compare `Zc`, `C`, `L`, `R`, `Td` digit for digit.

## tlsim (time-domain engine)

* **`NTL` line continuations must be on their own line**: `NTL_A ( n1 0 ) ( n2 0 )` then
  `+L=0.0555 rlgc_name=SL_4P2 file=rlgc.inc`.
* **No expression-parser.** Controlled sources written as `g1 ... cur='...'` are not
  understood; behavioural models must be rewritten with plain linear sources.
* **It ignores IBIS `RISE_TIME`/`FALL_TIME`.** The output edge comes from the V/I tables,
  `C_comp` and the *stimulus* edge.  Prove it per project (step the model into a resistive
  load with several `RISE_TIME` values and compare the measured 20-80% time) before
  building a slew-corner argument on it.
* **`bdrvr` node order** is
  `bdrvr <pullup_ref> <out> <pulldown_ref> <DATA> <ENABLE> <pclamp_ref> <gclamp_ref>`.
  Verify it against a netlist the tool generated itself rather than trusting the
  documentation: swapping DATA and ENABLE still runs and still produces a plausible
  waveform - with the pull-down device never conducting.
* `-SP` / `-FDA` modes can exit silently without producing output.
* Waveforms are read from the `.sim` file as lines
  `S!<time>!<node#>!<volts>!<node name>!`.

## Board data (`report -v ...`, dbdoctor, licences)

* Geometry must come from the tool's own reports: `x-section` (stackup), `elw`/`eln`/`ell`
  (length by layer/width), `dpg` (differential-pair gaps with segment coordinates),
  `vialist_net` (via count per net), `net` (pins per net), `cmp` (BOM).  Report files
  contain no layer information in `dpg` - state that assumption if you use those
  coordinates for same-layer distance maths.
* An up-rev conversion (16.6 -> 24.1) is `dbdoctor -outfile`; verify with
  `allegro_batch dbstat -v` and keep the conversion log.
* `il_allegro` (headless SKILL) needs `CDSROOT`, otherwise "Can't find Allegro env file".
* Licence: `CDS_LIC_FILE=<port>@<host>`.  If the port is not listening, some tools still
  run and others fail late; check early with a trivial `allegro_batch` invocation.

## Loss models (frequency vs time domain)

The board stackup gives `tanD` at 1 MHz (e.g. FR408HR 0.035).  Using that value directly
at GHz overestimates loss badly; a GHz-equivalent dissipation factor (often ~0.012) is
what the frequency-domain curves should use, and the sensitivity must be reported:
for one 2.2 in channel, 4 GHz insertion loss went 0.90 dB (DF 0.012) -> 1.24 dB
(DF 0.02) -> 1.88 dB (DF 0.035).

The RLGC blocks that `tlsim` consumes (`G = 0`, `R` only at DC) carry **no** dielectric
loss and **no** skin effect, so time-domain eyes and frequency-domain curves are not the
same loss model.  Say so in the report instead of pretending they agree.

## Vendor IBIS-AMI kits

* Kits ship a port map (e.g. `s4p_port_map.txt`: `Rx_Package: [2 1 4 3]`).  Feed it to
  the port re-ordering helper; do not assume the file's port order is the topology's.
* The die-termination s4p files are part of the link (they sit between the package and
  the AMI model) - they carry a few dB of "loss" that belongs to the model, not the board.
  Split the budget accordingly.
* Vendor AMI DLLs frequently do **not** implement the IBIS-AMI 12-argument `AMI_Init`
  prototype (the Xilinx GTH kit and Cadence's `amictwf*` both differ: leading integer
  arguments ignored, argument slots shifted, a second double in XMM3).  Calling them with
  the documented prototype crashes inside the DLL.  If a hand-written host cannot
  initialise them, report the eyes as "unequalised, at the RX die node" and do not present
  them as an AMI sign-off.
* A mask file (`sample_masks/*.maskfile`) declares only units (`UI V`, `Hz dB`) and
  geometry.  It does not declare its reference plane.  State the plane you applied it at,
  and check whether the mask can bite at all: a 25 mV-tall hexagon applied to a +/-160 mV
  eye yields "0 hits" for every lane - a structural result, not an achievement.
