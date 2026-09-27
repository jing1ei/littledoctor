"""`littledocter.py selftest` — proves the parser still reads every report shape we know.

The fixtures in ``knowledge/parser_fixtures.yaml`` are the contract. When a new
report format shows up and the parser misses it, add the line there first, watch
it fail, then fix ``src/parse.py``.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import parse

FIXTURES = Path(__file__).resolve().parent.parent / "knowledge" / "parser_fixtures.yaml"


def run(verbose: bool = False) -> int:
    spec = yaml.safe_load(FIXTURES.read_text(encoding="utf-8")) or {}
    fails: list[str] = []
    total = 0

    for case in spec.get("rows", []):
        total += 1
        rows = parse.parse_rows(case["line"])
        got = [[r.name, r.value] for r in rows]
        for want in case["expect"]:
            if not any(g[0] == want[0] and g[1].replace(" ", "") == want[1] for g in got):
                fails.append(f"rows: {case['line']!r}\n    want {want}  got {got}")
        if verbose:
            print(f"  rows  {case['line'][:52]:<54} -> {got}")

    for line in spec.get("none", []):
        total += 1
        rows = parse.parse_rows(line)
        if rows:
            fails.append(f"none: {line!r} should yield nothing, got "
                         f"{[(r.name, r.value) for r in rows]}")

    for case in spec.get("dates", []):
        total += 1
        got = parse.find_date(case["text"])
        if got != case["expect"]:
            fails.append(f"date: {case['text']!r} want {case['expect']} got {got}")

    for case in spec.get("impressions", []):
        total += 1
        got = parse.classify_impression(case["text"])
        if got != case["expect"]:
            fails.append(f"impression: {case['text']!r} want {case['expect']} got {got}")

    for case in spec.get("modality", []):
        total += 1
        got = parse.detect_modality(case["text"])
        if got != case["expect"]:
            fails.append(f"modality: {case['text']!r} want {case['expect']} got {got}")

    for case in spec.get("conclusions", []):
        total += 1
        got = [c["text"] for c in parse.parse_conclusions(case["text"])]
        for want in case.get("expect_contains", []):
            if not any(want in g for g in got):
                fails.append(f"conclusion: want {want!r} in {got}")

    if fails:
        print(f"selftest: {len(fails)} failure(s) across {total} cases\n")
        for f in fails:
            print("  ✗ " + f)
        return 1
    print(f"selftest: {total} parser cases pass")
    return 0
