# Cadence AutoSim

Automate Cadence Allegro X / OrCAD X (SPB) **signal-integrity analysis** from the command
line, and audit the result against its own sources.

It exists because a commercial channel simulator is not always available - and because an
SI report is only as good as its worst unverified number.  The workflow drives the tools
Cadence already ships (`bem2d` field solver, `signoise` extraction, `tlsim` time-domain
engine) plus vendor IBIS/AMI kits, and produces one self-contained HTML report whose every
figure traces back to a JSON result, a command line and a source file.

The skill is in [`cadence-autosim/`](cadence-autosim/):

```
cadence-autosim/
|- SKILL.md                  entry point: workflow, invariants, deliverables
|- agents/openai.yaml        UI metadata
|- scripts/
|   |- spb_env.ps1           dot-source: CDSROOT/SPBROOT/TELENV/HOME/PATH
|   |- bem2d_solve.py        bare-number cross-sections -> bem2d -> RLGC/Zc/Zdiff/kc,kl
|   |- touchstone.py         sNp reader, port re-order, S<->Z<->ABCD, mixed mode, cascade
|   |- ibis_to_tlsim.py      vendor IBIS [Model] -> tlsim DATAPOINTS BDRVR
|   |- report_kit.py         self-contained HTML helpers (charts, 1-UI eye, tables, page)
|   `- check_html_report.py  structural check of the produced report
`- references/
    |- workflow.md               the end-to-end pipeline and its decision points
    |- cadence-cli-invariants.md what silently goes wrong in bem2d/signoise/tlsim/AMI
    |- environment.md            environment, python, working-directory hygiene
    |- audit-checklist.md        how to audit an SI report, item by item
    `- case-study-zcu106.md      a full board run and the 12 defects its audit found
```

## Use it

Copy `cadence-autosim/` into your skills directory (or point your agent at it):

```powershell
Copy-Item -Recurse cadence-autosim "$env:USERPROFILE\.codex\skills\"
```

Then invoke it explicitly (`$cadence-autosim`) or let it be selected when the task matches
("field-solve this board's PCIe lanes and produce a report").

Quick taste of the scripts, no board required:

```powershell
. .\cadence-autosim\scripts\spb_env.ps1
python .\cadence-autosim\scripts\bem2d_solve.py --demo      # solve a few reference sections
python .\cadence-autosim\scripts\touchstone.py --selftest   # S-parameter algebra
python .\cadence-autosim\scripts\ibis_to_tlsim.py --selftest
python .\cadence-autosim\scripts\report_kit.py --demo demo.html
python .\cadence-autosim\scripts\check_html_report.py demo.html
```

`--demo` on the field solver reproduces, among others, the 4.2 mil stripline result
(40.404 ohm) that Cadence itself saves for the same stackup - a useful first confidence
check before pointing it at a real design.

## Scope

In scope: geometry extraction from the tool's own reports, 2-D field solving, RLGC/ABCD/S
channel assembly, tlsim time-domain eyes, PCIe/DDR-style compliance against the mask files
the tool ships, and the audited HTML report.

Out of scope (state it in the report rather than implying it): statistical/BER eyes and
jitter budgets, 3-D via coupling, DRAM-internal models, anything beyond the board's
connector, and IBIS-AMI equalisation unless a real AMI engine is available.  Vendor AMI
DLLs frequently cannot be initialised by a hand-written host - see the invariants document
for what to do instead of guessing.

## License

No license file is included yet; add one before relying on this in a commercial setting.
