#!/usr/bin/env python3
"""
Convert an IBIS [Model] into the DATAPOINTS/BDRVR form that the Cadence
Allegro SI time-domain engine (tlsim) consumes.

signoise normally generates this block from the device model library; doing it
here lets a real IBIS file (V/I tables, ramp, C_comp) drive a tlsim netlist
directly.

Usage:  python ibis2tlsim.py <file.ibs> <model_name> [out.inc]
        python ibis2tlsim.py --list <file.ibs> [substring]
        python ibis2tlsim.py --summary <file.ibs> <model_name>
        python ibis2tlsim.py --batch <file.ibs> <out.inc> <model_name> [<model_name> ...]

The big vendor files (zynquplus.ibs is 124 MB) use the full IBIS 5.1 syntax:
bracketed keyword names ("[Voltage Range]"), engineering units on every number
("1.2000V", "-16.0573mA"), and typ/min/max columns.  All three are handled here.

Conventions confirmed against the engine (details in
references/cadence-cli-invariants.md):

* the emitted tables carry the current the model **sinks** at the node, so the
  IBIS pull-up currents are negated;
* PULL_UP / POWER_CLAMP stay keyed on the IBIS axis (Vpad - VDD) - the engine
  adds the rail itself, pre-shifting them by +VDD doubles the source voltage;
* the engine **ignores** AC_PARAM's RISE_TIME/FALL_TIME: the output edge comes
  from the V/I tables, C_comp and the *stimulus* edge.  Emit them anyway (other
  tools read them) but do not expect them to shape the edge.

Self-test: ``python ibis_to_tlsim.py --selftest`` (writes a synthetic model).
"""

import re
import sys
from pathlib import Path


def _tok(s):
    """Parse one IBIS number token: '-16.0573mA', '1.2000V', '2.69p', 'NA'."""
    if s is None:
        return None
    s = s.strip()
    if not s or s.upper() in ("NA", "N/A"):
        return None
    m = re.match(r"([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)\s*([a-zA-Z]*)", s)
    if not m:
        return None
    v = float(m.group(1))
    suffix = m.group(2).lower()
    # drop trailing unit letters so "1pF" -> "p", "600mV" -> "m", "0.1nH" -> "n"
    suffix = re.sub(r"(ohms|ohm|[fhvas])$", "", suffix)
    mult = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3,
            "k": 1e3, "meg": 1e6, "g": 1e9, "": 1.0}.get(suffix, 1.0)
    return v * mult


def _blocks(path):
    """Yield (name, body) for every [Model] block in an IBIS file."""
    txt = Path(path).read_text(errors="ignore")
    parts = re.split(r"(?im)^\s*\[Model\]\s*", txt)[1:]
    for blk in parts:
        # the model name is the first token on the line that followed [Model]
        nl = blk.find("\n")
        name = blk[:nl].strip() if nl > 0 else blk.strip()
        body = blk if nl < 0 else blk[nl + 1:]
        yield name, body


def parse_ibis(path, model_name):
    # model blocks start at "[Model] <name>" and run to the next keyword line
    for name, body in _blocks(path):
        if name.lower() != model_name.lower():
            continue
        return _model_from(name, body)
    raise SystemExit(f"model '{model_name}' not found in {path}")


def _model_from(name, body):
    m = dict(name=name, raw=body)
    m["model_type"] = _kw(body, "Model_type") or "Output"
    m["c_comp"] = _num(_kw(body, "C_comp")) or 1e-12
    vr = _kw(body, "Voltage Range")
    m["vdd"] = _num(vr) if vr else 1.2
    m["vinl"] = _num(_kw(body, "Vinl"))
    m["vinh"] = _num(_kw(body, "Vinh"))
    for tag, key in (("[Pullup]", "pullup"), ("[Pulldown]", "pulldown"),
                     ("[POWER_clamp]", "pclamp"), ("[GND_clamp]", "gclamp")):
        m[key] = _table(body, tag)
    m["ramp_r"] = _ramp(_kw(body, "dV/dt_r"))
    m["ramp_f"] = _ramp(_kw(body, "dV/dt_f"))
    return m


