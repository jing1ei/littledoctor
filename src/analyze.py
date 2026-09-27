"""The analysis engine: status, trend, certainty, pattern findings and gaps."""
from __future__ import annotations

import re
import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .derive import compute_derived, normalise_sex, profile_age
from . import trend as trend_mod
from .kb import KnowledgeBase, kb
from . import __version__

SEV_ORDER = {"urgent": 0, "act": 1, "watch": 2, "info": 3}
PRIO_ORDER = {"essential": 1, "recommended": 2, "optional": 3}

W_CORE, W_EXT, W_EXAM = 1.0, 0.30, 0.35
F_FRESH, F_AGING, F_STALE = 1.0, 0.65, 0.30
CONTEXT_MARKERS = {
    "homa_ir", "fib4", "ast_alt_ratio", "tg_hdl_ratio", "tc_hdl_ratio",
    "neutrophil_lymphocyte_ratio", "hrv", "amh", "bmd_tscore",
    "sleep_quality", "stress_score", "phq9", "gad7",
}


def months_between(a: str, b: str) -> float:
    try:
        d1 = datetime.fromisoformat(a).date()
        d2 = datetime.fromisoformat(b).date()
    except Exception:
        return 999.0
    return abs((d2 - d1).days) / 30.44


# ── per-marker evaluation ────────────────────────────────────────────────────

def status_of(marker, value, qualitative, sex) -> tuple[str, str | None]:
    # Cycle, age and clinical risk cannot be inferred from a scalar value.
    if marker.phase_dependent or marker.id in CONTEXT_MARKERS:
        return "context", None
    if marker.direction == "qualitative":
        if qualitative is None:
            return "context", None
        expected = marker.expected or "negative"
        return ("optimal", None) if qualitative == expected else ("out_of_range", "unexpected")
    if value is None:
        return "context", None
    ref = marker.bounds(sex, "ref")
    lo, hi = ref.get("low"), ref.get("high")
    if hi is not None and value > hi:
        return "out_of_range", "high"
    if lo is not None and value < lo:
        return "out_of_range", "low"
    return ("steady" if (lo is not None or hi is not None) else "context"), None


def band_text(marker, sex, lang: str = "en") -> str:
    if marker.phase_dependent or marker.id in CONTEXT_MARKERS:
        return "clinical context required" if lang == "en" else "需结合临床背景"

    def fmt(b: dict) -> str:
        lo, hi = b.get("low"), b.get("high")
        if lo is not None and hi is not None:
            return f"{marker.fmt(lo)}–{marker.fmt(hi)}"
        if hi is not None:
            return f"≤ {marker.fmt(hi)}"
        if lo is not None:
            return f"≥ {marker.fmt(lo)}"
        return ""
    ref = fmt(marker.bounds(sex, "ref"))
    if ref:
        return f"{'reference' if lang == 'en' else '参考'} {ref}"
    return ""


def trend_of(points: list[dict], years: float = 8.0, today: str | None = None) -> dict:
    pts = [p for p in points if p.get("value") is not None]
    if today:
        pts = [p for p in pts if months_between(p["date"], today) <= years * 12 + 1]
    if len(pts) < 2:
        return {"direction": "flat", "pct": 0.0, "slope": 0.0, "points": len(pts)}
    pts = sorted(pts, key=lambda p: p["date"])
    first, last = pts[0], pts[-1]
    base = abs(first["value"]) or 1e-9
    pct = (last["value"] - first["value"]) / base * 100
    xs = [datetime.fromisoformat(p["date"]).date().toordinal() / 365.25 for p in pts]
    ys = [p["value"] for p in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom) if denom else 0.0
    direction = "up" if pct >= 3 else "down" if pct <= -3 else "flat"
    return {"direction": direction, "pct": round(pct, 1), "slope": round(slope, 4),
            "points": n, "span_years": round(xs[-1] - xs[0], 1)}


def freshness(months: float, fresh_months: int) -> str:
    if months <= fresh_months:
        return "fresh"
    if months <= fresh_months * 1.6:
        return "aging"
    return "stale"


def report_bounds(K, marker, text: str, unit: str) -> dict | None:
    """Recognize only unambiguous numeric intervals; never guess complex lab ranges."""
    text = text.strip().replace("≤", "<=").replace("≥", ">=")
    number = r"(-?\d+(?:\.\d+)?)"
    match = re.fullmatch(number + r"\s*[-–—~～]\s*" + number, text)
    if match:
        bounds = {"low": float(match[1]), "high": float(match[2])}
    else:
        match = re.fullmatch(r"([<>]=?)\s*" + number, text)
        if not match:
            return None
        bounds = {"high" if match[1].startswith("<") else "low": float(match[2])}
        bounds["exclusive"] = match[1] in ("<", ">")
    for key in ("low", "high"):
        if key in bounds:
            value, warning = K.convert(marker, bounds[key], unit)
            if warning:
                return None
            bounds[key] = value
    if "low" in bounds and "high" in bounds and bounds["low"] > bounds["high"]:
        return None
    return bounds


