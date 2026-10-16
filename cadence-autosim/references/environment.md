# Environment

## Cadence install and session variables

```powershell
. .\scripts\spb_env.ps1                                    # autodetect
. .\scripts\spb_env.ps1 -SpbRoot D:\apps\Cadence\SPB_24.1  # explicit
. .\scripts\spb_env.ps1 -SpbHome D:\Cadence\SPB_Data       # where pcbenv lives
```

Dot-source it; running it as a script sets the variables in a child process and leaves your
session unchanged.  What it sets and why:

| variable | value | why |
|---|---|---|
| `CDSROOT` / `SPBROOT` | install root | the CLI tools abort with "Can't find Allegro env file" without it |
| `TELENV` | `<root>\share\pcb\text\env` | Tcl/env search path for headless SKILL |
| `HOME` | a Cadence data dir, not the user profile | where `pcbenv` and tool state are looked up |
| `CDS_LIC_FILE` | e.g. `5280@localhost` | licence; if the port is not listening some tools still start and others fail late |
| `PATH` | `<root>\tools\pcb\bin`, `<root>\tools\bin` prepended | so `bem2d`, `signoise`, `tlsim`, `allegro_batch` resolve |

## Python

The field-solve/channel scripts need **numpy**; the report renderer and the HTML checker are
standard library only (useful when the machine's default interpreter has no numpy).  Pick
the interpreter deliberately:

```powershell
python scripts\bem2d_solve.py --demo          # needs numpy? no - standard library only
python scripts\touchstone.py --selftest       # needs numpy
python scripts\ibis_to_tlsim.py --selftest    # standard library only
python scripts\report_kit.py --demo out.html  # standard library only
python scripts\check_html_report.py out.html  # standard library only
```

## Working directory hygiene

* Run every solve/simulation in a scratch directory (the scripts default to a fresh
  temporary directory).  `signoise` writes `devices.dml` into the current directory, and
  the engines drop `.dl`/`.dst`/`cycle.msm` files next to the netlist.
* Keep generated artefacts next to the JSON they came from and never let a script read a
  previous run's output without saying so.
* Board file names must not contain a `.` (see the signoise section of
  [cadence-cli-invariants.md](cadence-cli-invariants.md)).

## What lives where in a run directory

```
<work>/
|- scripts/            analysis scripts (one per stage)
|- board/              board file + tool reports (x-section, elw, eln, dpg, ...)
|- models/             vendor IBIS/AMI/package/touchstone files
|- sim/                generated netlists, RLGC includes, waveforms
|- out/
|   |- <stage>.json    stage results (the report reads only these)
|   `- <board>_si_report.html
`- audit.md            the audit table and correction list
```