def parse_many(path, names):
    """Parse several [Model] blocks in one pass (the vendor files are huge)."""
    want = {n.lower(): n for n in names}
    found = {}
    for name, body in _blocks(path):
        if name.lower() in want and name.lower() not in found:
            found[name.lower()] = _model_from(name, body)
            if len(found) == len(want):
                break
    missing = sorted(n for k, n in want.items() if k not in found)
    if missing:
        raise SystemExit(f"models not found in {path}: {missing}")
    return [found[n.lower()] for n in names]


def _kw(body, key):
    # both "[Ramp]" and bare "Ramp" appear across vendors; accept either, and
    # tolerate the "Vinl = 0.76V" style used by the AMD/Xilinx models
    m = re.search(rf"(?im)^\s*\[?\s*{re.escape(key)}\s*\]?\s*=?\s*(.*)$", body)
    return m.group(1).strip() if m else None


def _num(s):
    if not s:
        return None
    return _tok(s.strip().split()[0])


def _table(body, tag):
    """Return [(V, I)] for an IBIS I-V keyword, typ column."""
    name = tag.strip("[]")
    m = re.search(rf"(?im)^\s*\[\s*{re.escape(name)}\s*\]\s*$", body)
    if not m:
        return []
    seg = body[m.end():]
    out = []
    for line in seg.split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("[") or s.startswith("|"):
            if s.startswith("["):
                break
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        v, cur = _tok(parts[0]), _tok(parts[1])
        if v is None or cur is None:
            continue
        out.append((v, cur))
    return out


def _ramp(s):
    """IBIS 'dV/dt_r 0.40/30ps' -> (volts, seconds)"""
    if not s:
        return None
    m = re.match(r"\s*([-+0-9.eE]+)\s*/\s*([-+0-9.eE]+\s*[a-zA-Z]*)", s)
    if not m:
        return None
    dv = _tok(m.group(1)); dt = _tok(m.group(2))
    if dv is None or dt is None or dt <= 0:
        return None
    return dv, dt


def emit(m, sign="physical"):
    """Render the DATAPOINTS block.

    The I-V tables are converted to the simulator's view of the output node:
    a current that the model pushes into the node as a function of the node
    voltage.  IBIS keys [Pullup] on (Vpad - VDD) and [Pulldown] on Vpad, both
    with 'current into the device' sign convention.
    """
    vdd = m["vdd"]
    o = []
    o.append(f"DATAPOINTS BDRVR {m['name']}")
    o.append("MODEL_VERSION=2.0")
    o.append(f"PullUpReference={vdd:g}")
    o.append("PullDownReference=0")
    o.append(f"PowerClampReference={vdd:g}")
    o.append("GroundClampReference=0")
    # AC parameters: the IBIS ramp gives the edge rate of the unloaded buffer
    if m["ramp_r"]:
        dv, dt = m["ramp_r"]
        dVdt = dv / dt
        t_rise = vdd / dVdt
    else:
        dVdt, t_rise = 1.0e9, vdd / 1.0e9
    if m["ramp_f"]:
        dv, dt = m["ramp_f"]
        dVdt_f = dv / dt
        t_fall = vdd / dVdt_f
    else:
        dVdt_f, t_fall = dVdt, t_rise
    o.append("DATAPOINTS AC_PARAM")
    o.append("dVGateOpen=1")
    o.append("dVGateClose=0")
    o.append("ENABLE_ON_VOLT=1")
    o.append("ENABLE_OFF_VOLT=0")
    o.append(f"RISE_TIME={t_rise:.6e}")
    o.append(f"FALL_TIME={t_fall:.6e}")
    o.append(f"dVdtr={dVdt:.6e}")
    o.append(f"dVdtf={dVdt_f:.6e}")
    o.append("END AC_PARAM")
    # thresholds: use the IBIS receiver thresholds when present, else DDR4 ratio
    vinl = m["vinl"] if m["vinl"] is not None else 0.4 * vdd
    vinh = m["vinh"] if m["vinh"] is not None else 0.6 * vdd
    o.append("DATAPOINTS LOGIC_PARAM")
    o.append(f"VHI_MIN={vinh:g}")
    o.append(f"VLO_MAX={vinl:g}")
    o.append("END LOGIC_PARAM")

    def tbl(tag, rows):
        if not rows:
            return
        o.append(f"DATAPOINTS VI {tag}")
        for v, cur in rows:
            o.append(f"{v:.6e} {cur:.6e}")
        o.append(f"END VI {tag}")

    # Two conventions were pinned down experimentally against tlsim:
    #  * the tables carry the current the model SINKS at the node (positive =
    #    current leaving the node into the model) -> IBIS currents are negated
    #  * PULL_UP / POWER_CLAMP stay keyed on the IBIS axis (Vpad - VDD); tlsim
    #    adds the power rail itself.  Pre-shifting them by +VDD doubles the
    #    source voltage (measured: 2.4 V open circuit instead of 1.2 V).
    if m["pullup"]:
        rows = sorted((v, -cur) for v, cur in m["pullup"])
        tbl("PULL_UP", rows)
    if m["pulldown"]:
        rows = sorted((v, cur) for v, cur in m["pulldown"])
        tbl("pull_down", rows)
    if m["pclamp"]:
        tbl("POWER_CLAMP", sorted((v, cur) for v, cur in m["pclamp"]))
    if m["gclamp"]:
        tbl("ground_clamp", sorted((v, cur) for v, cur in m["gclamp"]))
    o.append("DATAPOINTS PARASITICS")
    o.append(f"C_COMP={m['c_comp']:.6e}")
    o.append("END PARASITICS")
    o.append(f"END BDRVR {m['name']}")
    return "\n".join(o) + "\n"