def status_with_bounds(value, bounds: dict) -> tuple[str, str | None]:
    if value is None:
        return "context", None
    for key, side in (("low", "low"), ("high", "high")):
        if key in bounds:
            outside = value < bounds[key] if key == "low" else value > bounds[key]
            if outside or (bounds.get("exclusive") and value == bounds[key]):
                return "out_of_range", side
    return "steady", None


# ── the engine ───────────────────────────────────────────────────────────────

class Analysis:
    def __init__(self, K: KnowledgeBase, records: dict, profile: dict,
                 today: str | None = None):
        self.K = K
        self.profile = dict(profile or {})
        for key in ("conditions", "medications", "family_history"):
            value = self.profile.get(key)
            self.profile[key] = value if isinstance(value, list) else [value] if value else []
        if not isinstance(self.profile.get("flags"), dict):
            self.profile["flags"] = {}
        sex = str(self.profile.get("sex") or "").strip().lower()
        # a profile is written by a person: "Female", "F", "男" all mean something
        self.sex = normalise_sex(sex)
        self.today = today or date.today().isoformat()
        self.age = profile_age(self.profile, self.today)
        # things the reader deserves to know about the reading itself
        self.warnings: list[str] = []
        self.warnings.append("Record coverage is a checklist heuristic, not a health score, diagnostic certainty or a list of tests everyone needs.")
        self.warnings.append("This is not emergency triage: no complete critical-value or symptom assessment is implemented. A reassuring colour cannot rule out urgent illness.")
        if self.profile.get("screening_guideline") != "USPSTF":
            self.warnings.append("Automatic screening is not enabled: select a clinician-reviewed local schedule; the bundled reference requires screening_guideline: USPSTF.")
        if self.age is None or self.age < 18:
            self.warnings.append("Adult pattern rules are withheld without an adult age; pediatric interpretation is not supported.")
        if self.profile.get("flags", {}).get("pregnant") is True:
            self.warnings.append("Pregnancy-specific interpretation is not supported; adult automatic classification and pattern rules are withheld.")
        # markers measured but never listed in their system's panel; collected
        # while systems are built, reported once afterwards
        self.kb_orphans: list[str] = []
        if not self.sex:
            self.warnings.append(
                "no sex in the profile — sex-specific reference ranges and screening "
                "(ferritin, haemoglobin, mammogram, PSA, DXA) were left out"
                + (f"; '{sex}' was not understood" if sex else ""))
        if self.age is None:
            self.warnings.append(
                "no age or birth date in the profile — age-based screening was left out")

        base = []
        for r in records.get("measurements", []):
            if r["date"] > self.today:
                continue
            value = r.get("value")
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                self.warnings.append(f"{r.get('source', '')}: {r['marker']} excluded: invalid numeric value")
                continue
            if "unrecognised" in r.get("note", "") or r.get("unit_unverified"):
                self.warnings.append(f"{r.get('source', '')}: {r['marker']} excluded from interpretation: unverified unit")
                continue
            if any(sign in str(r.get("raw_value", "")) for sign in ("<", ">", "≤", "≥", "＜", "＞")):
                self.warnings.append(f"{r.get('source', '')}: {r['marker']} is a censored result, not an exact value; retained in the source report only")
                continue
            base.append(r)
        self.derived = compute_derived(K, base, self.profile)
        self.measurements = sorted(base + self.derived, key=lambda r: (r["date"], r["marker"]))
        self.exam_records = [r for r in records.get("exams", []) if r["date"] <= self.today]
        used_docs = {r.get("doc") for r in base + self.exam_records}
        self.documents = [d for d in records.get("documents", []) or []
                          if not d.get("date") or d["date"] <= self.today or d.get("id") in used_docs]
        for d in self.documents:
            self.warnings.extend(f"{d['file']}: {w}" for w in d.get("warnings", []))

        self.conditions = self._conditions()
        self.series = self._series()
        self.markers = self._marker_states()
        self.exams = self._exam_states()
        self.findings = self._findings()
        self.systems = self._system_states()
        if self.kb_orphans:
            names = ", ".join(dict.fromkeys(self.kb_orphans))
            self.warnings.append(
                "shown but not part of any panel in knowledge/systems.yaml: "
                + names + " — they are on the page and counted, and the panel "
                "lists could name them explicitly")
        # the history of each marker and system, read before actions are ranked
        trend_docs = [d for d in self.documents if not d.get("date") or d["date"] <= self.today]
        self.trends = trend_mod.build(self.systems, self.markers, self.K, self.sex,
                                      status_of, trend_docs, self.today)
        self.screening = self._screening()
        self.actions = self._actions()

    # -- what the person already knows ------------------------------------
    def _conditions(self) -> list[dict]:
        """The diagnoses a person already lives with, filed under a system.

        A condition may be written as a plain sentence — ``hypertension since
        2011`` — or as a mapping with ``name`` / ``system`` / ``since`` /
        ``managed``. Anything already under a doctor's care is marked as such, so
        the record can say "known, being treated" instead of raising an alarm
        about something the reader has lived with for twenty years.
        """
        out: list[dict] = []
        raw = self.profile.get("conditions") or []
        if isinstance(raw, (str, dict)):
            raw = [raw]
        for item in raw:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("name_zh") or "").strip()
            if not name and not item.get("name_zh"):
                continue
            # "hypertension since 2011", "高血压（2011年起）", "gout (2018)" — the year
            # belongs in `since`, not in the middle of the name on screen
            name, found_year = _split_since(name)
            sid = item.get("system") or self.K.resolve_condition(
                " ".join(str(x) for x in (name, item.get("name_zh") or "")))
            if sid and not any(s.get("id") == sid for s in self.K.systems):
                self.warnings.append(f"condition '{name}': unknown system '{sid}'")
                sid = None
            # a named condition is assumed to be under care unless it says
            # otherwise; nobody lists a diagnosis they have never been told
            managed = item.get("managed")
            since = item.get("since") or found_year
            if since and str(since)[:4].isdigit() and int(str(since)[:4]) > int(self.today[:4]):
                continue
            out.append({
                "name": name or str(item.get("name_zh")),
                "name_zh": _split_since(str(item.get("name_zh") or ""))[0] or name,
                "system": sid, "since": str(since) if since else None,
                "managed": True if managed is None else (managed is True or str(managed).lower() == "true"),
                "note": str(item.get("note") or ""),
                "note_zh": str(item.get("note_zh") or ""),
            })
            if not sid:
                self.warnings.append(
                    f"condition '{name}' could not be matched to a system — add "
                    f"`system:` to it in the profile to see it on the record")
        return out

    # -- series ------------------------------------------------------------
    def _series(self) -> dict[str, list[dict]]:
        s: dict[str, list[dict]] = defaultdict(list)
        for r in self.measurements:
            s[r["marker"]].append({"date": r["date"], "value": r.get("value"),
                                   "qualitative": r.get("qualitative"),
                                   "source": r.get("source", ""),
                                   "doc": r.get("doc"),
                                   "derived": bool(r.get("derived")),
                                   "ref_raw": r.get("ref_raw", ""),
                                   "raw_unit": r.get("raw_unit", r.get("unit", "")),
                                   "note": r.get("note", "")})
        for k in s:
            s[k].sort(key=lambda p: p["date"])
            values_by_date = defaultdict(set)
            for p in s[k]:
                values_by_date[p["date"]].add((p.get("value"), p.get("qualitative")))
            for p in s[k]:
                p["conflict"] = len(values_by_date[p["date"]]) > 1
        return dict(s)

    def _marker_states(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for mid, pts in self.series.items():
            m = self.K.markers.get(mid)
            if not m:
                continue
            last = pts[-1]
            st, side = status_of(m, last.get("value"), last.get("qualitative"), self.sex)
            if self.age is None or self.age < 18 or self.profile.get("flags", {}).get("pregnant") is True:
                st, side = "context", None
            # Printed ranges remain authoritative; unfamiliar syntax needs human review.
            ref_raw = str(last.get("ref_raw") or "").strip()
            reference = report_bounds(self.K, m, ref_raw, last.get("raw_unit", "")) if ref_raw else None
            reference_mismatch = bool(ref_raw) and reference is None
            context_only = m.phase_dependent or m.id in CONTEXT_MARKERS
            if ref_raw and not context_only and self.age is not None and self.age >= 18 and self.profile.get("flags", {}).get("pregnant") is not True:
                st, side = status_with_bounds(last.get("value"), reference) if reference else ("context", None)
            same_day = {(p.get("value"), p.get("qualitative")) for p in pts if p["date"] == last["date"]}
            if len(same_day) > 1:
                st, side = "context", None
                reference_mismatch = True
                self.warnings.append(f"{mid}: conflicting same-day results; interpretation withheld")
            months = months_between(last["date"], self.today)
            out[mid] = {
                "id": mid, "name": m.name, "name_zh": m.name_zh, "system": m.system,
                "unit": m.unit, "tier": m.tier, "note": m.note, "note_zh": m.note_zh,
                "derived": bool(last.get("derived")),
                "value": last.get("value"), "display": (last.get("qualitative")
                                                        or m.fmt(last.get("value"))),
                "qualitative": last.get("qualitative"),
                "date": last["date"], "months_since": round(months, 1),
                "freshness": freshness(months, m.fresh_months),
                "status": st, "side": side,
                "reference_mismatch": reference_mismatch,
                "context_only": context_only or self.age is None or self.age < 18 or self.profile.get("flags", {}).get("pregnant") is True,
                "source_reference": ref_raw,
                "band": f"report reference: {ref_raw} {last.get('raw_unit', '')}" if ref_raw else band_text(m, self.sex),
                "band_zh": f"报告参考范围：{ref_raw} {last.get('raw_unit', '')}" if ref_raw else band_text(m, self.sex, "zh"),
                "band_num": reference or m.bounds(self.sex, "ref"),
                "trend": trend_of(pts, today=self.today),
                "history": [{"date": p["date"], "value": p.get("value"),
                             "qualitative": p.get("qualitative"),
                             "reference_bounds": report_bounds(self.K, m, p.get("ref_raw", ""), p.get("raw_unit", "")) if p.get("ref_raw") else None,
                             "reference_unparsed": bool(p.get("ref_raw")) and report_bounds(self.K, m, p.get("ref_raw", ""), p.get("raw_unit", "")) is None,
                             "conflict": p.get("conflict", False),
                             "doc": p.get("doc")} for p in pts],
                "phase_dependent": m.phase_dependent,
                # BMI and eGFR may be printed on the slip or computed here;
                # only say "computed from" when this reading actually was
                "computed_from": last.get("note", "") if last.get("derived") else "",
            }
        return out

    def _exam_states(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for r in self.exam_records:
            cur = latest.get(r["exam"])
            if not cur or r["date"] > cur["date"] or (
                    r["date"] == cur["date"] and r["status"] == "abnormal"):
                latest[r["exam"]] = r
        out: dict[str, dict] = {}
        for eid, r in latest.items():
            e = self.K.exams.get(eid)
            if not e:
                continue
            months = months_between(r["date"], self.today)
            out[eid] = {"id": eid, "name": e.name, "name_zh": e.name_zh, "system": e.system,
                        "date": r["date"], "impression": r.get("impression", ""),
                        "status": r.get("status", "unclear"),
                        "months_since": round(months, 1),
                        "freshness": freshness(months, e.validity_months),
                        "note": e.note, "note_zh": e.note_zh, "doc": r.get("doc"),
                        "modality": r.get("modality", ""),
                        "history": sorted([
                            {"date": x["date"], "status": x["status"],
                             "impression": x.get("impression", ""), "doc": x.get("doc")}
                            for x in self.exam_records if x["exam"] == eid],
                            key=lambda p: p["date"])}
        return out

    # -- rules -------------------------------------------------------------
    def _eval(self, node: Any) -> bool:
        if node is None:
            return False
        if isinstance(node, list):
            return all(self._eval(n) for n in node)
        if not isinstance(node, dict):
            return False
        if "all" in node:
            if not all(self._eval(n) for n in node["all"]):
                return False
            dates = [self.markers[mid]["date"] for mid in self._supporting_markers(node)
                     if mid in self.markers]
            return not dates or months_between(min(dates), max(dates)) <= 3
        if "any" in node:
            return any(self._eval(n) for n in node["any"])
        if "none" in node:
            return not any(self._eval(n) for n in node["none"])
        return self._leaf(node)

    def _supporting_markers(self, node) -> set[str]:
        if not isinstance(node, dict) or "none" in node:
            return set()
        if "m" in node:
            return {node["m"]} if self._leaf(node) else set()
        children = node.get("all", node.get("any", []))
        return set().union(*(self._supporting_markers(n) for n in children
                             if self._eval(n))) if children else set()

    def _leaf(self, n: dict) -> bool:
        if "flag" in n:
            return self.profile.get("flags", {}).get(n["flag"]) is True
        if "sex" in n:
            return self.sex == n["sex"]
        if "age" in n:
            spec = n["age"]
            return self.age is not None and _cmp(self.age, spec.get("op", ">="), spec.get("v"))
        if "missing" in n:
            t = n["missing"]
            return t not in self.markers and t not in self.exams
        if "present" in n:
            t = n["present"]
            return t in self.markers or t in self.exams
        if "exam" in n:
            st = self.exams.get(n["exam"])
            want = n.get("is", "abnormal")
            if want == "missing":
                return st is None
            if st is None:
                return False
            if want == "stale":
                return st["freshness"] == "stale"
            return st["status"] == want
        mid = n.get("m")
        if not mid:
            return False
        ms = self.markers.get(mid)
        if not ms:
            return False
        if ms["freshness"] == "stale" or ms.get("reference_mismatch") or ms["status"] == "context":
            return False
        if "op" in n:
            return ms["value"] is not None and _cmp(ms["value"], n["op"], n.get("v"))
        if "status" in n:
            want = n["status"]
            if want in ("high", "low"):
                return ms["side"] == want and ms["status"] in ("suboptimal", "out_of_range")
            if want == "any":
                return True
            return ms["status"] == want
        if "is" in n:
            want = n["is"]
            expected = (self.K.markers[mid].expected or "negative")
            if want == "abnormal":
                return ms["qualitative"] is not None and ms["qualitative"] != expected
            if want == "normal":
                return ms["qualitative"] == expected
            return ms["qualitative"] == want
        if "trend" in n:
            tr = ms["trend"]
            if tr["direction"] != n["trend"]:
                return False
            if tr["points"] < 3:
                return False
            if n.get("years") and tr.get("span_years", 0) < min(1.0, n["years"] / 4):
                return False
            return abs(tr["pct"]) >= float(n.get("pct", 0))
        return False

    def _findings(self) -> list[dict]:
        out: list[dict] = []
        for rule in self.K.rules:
            if self.age is None or self.age < 18:
                continue  # These rules have only been reviewed for adults.
            if self.profile.get("flags", {}).get("pregnant") is True:
                continue  # Pregnancy-specific interpretation requires a separate pathway.
            if rule.get("requires_flag") and self.profile.get("flags", {}).get(rule["requires_flag"]) is not True:
                continue
            # Each OR branch is evaluated independently; an unrelated old result
            # must not suppress a current observation.
            try:
                if not self._eval(rule.get("when")):
                    continue
            except Exception as exc:
                self.warnings.append(f"rule {rule.get('id', '?')} could not be evaluated: {exc}")
                continue
            evidence = []
            for mid in sorted(self._supporting_markers(rule.get("when"))):
                ms = self.markers.get(mid)
                if ms and ms["status"] != "context" and ms["freshness"] != "stale" and not ms.get("reference_mismatch"):
                    evidence.append({"id": mid, "name": ms["name"], "name_zh": ms["name_zh"],
                                     "display": ms["display"], "unit": ms["unit"],
                                     "status": ms["status"], "side": ms["side"],
                                     "date": ms["date"], "band": ms["band"]})
                    evidence[-1]["doc"] = next((p.get("doc") for p in reversed(self.series[mid]) if p["date"] == ms["date"]), None)
            out.append({
                "id": rule["id"], "system": rule.get("system", ""),
                "kind": rule.get("kind", "pattern"),
                "severity": rule.get("severity", "info"), "title": rule.get("title", ""),
                "title_zh": rule.get("title_zh", ""),
                "says": " ".join((rule.get("says") or "").split()),
                "says_zh": " ".join((rule.get("says_zh") or "").split()),
                "do": rule.get("do", []) or [], "do_zh": rule.get("do_zh", []) or [],
                "next": rule.get("next", []) or [],
                "evidence": evidence,
            })
        out.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["system"]))
        return out

    # -- systems -----------------------------------------------------------
    def _system_states(self) -> list[dict]:
        out: list[dict] = []
        for s in sorted(self.K.systems, key=lambda x: x.get("order", 99)):
            core = [m for m in s.get("core", []) if self._applies_marker(m)]
            ext = [m for m in s.get("extended", []) if self._applies_marker(m)]
            exams = [e for e in s.get("exams", []) if self._applies_exam(e)]

            num = den = 0.0
            known, unknown = [], []
            for group, weight in ((core, W_CORE), (ext, W_EXT)):
                for mid in group:
                    den += weight
                    ms = self.markers.get(mid)
                    if ms:
                        f = {"fresh": F_FRESH, "aging": F_AGING}.get(ms["freshness"], F_STALE)
                        num += weight * f
                        known.append(mid)
                    else:
                        unknown.append(mid)
            exam_known, exam_unknown = [], []
            for eid in exams:
                den += W_EXAM
                es = self.exams.get(eid)
                if es:
                    f = {"fresh": F_FRESH, "aging": F_AGING}.get(es["freshness"], F_STALE)
                    num += W_EXAM * f
                    exam_known.append(eid)
                else:
                    exam_unknown.append(eid)

            certainty = (num / den) if den else 0.0
            # A result that was actually measured must never fall through the
            # floor. If a marker names this system but the system's panel never
            # listed it, it still belongs on the page — it just does not count
            # towards how complete the intended panel is.
            listed = set(core) | set(ext)
            orphans = sorted(mid for mid, ms in self.markers.items()
                             if ms.get("system") == s["id"] and mid not in listed)
            if orphans:
                known = known + orphans
                self.kb_orphans.extend(orphans)
            core_missing = [m for m in core if m not in self.markers]
            core_stale = [m for m in core
                          if m in self.markers and self.markers[m]["freshness"] == "stale"]

            if certainty >= 0.70 and not core_missing:
                label = "Established"
            elif certainty >= 0.42:
                label = "Partial"
            elif certainty >= 0.15:
                label = "Sparse"
            else:
                label = "Unmapped"
            # "Established" has to mean established *now*. A core test that has
            # gone stale, or one that was never done, is a hole in the picture
            # however many other numbers surround it.
            if label == "Established" and (core_missing or core_stale):
                label = "Partial"

            sys_markers = [self.markers[m] for m in known]
            sys_findings = [f for f in self.findings if f["system"] == s["id"]]
            # gap findings describe missing evidence, not a bodily problem:
            # they must not turn a system red
            worst = min([SEV_ORDER.get(f["severity"], 9) for f in sys_findings
                         if f.get("kind") != "gap"], default=9)
            has_out_core = any(m["status"] == "out_of_range" and m["tier"] == "core" and m["freshness"] != "stale"
                               for m in sys_markers)
            # An abnormal finding only speaks for the present while it is still in
            # date. A stale one is a lead to re-check, not a current problem.
            has_abn_exam = any(e["status"] == "abnormal" and e["freshness"] != "stale"
                               for e in (self.exams.get(x) for x in exam_known) if e)
            has_stale_abn_exam = any(e["status"] == "abnormal"
                                     for e in (self.exams.get(x) for x in exam_known) if e)
            has_out = (any(m["status"] == "out_of_range" for m in sys_markers)
                       or has_stale_abn_exam)
            has_sub = any(m["status"] == "suboptimal" for m in sys_markers)

            interpreted = any(m["status"] != "context" for m in sys_markers) or bool(exam_known)
            if not interpreted and worst > 1:
                status = "unknown"
            elif label in ("Unmapped", "Sparse") and not (has_out or worst <= 1):
                status = "unknown"
            elif has_out_core or has_abn_exam or worst <= 1:
                status = "attention"
            elif has_out or has_sub or worst == 2:
                status = "watch"
            else:
                status = "steady"

            # What the reader already knows, and is already treating, should not
            # be presented as a fresh alarm every time the page is opened. The
            # finding stays, the wording stays, the colour calms down.
            # One exception, deliberately: something urgent is never calmed. Being
            # under care for blood pressure is no reason to quieten a potassium of
            # 6.4 in the same system.
            conds = [c for c in self.conditions if c["system"] == s["id"]]
            under_care = any(c["managed"] for c in conds)
            urgent = any(f["severity"] == "urgent" and f.get("kind") != "gap"
                         for f in sys_findings)
            display = status
            if under_care and not urgent and status in ("attention", "watch"):
                display = "managed"

            out.append({
                "id": s["id"], "name": s["name"], "name_zh": s.get("name_zh", ""),
                "essence": s.get("essence", ""), "essence_zh": s.get("essence_zh", ""),
                "certainty": round(certainty, 3), "certainty_label": label,
                "status": status, "display_state": display,
                "conditions": conds, "under_care": under_care, "urgent": urgent,
                "markers": sorted(sys_markers, key=lambda m: (
                    0 if m["tier"] == "core" else 1,
                    {"out_of_range": 0, "suboptimal": 1, "optimal": 2}.get(m["status"], 3),
                    m["name"])),
                "unknown_markers": [{"id": m, "name": self.K.markers[m].name,
                                     "name_zh": self.K.markers[m].name_zh,
                                     "tier": self.K.markers[m].tier,
                                     "note": self.K.markers[m].note,
                                     "note_zh": self.K.markers[m].note_zh} for m in unknown],
                "core_missing": core_missing, "core_stale": core_stale,
                "exams": [self.exams[e] for e in exam_known],
                "unknown_exams": [{"id": e, "name": self.K.exams[e].name,
                                   "name_zh": self.K.exams[e].name_zh,
                                   "note": self.K.exams[e].note,
                                   "note_zh": self.K.exams[e].note_zh} for e in exam_unknown],
                "findings": sys_findings,
                "maintenance": s.get("maintenance", []) or [],
                "maintenance_zh": s.get("maintenance_zh", []) or [],
            })
        return out

    def _applies_marker(self, mid: str) -> bool:
        m = self.K.markers.get(mid)
        if not m:
            return False
        return not (m.sex and self.sex and m.sex != self.sex)

    def _applies_exam(self, eid: str) -> bool:
        e = self.K.exams.get(eid)
        if not e:
            return False
        return not (e.sex and self.sex and e.sex != self.sex)

    # -- screening ---------------------------------------------------------
    def _screening(self) -> list[dict]:
        flags = self.profile.get("flags", {}) or {}
        out: list[dict] = []
        if flags.get("pregnant") is True:
            return out
        for entry in self.K.schedule:
            if self.profile.get("screening_guideline") != "USPSTF":
                continue
            if entry.get("requires_flag") and flags.get(entry["requires_flag"]) is not True:
                continue
            target = entry.get("target", "")
            known = self.K.markers.get(target) or self.K.exams.get(target)
            if not known:
                # a schedule entry naming a test the knowledge base does not have
                # is an editing mistake, not a reason to crash a person's page
                self.warnings.append(
                    f"screening '{entry.get('id', '?')}' targets unknown test '{target}'")
                continue
            ap = entry.get("applies", {}) or {}
            if ap.get("sex") and ap["sex"] != self.sex:
                continue
            if self.age is None and (ap.get("age_min") is not None or ap.get("age_max") is not None):
                continue
            if self.age is not None:
                if ap.get("age_min") and self.age < ap["age_min"]:
                    continue
                if ap.get("age_max") and self.age > ap["age_max"]:
                    continue
            if ap.get("if_flags") and not any(flags.get(f) is True for f in ap["if_flags"]):
                continue
            if entry.get("alternative_group"):
                alternatives = [x for x in self.K.schedule if x.get("alternative_group") == entry["alternative_group"]]
                if any((st := self.markers.get(x["target"]) or self.exams.get(x["target"]))
                       and st["months_since"] <= x["cadence_months"]
                       and st["status"] in ("normal", "optimal", "steady") for x in alternatives):
                    continue
            st = self.markers.get(target) or self.exams.get(target)
            cadence = float(entry.get("cadence_months", 12))
            if not st:
                state, last, months = "never", None, None
            else:
                last = st["date"]
                months = st["months_since"]
                if months > cadence * 1.35:
                    state = "overdue"
                elif months > cadence:
                    state = "due"
                else:
                    state = "current"
            out.append({
                "id": entry["id"], "target": target, "label": entry.get("label", target),
                "label_zh": entry.get("label_zh", ""),
                "priority": entry.get("priority", "recommended"),
                "cadence_months": cadence, "why": entry.get("why", ""),
                "why_zh": entry.get("why_zh", ""),
                "state": state, "last": last, "months_since": months,
                "system": known.system,
                "kind": "exam" if target in self.K.exams else "lab",
            })
        order = {"never": 0, "overdue": 1, "due": 2, "current": 3}
        out.sort(key=lambda x: (order[x["state"]], PRIO_ORDER.get(x["priority"], 9), x["label"]))
        return out

    # -- merged action list ------------------------------------------------
    def _actions(self) -> list[dict]:
        acts: dict[str, dict] = {}

        def touch(target: str) -> dict | None:
            obj = self.K.markers.get(target) or self.K.exams.get(target)
            if not obj or getattr(obj, "derived", False):
                return None   # computed values need no new test
            a = acts.setdefault(target, {
                "target": target, "name": obj.name, "name_zh": obj.name_zh,
                "system": obj.system, "kind": "exam" if target in self.K.exams else "lab",
                "reasons": [], "priority": "optional", "state": None, "rank": 9,
                "last": None,
            })
            st = self.markers.get(target) or self.exams.get(target)
            if st:
                a["last"] = st["date"]
            return a

        for f in self.findings:
            for nxt in f.get("next", []):
                a = touch(nxt.get("test", ""))
                if not a:
                    continue
                reason = " ".join((nxt.get("why") or "").split())
                if reason and reason not in [r["text"] for r in a["reasons"]]:
                    a["reasons"].append({
                        "text": reason,
                        "text_zh": " ".join((nxt.get("why_zh") or "").split()),
                        "from": f["title"], "from_zh": f.get("title_zh", ""),
                        "severity": f["severity"]})
                a["rank"] = min(a["rank"], SEV_ORDER.get(f["severity"], 9))
                if f["severity"] in ("urgent", "act"):
                    a["priority"] = "essential"
                elif a["priority"] == "optional":
                    a["priority"] = "recommended"

        for s in self.screening:
            if s["state"] == "current":
                continue
            a = touch(s["target"])
            if not a:
                continue
            label = {"never": "never measured", "overdue": "overdue",
                     "due": "due now"}[s["state"]]
            label_zh = {"never": "从未做过", "overdue": "已逾期",
                        "due": "到期"}[s["state"]]
            reason = f"Scheduled screening — {label}. {s['why']}"
            if reason not in [r["text"] for r in a["reasons"]]:
                a["reasons"].append({
                    "text": reason,
                    "text_zh": f"筛查计划 — {label_zh}。{s.get('why_zh') or ''}".strip(),
                    "from": s["label"], "from_zh": s.get("label_zh", ""),
                    "severity": "schedule"})
            if PRIO_ORDER.get(s["priority"], 9) < PRIO_ORDER.get(a["priority"], 9):
                a["priority"] = s["priority"]
            a["state"] = s["state"]
            a["rank"] = min(a["rank"], 1 if (s["priority"] == "essential"
                                             and s["state"] != "due") else 2)

        out = list(acts.values())
        for a in out:
            a["reasons"].sort(key=lambda r: SEV_ORDER.get(r["severity"], 8))
            st = self.markers.get(a["target"]) or self.exams.get(a["target"])
            if a["state"] is None:
                a["state"] = "never" if st is None else "refresh"
            # something already measured and still current is a re-check, not a gap
            if a["state"] in ("refresh",) and st and st["freshness"] == "fresh":
                a["state"] = "recheck"
                a["rank"] = max(a["rank"], 3)
        out.sort(key=lambda a: (a["rank"], PRIO_ORDER.get(a["priority"], 9),
                                0 if a["state"] == "never" else 1, a["name"]))
        # rank is an internal weight; order is the queue position a reader sees
        for i, a in enumerate(out, 1):
            a["order"] = i
        return out

    # -- assembly ----------------------------------------------------------
    def summary(self) -> dict:
        dates = sorted({r["date"] for r in self.measurements} |
                       {r["date"] for r in self.exam_records})
        # every state is reported, including the zeros: a reader (or a page) that
        # asks "how many are under care" deserves an answer, not a missing key
        sys_by_status: dict[str, int] = {k: 0 for k in
                                        ("attention", "managed", "watch", "steady", "unknown")}
        for s in self.systems:
            k = s["display_state"]
            sys_by_status[k] = sys_by_status.get(k, 0) + 1
        den = sum(1 for s in self.systems)
        overall = sum(s["certainty"] for s in self.systems) / den if den else 0.0
        flagged = [m for m in self.markers.values() if m["status"] == "out_of_range"]
        watch = [m for m in self.markers.values() if m["status"] == "suboptimal"]
        # "steady" is in the laboratory range for a marker that has no tighter
        # optimal band; it belongs with the reassuring ones, not in a void
        settled = [m for m in self.markers.values()
                   if m["status"] in ("optimal", "steady")]
        context = [m for m in self.markers.values() if m["status"] == "context"]
        return {
            "generated_at": datetime.now().isoformat(timespec="minutes"),
            "as_of": self.today,
            "measurements": len(self.measurements),
            "derived": len(self.derived),
            "markers_tracked": len(self.markers),
            "markers_catalogued": len(self.K.markers),
            "exams_on_file": len(self.exams),
            "first_record": dates[0] if dates else None,
            "last_record": dates[-1] if dates else None,
            "years_covered": round((months_between(dates[0], dates[-1]) / 12), 1)
            if len(dates) > 1 else 0.0,
            "visits": len(dates),
            "certainty": round(overall, 3),
            "systems_total": den,
            "systems": dict(sys_by_status),
            "counts": {"out_of_range": len(flagged), "suboptimal": len(watch),
                       "optimal": len(settled), "context": len(context)},
            "findings": {k: sum(1 for f in self.findings if f["severity"] == k)
                         for k in SEV_ORDER},
            "documents": len(self.documents),
        }

    def timeline(self) -> list[dict]:
        agg: dict[str, dict] = {}
        for r in self.measurements:
            a = agg.setdefault(r["date"], {"date": r["date"], "labs": 0, "exams": 0,
                                           "sources": set()})
            a["labs"] += 1
            a["sources"].add(r.get("source", ""))
        for r in self.exam_records:
            a = agg.setdefault(r["date"], {"date": r["date"], "labs": 0, "exams": 0,
                                           "sources": set()})
            a["exams"] += 1
            a["sources"].add(r.get("source", ""))
        out = []
        for k in sorted(agg):
            a = agg[k]
            out.append({"date": a["date"], "labs": a["labs"], "exams": a["exams"],
                        "sources": sorted(x for x in a["sources"] if x)})
        return out

    def to_dict(self) -> dict:
        return {
            "release": {"version": __version__, "mode": "adult_record_review",
                        "screening": self.profile.get("screening_guideline") or "off"},
            "profile": {
                "name": self.profile.get("name", ""),
                "sex": self.sex,
                "age": round(self.age, 1) if self.age else None,
                "height": self.markers.get("height", {}).get("value"),
                "conditions": self.profile.get("conditions", []) or [],
                "medications": self.profile.get("medications", []) or [],
                "family_history": self.profile.get("family_history", []) or [],
                "flags": self.profile.get("flags", {}) or {},
                "notes": self.profile.get("notes", ""),
                "demo": bool(self.profile.get("demo")),
            },
            "summary": self.summary(),
            "systems": self.systems,
            "findings": self.findings,
            "actions": self.actions,
            "screening": self.screening,
            "timeline": self.timeline(),
            "markers": self.markers,
            "exams": self.exams,
            "documents": self.documents,
            "trends": self.trends,
            # everything the reader should know about how to read the page: a
            # missing sex, an unmatched condition, a knowledge-base quibble
            "kb_warnings": list(dict.fromkeys(
                list(getattr(self.K, "warnings", [])) + self.warnings)),
        }


