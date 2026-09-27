"""Command line interface.

    python3 littledocter.py init                     # create data/profile.yaml + reports/
    python3 littledocter.py demo                     # build the page from the sample record
    python3 littledocter.py run                      # ingest -> analyse -> render
    python3 littledocter.py ingest                   # parse reports/ into data/measurements.json
    python3 littledocter.py analyse                  # data/measurements.json -> data/analysis.json
    python3 littledocter.py render                   # data/analysis.json -> site/index.html
    python3 littledocter.py check                    # knowledge-base + parser self-test
    python3 littledocter.py marker ferritin          # explain one marker + your own history
    python3 littledocter.py trend liver              # since when, and where it is heading
    python3 littledocter.py search 甲状腺 结节        # grep the original reports
    python3 littledocter.py docs                     # what is on file, one line each
    python3 littledocter.py selftest                 # parser fixtures only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

import yaml

from .analyze import analyze, band_text
from .ingest import ingest_dir, _prune_pages
from .storage import atomic_write, disjoint, output_lock
from .kb import kb
from .render import render_site
from . import __version__

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "reports": ROOT / "reports",
    "data": ROOT / "data",
    "site": ROOT / "site",
    "profile": ROOT / "data" / "profile.yaml",
}

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "0": "\033[0m"}
# Escape codes are for a person looking at a terminal. Piped into a file, read by
# another program or run with NO_COLOR set, they are noise — so drop them.
if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
    C = {k: "" for k in C}


def rel(path: Path) -> str:
    """Path shown relative to the project when possible, absolute otherwise."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_profile(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        example = path.parent / "profile.example.yaml"
        hint = (f"\n  copy {rel(example)} to {rel(path)} and edit it"
                if example.exists() else "")
        print(f"{C['y']}! no profile at {rel(path)} — reading without one.\n"
              f"  Sex-specific ranges and age-based screening will be left out."
              f"{hint}{C['0']}")
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as err:
        where = getattr(getattr(err, "problem_mark", None), "line", None)
        line = f" near line {where + 1}" if isinstance(where, int) else ""
        sys.exit(f"{C['r']}{rel(path)} is not valid YAML{line}.{C['0']}\n"
                 f"  {getattr(err, 'problem', err)}\n"
                 f"  {C['dim']}Most often a stray ':' inside a sentence — put the "
                 f"whole line in \"quotes\".{C['0']}")
    except OSError as err:
        sys.exit(f"{C['r']}cannot read {rel(path)}: {err}{C['0']}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(f"{C['y']}! {rel(path)} should be a mapping of fields "
              f"(name:, sex:, birth_date: …) — ignoring it{C['0']}")
        return {}
    return data


def cmd_ingest(args) -> dict:
    K = kb()
    reports = Path(args.reports)
    if not reports.is_dir():
        sys.exit(f"{C['r']}no such folder: {reports}{C['0']}")
    print(f"{C['b']}› ingesting{C['0']} {args.reports}")
    records = ingest_dir(reports, K, use_vision=not args.no_vision,
                         pages_dir=Path(args.data) / "pages",
                         cache_dir=Path(args.data) / "cache" / "vision")
    if not records["measurements"] and not records["exams"]:
        sys.exit(
            f"{C['y']}! nothing readable in {rel(reports)} — refusing to overwrite "
            f"existing data.\n"
            f"  Drop your reports (PDF / photo / .txt / .csv / .yaml) in there, or try\n"
            f"    python3 littledocter.py run --reports sample_reports{C['0']}")
    out = Path(args.data) / "measurements.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out, json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False))
    _prune_pages(Path(args.data) / "pages", records["documents"])
    for d in records["documents"]:
        tag = f"{d['measurements']:>3} labs  {d['exams']:>2} exams"
        colour = C["g"] if d["measurements"] or d["exams"] else C["dim"]
        print(f"  {colour}{tag}{C['0']}  {d['file']}  {C['dim']}[{d['kind']}]{C['0']}")
    print(f"  → {len(records['measurements'])} measurements, "
          f"{len(records['exams'])} exam findings → {rel(out)}")
    return records