def waveforms(body):
    """[(kind, fixture, [(t, v_typ), ...])] for the IBIS V-t tables."""
    out = []
    for kind in ("Rising Waveform", "Falling Waveform"):
        for m in re.finditer(rf"(?im)^\s*\[\s*{kind}\s*\]\s*$", body):
            seg = body[m.end():]
            fx = re.search(r"(?im)^\s*R_fixture\s*=\s*([0-9.eE+-]+)", seg)
            vf = re.search(r"(?im)^\s*V_fixture\s*=\s*([0-9.eE+-]+[a-zA-Z]*)", seg)
            rows = []
            for line in seg.split("\n"):
                s = line.strip()
                if not s:
                    continue
                if s.startswith("["):
                    break
                if s.startswith("|"):
                    continue
                parts = s.split()
                if len(parts) < 2:
                    continue
                t = _tok(parts[0])
                v = _tok(parts[1])
                if t is None or v is None:
                    continue
                rows.append((t, v))
            if rows:
                out.append(dict(kind=kind, r_fixture=_tok(fx.group(1)) if fx else None,
                                v_fixture=_tok(vf.group(1)) if vf else None, rows=rows))
    return out


def edge_time(rows, frac_lo=0.2, frac_hi=0.8):
    """Transition time between frac_lo and frac_hi of the final swing."""
    v0, v1 = rows[0][1], rows[-1][1]
    span = v1 - v0
    if abs(span) < 1e-12:
        return None, None, None
    lo, hi = v0 + frac_lo * span, v0 + frac_hi * span

    def cross(level):
        for (t0, a), (t1, b) in zip(rows, rows[1:]):
            if (a - level) * (b - level) <= 0 and a != b:
                return t0 + (t1 - t0) * (level - a) / (b - a)
        return None
    a, b = cross(lo), cross(hi)
    if a is None or b is None:
        return None, None, span
    return abs(b - a), (a + b) / 2, span


_SYNTHETIC = """
[IBIS Ver] 5.1
[File name] synthetic.ibs
[Component] demo
[Model] DEMO_OUT40
Model_type I/O
C_comp 2.5pF
[Voltage Range] 1.2000V
Vinl = 0.76V
Vinh = 0.92V
[dV/dt_r] 0.40/200ps
[dV/dt_f] 0.40/220ps
[Pullup]
| Vpad-VDD  I(typ)
-1.2V -45mA
 0.0V  20mA
 1.2V  0.0A
[Pulldown]
| Vpad  I(typ)
-1.2V -16mA
 0.0V  0.0A
 1.2V  49mA
"""


