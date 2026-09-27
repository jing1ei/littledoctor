"""Derived measurements: values computed from other values on the same date."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from .kb import KnowledgeBase


def _age_at(birth, when) -> float | None:
    """Age in years. Accepts ISO strings or date/datetime objects."""
    if not birth:
        return None
    try:
        born = date.fromisoformat(str(birth)[:10])
        at = date.fromisoformat(str(when)[:10])
    except (TypeError, ValueError):
        return None
    age = (at - born).days / 365.25
    return age if 0 <= age < 130 else None


def normalise_sex(value) -> str | None:
    return {"f": "female", "female": "female", "woman": "female", "女": "female",
            "m": "male", "male": "male", "man": "male", "男": "male"}.get(
                str(value or "").strip().lower())


def profile_age(profile: dict, when: str) -> float | None:
    age = _age_at(profile.get("birth_date"), when)
    if age is not None:
        return age
    try:
        age = float(profile.get("age"))
    except (TypeError, ValueError):
        return None
    return age if 0 < age < 130 else None


def compute_derived(K: KnowledgeBase, measurements: list[dict], profile: dict) -> list[dict]:
    """Return new measurement records for every formula whose inputs are present."""
    sex = normalise_sex(profile.get("sex"))
    pregnant = (profile.get("flags") or {}).get("pregnant") is True

    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    conflicted: set[tuple[str, str]] = set()
    doc_of: dict[str, str] = {}                  # which document that date came from
    for r in measurements:
        if r.get("value") is not None:
            key = (r["date"], r["marker"])
            previous = by_date[r["date"]].get(r["marker"])
            if previous is not None and previous != r["value"]:
                conflicted.add(key)
            by_date[r["date"]][r["marker"]] = r["value"]
            if r.get("doc"):
                doc_of.setdefault(r["date"], r["doc"])
    dates = sorted(by_date)
    for d, mid in conflicted:
        by_date[d].pop(mid, None)

    def carried(mid: str, upto: str) -> float | None:
        """Latest value of a stable marker at or before `upto`."""
        best = None
        for d in dates:
            if d <= upto and mid in by_date[d]:
                best = by_date[d][mid]
        return best

    out: list[dict] = []
    for d in dates:
        vals = by_date[d]
        # A present-day age is not the patient's age at a historical blood draw.
        age = _age_at(profile.get("birth_date"), d)

        def get(mid: str, carry: bool = False):
            """Positive readings only — a zero or negative lab value is a typo,
            and letting it through would divide by zero or root a negative."""
            v = vals.get(mid)
            if v is None and carry:
                v = carried(mid, d)
            return v if isinstance(v, (int, float)) and v > 0 else None

        def emit(mid: str, value: float | None, note: str) -> None:
            if value is None or not math.isfinite(value) or mid in vals:
                return
            m = K.markers.get(mid)
            if not m:
                return
            out.append({"marker": mid, "system": m.system, "value": round(value, 4),
                        "qualitative": None, "unit": m.unit, "date": d,
                        "doc": doc_of.get(d),
                        "source": "computed", "raw_name": m.name, "raw_value": "",
                        "ref_raw": "", "flag": "", "note": note, "derived": True})

        w, h = get("weight"), get("height", carry=True)
        if w and h and not pregnant:
            emit("bmi", w / ((h / 100) ** 2), "weight / height²")

        cr = get("creatinine")
        if cr and age is not None and age >= 18 and sex and not pregnant:
            scr = cr / 88.4                       # umol/L -> mg/dL
            k, alpha = (0.7, -0.241) if sex == "female" else (0.9, -0.302)
            egfr = (142 * (min(scr / k, 1) ** alpha) * (max(scr / k, 1) ** -1.200)
                    * (0.9938 ** age) * (1.012 if sex == "female" else 1.0))
            emit("egfr", egfr, "CKD-EPI 2021 (race-free)")

        g, ins = get("glucose_fasting"), get("insulin_fasting")
        if g and ins:
            emit("homa_ir", g * ins / 22.5, "glucose × insulin / 22.5")

        tc, hdl, tg = get("total_cholesterol"), get("hdl_c"), get("triglycerides")
        if tc and hdl and tc >= hdl:
            emit("non_hdl_c", tc - hdl, "TC − HDL")
            emit("tc_hdl_ratio", tc / hdl, "TC / HDL")
        if tg and hdl:
            emit("tg_hdl_ratio", tg / hdl, "TG / HDL")

        ast, alt, plt = get("ast"), get("alt"), get("platelets")
        if ast and alt:
            emit("ast_alt_ratio", ast / alt, "AST / ALT")
        if ast and alt and plt and age is not None and age >= 35 and not pregnant:
            emit("fib4", age * ast / (plt * math.sqrt(alt)), "age × AST / (platelets × √ALT)")

        neu, lym = get("neutrophils"), get("lymphocytes")
        if neu and lym:
            emit("neutrophil_lymphocyte_ratio", neu / lym, "neutrophils / lymphocytes")

    return out