def cmd_analyse(args) -> dict:
    src = Path(args.data) / "measurements.json"
    if not src.exists():
        sys.exit(f"{C['r']}no measurements.json — run `ingest` first{C['0']}")
    records = json.loads(src.read_text(encoding="utf-8"))
    profile = load_profile(Path(args.profile))
    result = analyze(records, profile, kb(), today=args.today)
    out = Path(args.data) / "analysis.json"
    atomic_write(out, json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    s = result["summary"]
    print(f"{C['b']}› analysis{C['0']}  {s['markers_tracked']} markers · "
          f"{s['visits']} dates · {s['years_covered']} yrs · "
          f"certainty {s['certainty'] * 100:.0f}%")
    for sysx in result["systems"]:
        icon = {"attention": f"{C['r']}●", "managed": f"{C['b']}◍",
                "watch": f"{C['y']}●", "steady": f"{C['g']}●",
                "unknown": f"{C['dim']}○"}.get(
            sysx.get("display_state", sysx["status"]), f"{C['dim']}·")
        care = " · known, under care" if sysx.get("under_care") else ""
        print(f"  {icon}{C['0']} {sysx['name']:<32} {sysx['certainty_label']:<12}"
              f" {C['dim']}{sysx['certainty'] * 100:>3.0f}%{care}{C['0']}")
    for w in result.get("kb_warnings", []):
        print(f"  {C['y']}! {w}{C['0']}")
    print(f"  {len(result['findings'])} findings · {len(result['actions'])} suggested next steps")
    return result


def cmd_render(args) -> Path:
    src = Path(args.data) / "analysis.json"
    if not src.exists():
        sys.exit(f"{C['r']}no analysis.json — run `analyse` first{C['0']}")
    data = json.loads(src.read_text(encoding="utf-8"))
    written = render_site(data, Path(args.site),
                          pages_dir=Path(args.data) / "pages",
                          reports_dir=Path(args.reports),
                          include_originals=args.with_originals)
    print(f"{C['b']}› page{C['0']}    {written['index']}")
    print(f"{C['b']}› archive{C['0']} {written['archive']}  "
          f"{C['dim']}[{len(data.get('documents', []))} documents"
          f"{', originals copied' if 'reports' in written else ''}]{C['0']}")
    if getattr(args, "open", False):
        if not webbrowser.open(written["index"].resolve().as_uri()):
            print(f"Could not open a browser. Open {written['index']} manually.")
    return written["index"]


def cmd_run(args) -> None:
    # Validate profile syntax before replacing the last successful ingestion.
    load_profile(Path(args.profile))
    cmd_ingest(args)
    cmd_analyse(args)
    cmd_render(args)


def cmd_init(args) -> None:
    """Make the two things a first-time reader needs: a profile and a folder."""
    reports = Path(args.reports)
    reports.mkdir(parents=True, exist_ok=True)
    profile = Path(args.profile)
    if profile.exists():
        print(f"  {C['dim']}profile already there: {rel(profile)}{C['0']}")
    else:
        profile.parent.mkdir(parents=True, exist_ok=True)
        # The example belongs to the demo patient, not the new user.
        personal = {"name": "", "sex": None, "birth_date": None, "demo": False,
                    "conditions": [], "medications": [], "family_history": [],
                    "flags": {}, "notes": ""}
        atomic_write(profile,
            "# Fill in your details; leave unknown fields empty.\n"
            "# sex: female | male; birth_date: YYYY-MM-DD\n"
            "# Screening reminders are off by default. See docs/CLINICAL_SCOPE.md.\n"
            + yaml.safe_dump(personal, allow_unicode=True, sort_keys=False))
        print(f"{C['g']}› created{C['0']} {rel(profile)}  "
              f"{C['dim']}(blank personal profile){C['0']}")
    print(f"  {C['dim']}reports folder: {rel(reports)}{C['0']}")
    print(f"\n{C['b']}next{C['0']}")
    print("  1. open the profile and fill in sex, birth date, and anything you")
    print("     already live with under `conditions:`")
    print(f"  2. drop your reports into {rel(reports)}  (PDF, photo, .txt, .csv, .yaml)")
    print("  3. python3 littledocter.py run --no-vision --open")
    print(f"  4. open {rel(Path(args.site) / 'index.html')} in your browser")
    print(f"  {C['dim']}or look around first: python3 littledocter.py demo{C['0']}")


def cmd_demo(args) -> None:
    """The whole thing, on the bundled sample record.

    The demo keeps its own working folder (``data/demo``) so it can never
    overwrite a real reading in ``data/``. The page it writes is the usual
    ``site/`` one, which is regenerated from your own reports the moment you run
    ``python3 littledocter.py run`` again.
    """
    args.reports = str(ROOT / "sample_reports")
    args.profile = str(ROOT / "data" / "profile.example.yaml")
    if str(Path(args.data).resolve()) == str(DEFAULTS["data"].resolve()):
        args.data = str(DEFAULTS["data"] / "demo")
    print(f"{C['dim']}demo: sample_reports + the example profile → {rel(Path(args.data))} "
          f"and {rel(Path(args.site))}; your own data/profile.yaml is untouched{C['0']}")
    cmd_run(args)


def cmd_check(args) -> None:
    K = kb()
    print(f"{C['b']}knowledge base{C['0']}")
    print(f"  markers  {len(K.markers)}  (derived: "
          f"{sum(1 for m in K.markers.values() if m.derived)})")
    print(f"  exams    {len(K.exams)}")
    print(f"  systems  {len(K.systems)}")
    print(f"  rules    {len(K.rules)}")
    print(f"  schedule {len(K.schedule)}")
    print(f"  aliases  {len(K._alias_marker)}")
    if K.alias_conflicts:
        print(f"  {C['y']}ambiguous aliases dropped: "
              f"{', '.join(sorted(K.alias_conflicts))}{C['0']}")
    for w in getattr(K, "warnings", []):
        print(f"  {C['y']}! {w}{C['0']}")
    for r in K.rules:
        for nxt in r.get("next", []):
            t = nxt.get("test")
            if t not in K.markers and t not in K.exams:
                print(f"  {C['r']}! rule {r['id']} -> unknown test '{t}'{C['0']}")
    # a marker that names a system but is on none of its panels would still be
    # shown, yet nobody chose where — worth saying out loud while editing the KB
    listed = {m for s in K.systems for m in
              (list(s.get("core", []) or []) + list(s.get("extended", []) or []))}
    sys_ids = {s["id"] for s in K.systems}
    for mid, m in sorted(K.markers.items()):
        if m.system and m.system not in sys_ids:
            print(f"  {C['r']}! marker '{mid}' -> unknown system '{m.system}'{C['0']}")
        elif mid not in listed:
            print(f"  {C['y']}! marker '{mid}' is on no panel of "
                  f"'{m.system}'{C['0']}")
    from . import selftest
    failed = selftest.run()
    for error in K.errors:
        print(f"  {C['r']}! {error}{C['0']}")
    if failed or K.errors:
        sys.exit(1)
    print(f"{C['g']}ok{C['0']}")


def cmd_marker(args) -> None:
    K = kb()
    mid = K.resolve_marker(args.name) or args.name
    m = K.markers.get(mid)
    if not m:
        sys.exit(f"unknown marker '{args.name}'")
    print(f"{C['b']}{m.name}{C['0']} · {m.name_zh}  [{mid}]  {m.system}")
    print(f"  unit {m.unit or '—'} · tier {m.tier} · stays current ~{m.fresh_months} months")
    profile = load_profile(Path(args.profile))
    print(f"  band {band_text(m, profile.get('sex')) or '—'}")
    print(f"  {m.note}")
    path = Path(args.data) / "analysis.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        st = data["markers"].get(mid)
        if st:
            print(f"{C['b']}  your record{C['0']}")
            for p in st["history"]:
                v = p.get("qualitative") or m.fmt(p.get("value"))
                print(f"    {p['date']}   {v} {m.unit}")
            print(f"    status {st['status']} ({st['side'] or 'in band'}) · "
                  f"trend {st['trend']['direction']} {st['trend']['pct']:+.1f}%")


def cmd_selftest(args) -> None:
    from . import selftest
    code = selftest.run(verbose=False)
    if code:
        sys.exit(code)


def _load_analysis(args) -> dict:
    path = Path(args.data) / "analysis.json"
    if not path.exists():
        sys.exit(f"{C['r']}no analysis.json — run `analyse` first{C['0']}")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_trend(args) -> None:
    """Since when, and where is it heading — for a system or a single marker."""
    data = _load_analysis(args)
    trends = data.get("trends", {})
    key = (args.name or "").lower()
    systems = {s["id"]: s for s in data.get("systems", [])}

    if not key:                                       # the whole picture, ranked
        print(f"{C['b']}trends{C['0']}")
        rank = {"worsening": 0, "mixed": 1, "new": 2, "drifting": 3, "stable": 4,
                "improving": 5, "resolved": 6, "single": 7, "unknown": 8}
        rows = sorted(trends.get("systems", {}).values(),
                      key=lambda t: (rank.get(t["trajectory"], 9), t["id"]))
        for t in rows:
            colour = {"worsening": C["r"], "mixed": C["y"], "new": C["y"],
                      "drifting": C["y"], "improving": C["g"],
                      "resolved": C["g"]}.get(t["trajectory"], C["dim"])
            name = (systems.get(t["id"], {}) or {}).get("name", t["id"])
            print(f"  {colour}{t['trajectory_word']:<14}{C['0']} {name:<30}"
                  f" {C['dim']}{t['headline']}{C['0']}")
        print(f"{C['dim']}  python3 littledocter.py trend <system|marker> for the story{C['0']}")
        return

    K = kb()
    sid = next((s for s in systems if s == key or systems[s]["name"].lower() == key), None)
    if sid:
        t, s = trends["systems"][sid], systems[sid]
        print(f"{C['b']}{s['name']}{C['0']} · {s['name_zh']}  [{sid}]")
        span = f" over {t['years_tracked']:g}y" if t.get("years_tracked") else ""
        print(f"  {t['trajectory_word']} · {t['headline']} · {t['visits']} "
              f"dated record{'' if t['visits'] == 1 else 's'}{span}")
        for line in t.get("story", []):
            print(f"  {line}")
        if t.get("milestones"):
            print(f"{C['b']}  milestones{C['0']}")
            for m in t["milestones"]:
                dot = {2: f"{C['r']}●", 1: f"{C['y']}●"}.get(m["sev"], f"{C['g']}●")
                print(f"    {m['date']}  {dot}{C['0']} {m['detail'] or m['label']}")
        for q in t.get("quotes", []):
            print(f"    {C['dim']}{q['date']}  “{q['text']}”  [{q['file']}]{C['0']}")
        return

    mid = K.resolve_marker(args.name) or args.name
    st = (trends.get("markers") or {}).get(mid)
    if not st:
        sys.exit(f"unknown system or marker '{args.name}' "
                 f"(or nothing on file for it)")
    m = K.markers.get(mid)
    print(f"{C['b']}{st.get('name') or mid}{C['0']}  [{mid}]"
          f"{'  ' + m.system if m else ''}")
    print(f"  {st['trajectory_word']} · {st['sentence'] or 'no trend to read yet'}")
    for p in st.get("series", []):
        icon = {"out_of_range": f"{C['r']}●", "suboptimal": f"{C['y']}●"}.get(
            p["status"], f"{C['g']}●")
        val = p["value"] if p["value"] is not None else "—"
        print(f"    {p['date']}  {icon}{C['0']} {val} {st.get('unit','')}")


def _docs(args) -> list[dict]:
    """Documents come from analysis.json when it exists, measurements.json otherwise."""
    for name in ("analysis.json", "measurements.json"):
        path = Path(args.data) / name
        if path.exists():
            docs = json.loads(path.read_text(encoding="utf-8")).get("documents")
            if docs:
                return docs
    sys.exit(f"{C['r']}no documents on file — run `ingest` first{C['0']}")


def cmd_docs(args) -> None:
    docs = sorted(_docs(args), key=lambda d: (d.get("date") or "", d["file"]))
    print(f"{C['b']}on file{C['0']}  {len(docs)} documents")
    for d in docs:
        flag = (f"{C['r']}finding{C['0']}" if any(
            c.get("status") == "abnormal" for c in d.get("conclusions") or [])
            else f"{C['dim']}—      {C['0']}")
        print(f"  {d['id']}  {d.get('date') or '        '}  {flag}  "
              f"{d['measurements']:>3} labs {d['exams']:>2} imp  "
              f"{C['dim']}{d['kind']:<9}{C['0']} {d['file']}")
        for w in d.get("warnings", []):
            print(f"        {C['y']}! {w}{C['0']}")


def cmd_search(args) -> None:
    """Grep the original reports: printed words, conclusions, marker names."""
    docs = _docs(args)
    terms = [t.lower() for t in args.terms if t.strip()]
    if not terms:
        return cmd_docs(args)
    K = kb()
    hits = 0
    for d in sorted(docs, key=lambda x: (x.get("date") or ""), reverse=True):
        names = " ".join(
            f"{(K.markers[m].name if m in K.markers else m)} "
            f"{(K.markers[m].name_zh if m in K.markers else '')}"
            for m in d.get("markers", []))
        hay = " ".join([d["file"], d.get("title") or "", d.get("date") or "",
                        d.get("kind") or "", d.get("modality") or "", names,
                        " ".join(c["text"] for c in d.get("conclusions") or []),
                        d.get("text") or ""]).lower()
        if not all(t in hay for t in terms):
            continue
        hits += 1
        print(f"{C['b']}{d.get('date') or 'undated'}{C['0']}  {d.get('title') or d['name']}"
              f"  {C['dim']}{d['file']} [{d['kind']}]{C['0']}")
        text = d.get("text") or ""
        low = text.lower()
        shown = 0
        for t in terms:
            at = low.find(t)
            while at >= 0 and shown < args.context:
                a, b = max(0, at - 60), min(len(text), at + 90)
                line = " ".join(text[a:b].split())
                print(f"    {C['dim']}…{C['0']} {line} {C['dim']}…{C['0']}")
                shown += 1
                at = low.find(t, at + max(len(t), 1) + 40)
        for c in d.get("conclusions") or []:
            if any(t in c["text"].lower() for t in terms):
                mark = C["r"] if c.get("status") == "abnormal" else C["dim"]
                print(f"    {mark}“{c['text']}”{C['0']}")
    print(f"{C['dim']}{hits} of {len(docs)} documents match "
          f"{' + '.join(terms)}{C['0']}")


# Suppressed subparser defaults preserve shared options passed before the command.
OPTION_DEFAULTS = {
    "reports": str(DEFAULTS["reports"]), "data": str(DEFAULTS["data"]),
    "site": str(DEFAULTS["site"]), "profile": str(DEFAULTS["profile"]),
    "today": None, "no_vision": False, "with_originals": False, "open": False,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    S = argparse.SUPPRESS
    common.add_argument("--reports", default=S,
                        help=f"folder of reports to read (default {rel(DEFAULTS['reports'])})")
    common.add_argument("--data", default=S,
                        help=f"working folder for parsed data (default {rel(DEFAULTS['data'])})")
    common.add_argument("--site", default=S,
                        help=f"where the page is written (default {rel(DEFAULTS['site'])})")
    common.add_argument("--profile", default=S,
                        help=f"who this is about (default {rel(DEFAULTS['profile'])})")
    common.add_argument("--today", default=S, help="evaluate as of this date (YYYY-MM-DD)")
    common.add_argument("--no-vision", default=S, action="store_true",
                        help="skip vision-model OCR for images / scanned PDFs")
    common.add_argument("--open", default=S, action="store_true",
                        help="open the generated homepage after demo, run, or render")
    common.add_argument("--with-originals", default=S, action="store_true",
                        help="copy the original report files into the site so the "
                             "archive can open them")

    p = argparse.ArgumentParser(prog="littledocter", description=__doc__, parents=[common],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"littledocter {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", parents=[common]).set_defaults(fn=cmd_run)
    sub.add_parser("init", parents=[common],
                   help="create data/profile.yaml and reports/").set_defaults(fn=cmd_init)
    sub.add_parser("demo", parents=[common],
                   help="build the page from the bundled sample record"
                   ).set_defaults(fn=cmd_demo)
    sub.add_parser("ingest", parents=[common]).set_defaults(fn=cmd_ingest)
    for name in ("analyse", "analyze"):
        sub.add_parser(name, parents=[common]).set_defaults(fn=cmd_analyse)
    sub.add_parser("render", parents=[common]).set_defaults(fn=cmd_render)
    sub.add_parser("check", parents=[common]).set_defaults(fn=cmd_check)
    sub.add_parser("selftest", parents=[common]).set_defaults(fn=cmd_selftest)
    sub.add_parser("docs", parents=[common]).set_defaults(fn=cmd_docs)
    mk = sub.add_parser("marker", parents=[common])
    mk.add_argument("name")
    mk.set_defaults(fn=cmd_marker)
    tr = sub.add_parser("trend", parents=[common])
    tr.add_argument("name", nargs="?", default="",
                    help="a system (liver) or a marker (ferritin); omit for all systems")
    tr.set_defaults(fn=cmd_trend)
    se = sub.add_parser("search", parents=[common])
    se.add_argument("terms", nargs="*", help="all terms must appear in the document")
    se.add_argument("--context", type=int, default=3,
                    help="passages to quote per document (default 3)")
    se.set_defaults(fn=cmd_search)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for key, value in OPTION_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    for key in ("reports", "data", "site", "profile"):
        setattr(args, key, str(Path(getattr(args, key)).expanduser()))
    if not getattr(args, "fn", None):
        args.fn = cmd_run
    if getattr(args, "today", None):
        try:                       # a wrong --today silently ages every result
            args.today = datetime.strptime(args.today, "%Y-%m-%d").date().isoformat()
        except ValueError:
            sys.exit(f"{C['r']}--today must be a date like 2026-09-05, "
                     f"not '{args.today}'{C['0']}")
    try:
        if args.fn == cmd_demo:
            args.reports = str(ROOT / "sample_reports")
            args.profile = str(ROOT / "data" / "profile.example.yaml")
            if Path(args.data).resolve() == DEFAULTS["data"].resolve():
                args.data = str(DEFAULTS["data"] / "demo")
        writes = args.fn in (cmd_run, cmd_demo, cmd_ingest, cmd_analyse, cmd_render, cmd_init)
        with ExitStack() as stack:
            if writes:
                data, site, reports = (Path(getattr(args, key)).resolve() for key in ("data", "site", "reports"))
                disjoint(data, reports)
                disjoint(site, reports)
                if site == data or site in data.parents:
                    raise ValueError("site cannot contain or equal the working data folder")
                profile = Path(args.profile).resolve()
                if profile == site or site in profile.parents:
                    raise ValueError("profile must be outside the generated site")
                folders = {data}
                if args.fn in (cmd_run, cmd_demo, cmd_render):
                    folders.add(site)
                for folder in sorted(folders):
                    stack.enter_context(output_lock(folder))
            args.fn(args)
    except (OSError, ValueError) as exc:
        sys.exit(f"littledocter: {exc}")
    except KeyboardInterrupt:
        sys.exit("littledocter: interrupted; completed files were preserved")