def _selftest():
    """Parse + emit a synthetic model, so the converter can be checked offline."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "synthetic.ibs"
        p.write_text(_SYNTHETIC, encoding="ascii")
        m = parse_ibis(str(p), "DEMO_OUT40")
        assert abs(m["vdd"] - 1.2) < 1e-9, m["vdd"]
        assert abs(m["vinl"] - 0.76) < 1e-9 and abs(m["vinh"] - 0.92) < 1e-9
        assert abs(m["c_comp"] - 2.5e-12) < 1e-15, m["c_comp"]
        assert len(m["pullup"]) == 3 and len(m["pulldown"]) == 3
        txt = emit(m)
        assert "DATAPOINTS BDRVR DEMO_OUT40" in txt
        assert "C_COMP=2.500000e-12" in txt
        # IBIS pull-up current -45 mA at Vpad-VDD = -1.2 V must come out as
        # +45 mA into the node (the engine's sign convention)
        assert "4.500000e-02" in txt and "-4.500000e-02" not in txt, \
            "pull-up currents must be negated"
        print("ibis_to_tlsim.py self-test OK: parse + emit")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--selftest":
        _selftest()
        raise SystemExit(0)
    if not argv:
        raise SystemExit(__doc__)
    if argv[0] == "--list":
        pat = argv[2].lower() if len(argv) > 2 else None
        names = [n for n, _ in _blocks(argv[1])]
        for n in names:
            if pat is None or pat in n.lower():
                print(n)
        print(f"# {len(names)} models total", file=sys.stderr)
        raise SystemExit(0)
    if argv[0] == "--summary":
        m = parse_ibis(argv[1], argv[2])
        for k in ("name", "model_type", "vdd", "vinl", "vinh", "c_comp",
                  "ramp_r", "ramp_f"):
            print(f"{k:12s} {m[k]}")
        for k in ("pullup", "pulldown", "pclamp", "gclamp"):
            rows = m[k]
            if rows:
                print(f"{k:12s} {len(rows)} rows  V=[{rows[0][0]:.4g},{rows[-1][0]:.4g}] "
                      f"I=[{min(r[1] for r in rows):.4g},{max(r[1] for r in rows):.4g}]")
        if m["pullup"]:
            print("  first pulldown rows:", [(round(v, 4), round(c, 6)) for v, c in m["pulldown"][:3]])
            print("  last  pulldown rows:", [(round(v, 4), round(c, 6)) for v, c in m["pulldown"][-3:]])
            print("  first pullup   rows:", [(round(v, 4), round(c, 6)) for v, c in m["pullup"][:3]])
            print("  last  pullup   rows:", [(round(v, 4), round(c, 6)) for v, c in m["pullup"][-3:]])
        raise SystemExit(0)
    if argv[0] == "--dump":
        for name, body in _blocks(argv[1]):
            if name.lower() == argv[2].lower():
                print(f"[Model] {name}")
                print(body)
                break
        raise SystemExit(0)
    if argv[0] == "--wave":
        for name, body in _blocks(argv[1]):
            if name.lower() != argv[2].lower():
                continue
            r = _kw(body, "dV/dt_r")
            f = _kw(body, "dV/dt_f")
            print(f"[Model] {name}   dV/dt_r = {r}   dV/dt_f = {f}")
            for w in waveforms(body):
                dt, tmid, span = edge_time(w["rows"])
                print(f"  {w['kind']:16s} R={w['r_fixture']} V_fix={w['v_fixture']} "
                      f"rows={len(w['rows'])} t=[{w['rows'][0][0]*1e9:.3f},{w['rows'][-1][0]*1e9:.3f}] ns "
                      f"V=[{w['rows'][0][1]:.4f},{w['rows'][-1][1]:.4f}] V "
                      f"20-80%={None if dt is None else round(dt*1e12,1)} ps")
            break
        raise SystemExit(0)
    if argv[0] == "--batch":
        ibs, out, models = argv[1], Path(argv[2]), argv[3:]
        txt = [emit(m) for m in parse_many(ibs, models)]
        out.write_text("\n".join(txt), encoding="ascii")
        print(f"wrote {out} ({len(models)} models, {out.stat().st_size} bytes)")
        raise SystemExit(0)
    ibs, model = argv[0], argv[1]
    out = Path(argv[2]) if len(argv) > 2 else None
    m = parse_ibis(ibs, model)
    txt = emit(m)
    if out:
        out.write_text(txt, encoding="ascii")
        print(f"wrote {out}")
    print(txt)