_SINCE_RE = re.compile(
    r"[\s,，;；]*[（(]?\s*(?:since|from|dx|diagnosed(?:\s+in)?|自|于)?\s*"
    r"((?:19|20)\d{2})\s*(?:年)?\s*(?:起|开始|至今)?\s*[)）]?\s*$",
    re.IGNORECASE)


def _split_since(text: str) -> tuple[str, str | None]:
    """Split a trailing year out of a free-text condition.

    ``"hypertension since 2011"`` → ``("hypertension", "2011")``. A bare year on
    its own is left alone: it is the whole of what the reader wrote.
    """
    s = (text or "").strip()
    m = _SINCE_RE.search(s)
    if not m:
        return s, None
    head = s[:m.start()].strip(" ,，;；·-—")
    return (head, m.group(1)) if head else (s, None)


def _cmp(a: float, op: str, b: float) -> bool:
    try:
        b = float(b)
    except (TypeError, ValueError):
        return False
    return {">=": a >= b, ">": a > b, "<=": a <= b, "<": a < b,
            "==": abs(a - b) < 1e-9, "!=": abs(a - b) >= 1e-9}.get(op, False)


def analyze(records: dict, profile: dict, K: KnowledgeBase | None = None,
            today: str | None = None) -> dict:
    return Analysis(K or kb(), records, profile, today=today).to_dict()
