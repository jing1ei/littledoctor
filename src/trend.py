"""Trend analysis: since when, and where is it going.

A marker or a system is never just a value — it has a history, and the history is
usually the answer to the question a person actually has. This module reads the
same records the analyser uses and produces, per marker and per system:

* **onset** — the first record that showed the problem, and what showed it
* **course** — the transitions in between, quoting the report that made them
* **trajectory** — worsening / improving / drifting / stable / resolved / new
* **story** — three short sentences: where it began, what happened, where it stands

Every sentence is produced twice, English and Chinese (``*_zh``), because the page
can be read in either language. Every milestone carries the id of the document it
came from, so the page can link a 2023 finding straight back to the 2023 report.

It stays free of the knowledge base's opinions: severity comes in through a
``status_fn`` supplied by the caller, so this module can be tested on its own and
never forms an import cycle with :mod:`src.analyze`.
"""
from __future__ import annotations

from datetime import date

SEV_MARKER = {"optimal": 0, "context": 0, "suboptimal": 1, "out_of_range": 2}
SEV_EXAM = {"normal": 0, "unclear": 1, "abnormal": 2}
SEV_WORD = {0: "in range", 1: "borderline", 2: "out of range"}
SEV_WORD_ZH = {0: "在范围内", 1: "临界", 2: "超出范围"}

# The words a reader actually meets. They are deliberately plain and
# unfrightening: they describe direction, not doom. "Worsening" was replaced by
# "Moving away from range" because a number moving is a fact, while a body
# worsening is a verdict this file is not entitled to give.
TRAJECTORY_WORD = {
    "worsening": "Moving away from range",
    "improving": "Improving",
    "stable": "Holding steady",
    "drifting": "Drifting inside range",
    "resolved": "Back in range",
    "new": "Newly noted",
    "single": "One record only",
    "mixed": "Mixed",
    "unknown": "No trend yet",
}
TRAJECTORY_WORD_ZH = {
    "worsening": "正在偏离范围",
    "improving": "正在改善",
    "stable": "保持稳定",
    "drifting": "范围内漂移",
    "resolved": "已回到范围内",
    "new": "新近出现",
    "single": "仅一次记录",
    "mixed": "好坏参半",
    "unknown": "尚无趋势",
}
STATUS_WORD = {"attention": "worth a doctor's look", "watch": "worth watching",
               "managed": "known, under care", "steady": "steady",
               "unknown": "limited interpretable data"}
STATUS_WORD_ZH = {"attention": "建议请医生看看", "watch": "值得观察",
                  "managed": "已知，在管理中", "steady": "平稳", "unknown": "可解读资料有限"}
CERTAINTY_ZH = {"Established": "证据充分", "Partial": "证据部分",
                "Sparse": "证据稀少", "Unmapped": "尚无数据"}
CERTAINTY_EN = {"Established": "well documented", "Partial": "partly documented",
                "Sparse": "thinly documented", "Unmapped": "undocumented"}

COUNT_WORD = {0: "No", 1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
              6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def _count(n: int, noun: str) -> str:
    word = COUNT_WORD.get(n, str(n))
    return f"{word} {noun}" + ("" if n == 1 else "s")


def _year(d: str | None) -> str:
    return (d or "")[:4]


def _years_between(a: str, b: str) -> float:
    try:
        ya, ma, da = (int(x) for x in a.split("-"))
        yb, mb, db = (int(x) for x in b.split("-"))
        return round((date(yb, mb, db) - date(ya, ma, da)).days / 365.25, 1)
    except Exception:
        return 0.0


def _fmt(v, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    if abs(v) >= 100 or float(v).is_integer():
        return f"{v:.0f}"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


def _tidy(s: str) -> str:
    """A quoted conclusion should not end with two punctuation marks."""
    return (s or "").strip().rstrip(".。;；,，")


def _slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope per year; 0.0 when it cannot be computed."""
    n = len(points)
    if n < 2:
        return 0.0
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    if den <= 1e-12:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in points) / den


# ── one marker ───────────────────────────────────────────────────────────────

def marker_story(marker, state: dict, sex: str | None, status_fn, today: str) -> dict:
    """The life of one marker: crossings, drift, and a sentence about it."""
    hist = [h for h in state.get("history", []) if h.get("date")]
    unit = state.get("unit", "") or ""
    graded: list[dict] = []
    for h in hist:
        st, side = status_fn(marker, h.get("value"), h.get("qualitative"), sex)
        if h.get("reference_bounds"):
            from .analyze import status_with_bounds
            st, side = status_with_bounds(h.get("value"), h["reference_bounds"])
        if h.get("reference_unparsed") or h.get("conflict"):
            st, side = "context", None
        if state.get("context_only"):
            st, side = "context", None
        graded.append({"date": h["date"], "value": h.get("value"),
                       "qualitative": h.get("qualitative"), "status": st,
                       "side": side, "sev": SEV_MARKER.get(st, 0),
                       "doc": h.get("doc")})
    if not graded:
        return {"id": state.get("id"), "points": 0, "trajectory": "unknown",
                "trajectory_word": TRAJECTORY_WORD["unknown"],
                "trajectory_word_zh": TRAJECTORY_WORD_ZH["unknown"],
                "sentence": "", "sentence_zh": "", "crossings": [], "onset": None,
                "series": []}

    first, last = graded[0], graded[-1]
    span = _years_between(first["date"], last["date"])
    crossings: list[dict] = []
    for prev, cur in zip(graded, graded[1:]):
        if cur["sev"] != prev["sev"]:
            crossings.append({"date": cur["date"], "from": prev["status"],
                              "to": cur["status"], "value": cur["value"],
                              "rising": cur["sev"] > prev["sev"], "doc": cur.get("doc")})
    onset = next((g for g in graded if g["sev"] > 0), None)
    clear_since = None
    if last["sev"] == 0:
        for g in reversed(graded):
            if g["sev"] > 0:
                break
            clear_since = g["date"]

    nums = [(_years_between(first["date"], g["date"]), float(g["value"]))
            for g in graded if isinstance(g.get("value"), (int, float))]
    slope = _slope(nums) if len(nums) >= 2 else 0.0
    pct = None
    if len(nums) >= 2 and abs(nums[0][1]) > 1e-9:
        pct = round((nums[-1][1] - nums[0][1]) / abs(nums[0][1]) * 100, 1)

    distinct_dates = len({g["date"] for g in graded})
    if state.get("status") == "context":
        traj = "unknown"
    elif distinct_dates == 1:
        traj = "single"
    elif last["status"] in ("steady", "optimal") and any(g["sev"] > 0 for g in graded[:-1]):
        traj = "resolved"
    elif last["sev"] > first["sev"]:
        traj = "worsening"
    elif last["sev"] < first["sev"]:
        traj = "improving"
    elif len(nums) >= 3:
        # same band throughout: let the numbers decide, relative to the good side
        good_down = marker.direction == "high_bad" or (
            marker.direction != "low_bad" and (state.get("side") or last.get("side")) == "high")
        moving_bad = slope > 0 if good_down else slope < 0
        band = state.get("band_num") or {}          # knowledge bounds are low/high
        scale = abs(float(band.get("high", 0) or 0) - float(band.get("low", 0) or 0)) or None
        travel = abs(slope * max(span, 1))
        if last["sev"] > 0:
            material = scale is None or travel > 0.12 * scale
            traj = ("worsening" if moving_bad else "improving") if material else "stable"
        else:
            traj = "stable"  # Numeric movement inside a band is not disease progression.
    else:
        traj = "stable"

    parts: list[str] = []
    parts_zh: list[str] = []
    if len(nums) >= 2:
        move = f"{_fmt(nums[0][1])} → {_fmt(nums[-1][1])}{(' ' + unit) if unit else ''}"
        pctxt = f" ({pct:+.0f}%)" if pct is not None else ""
        pctxt_zh = f"（{pct:+.0f}%）" if pct is not None else ""
        # two readings taken the same day are a repeat, not a span of years
        over = f" over {span:g}y" if span else " on the same day"
        over_zh = f"，{span:g}年" if span else "，同一天两次"
        parts.append(f"{move}{over}{pctxt}")
        parts_zh.append(f"{move}{over_zh}{pctxt_zh}")
    elif last.get("qualitative"):
        parts.append(f"{last['qualitative']} at {_year(last['date'])}")
        parts_zh.append(f"{_year(last['date'])}年：{last['qualitative']}")
    if onset and last["sev"] > 0:
        parts.append(f"first flagged result recorded in {_year(onset['date'])}; latest result also flagged")
        parts_zh.append(f"首次异常记录于{_year(onset['date'])}年；最近一次也异常")
    if traj == "resolved" and clear_since:
        parts.append(f"back in range since {_year(clear_since)}")
        parts_zh.append(f"自{_year(clear_since)}年起回到范围内")
    if traj == "drifting":
        parts.append("still in range, moving toward the edge")
        parts_zh.append("仍在范围内，但正向边界移动")

    return {
        "id": state.get("id"), "name": state.get("name"),
        "name_zh": state.get("name_zh", ""), "unit": unit,
        "points": len(graded), "span_years": span,
        "first": {"date": first["date"], "value": first["value"],
                  "status": first["status"]},
        "last": {"date": last["date"], "value": last["value"], "status": last["status"]},
        "slope_per_year": round(slope, 4), "pct_change": pct,
        "crossings": crossings[-4:],
        "onset": onset and {"date": onset["date"], "value": onset["value"],
                            "status": onset["status"], "doc": onset.get("doc")},
        "clear_since": clear_since,
        "trajectory": traj,
        "trajectory_word": TRAJECTORY_WORD[traj],
        "trajectory_word_zh": TRAJECTORY_WORD_ZH[traj],
        "sentence": "; ".join(parts),
        "sentence_zh": "；".join(parts_zh),
        "series": [{"date": g["date"], "value": g["value"], "status": g["status"],
                    "doc": g.get("doc")} for g in graded],
    }


# ── one system ───────────────────────────────────────────────────────────────

def system_story(system: dict, marker_stories: dict[str, dict],
                 documents: list[dict], today: str) -> dict:
    """Assemble a system's history from labs, imaging and the reports' own words."""
    sid = system["id"]
    events: list[dict] = []

    for m in system.get("markers", []):
        st = marker_stories.get(m["id"])
        if not st or not st.get("points"):
            continue
        label = m.get("name") or m["id"]
        label_zh = m.get("name_zh") or label
        unit = f" {st['unit']}" if st.get("unit") else ""
        # Include ordinary dated results too: otherwise an always-normal panel
        # incorrectly looks like it has never been measured.
        for point in st.get("series", []):
            if point["status"] == "context":
                continue
            sev = SEV_MARKER.get(point["status"], 0)
            events.append({"date": point["date"], "kind": "lab", "sev": sev,
                           "label": label, "label_zh": label_zh,
                           "detail": f"{label} {_fmt(point['value'])}{unit} — {SEV_WORD[sev]}",
                           "detail_zh": f"{label_zh} {_fmt(point['value'])}{unit} — {SEV_WORD_ZH[sev]}",
                           "marker": m["id"], "doc": point.get("doc")})
        if st.get("onset"):
            o = st["onset"]
            sev = SEV_MARKER.get(o["status"], 0)
            events.append({
                "date": o["date"], "kind": "lab", "sev": sev,
                "label": label, "label_zh": label_zh,
                "detail": f"{label} {_fmt(o['value'])}{unit} — {SEV_WORD.get(sev, '')}",
                "detail_zh": f"{label_zh} {_fmt(o['value'])}{unit} — {SEV_WORD_ZH.get(sev, '')}",
                "marker": m["id"], "doc": o.get("doc"), "onset": True})
        for c in st.get("crossings", []):
            if st.get("onset") and c["date"] == st["onset"]["date"]:
                continue
            sev = SEV_MARKER.get(c["to"], 0)
            moved = "moved to" if c["rising"] else "settled to"
            moved_zh = "转为" if c["rising"] else "回落至"
            events.append({
                "date": c["date"], "kind": "lab", "sev": sev,
                "label": label, "label_zh": label_zh,
                "detail": f"{label} {_fmt(c['value'])}{unit} — {moved}"
                          f" {SEV_WORD.get(sev, c['to'])}",
                "detail_zh": f"{label_zh} {_fmt(c['value'])}{unit} — {moved_zh}"
                             f"{SEV_WORD_ZH.get(sev, c['to'])}",
                "marker": m["id"], "doc": c.get("doc")})

    for e in system.get("exams", []):
        e_label = e.get("name") or e["id"]
        for h in e.get("history", []):
            impression = h.get("impression", "") or ""
            events.append({
                "date": h["date"], "kind": "exam", "sev": SEV_EXAM.get(h["status"], 0),
                "label": e_label, "label_zh": e.get("name_zh") or e_label,
                "detail": impression, "detail_zh": impression,
                "exam": e["id"], "status": h["status"], "doc": h.get("doc")})

    quotes: list[dict] = []
    for d in documents or []:
        for c in d.get("conclusions", []) or []:
            if sid in (c.get("systems") or []) and d.get("date"):
                quotes.append({"date": d["date"], "text": c["text"],
                               "status": c.get("status", "unclear"),
                               "doc": d.get("id"), "file": d.get("file"),
                               "modality": d.get("modality", ""),
                               "kind": d.get("kind", "")})
    quotes.sort(key=lambda q: q["date"])

    events.sort(key=lambda x: (x["date"], -x["sev"]))
    if not events:
        return {"id": sid, "trajectory": "unknown",
                "trajectory_word": TRAJECTORY_WORD["unknown"],
                "trajectory_word_zh": TRAJECTORY_WORD_ZH["unknown"],
                "onset": None, "milestones": [], "quotes": quotes[-6:],
                "story": [], "story_zh": [], "years_tracked": 0.0, "visits": 0,
                "headline": "Not enough interpretable records for a trend",
                "headline_zh": "可解读记录不足，暂不判断趋势"}

    # severity per visit → the shape of the system over time
    by_date: dict[str, int] = {}
    for ev in events:
        by_date[ev["date"]] = max(by_date.get(ev["date"], 0), ev["sev"])
    dates = sorted(by_date)
    curve = [(_years_between(dates[0], d), float(by_date[d])) for d in dates]
    slope = _slope(curve)
    first_sev, last_sev = by_date[dates[0]], by_date[dates[-1]]
    onset_ev = next((e for e in events if e["sev"] > 0), None)
    years = _years_between(dates[0], dates[-1])

    if len(dates) == 1:
        traj = "single"                       # one date is a snapshot, never a trend
    elif last_sev == 0 and any(v > 0 for v in by_date.values()):
        traj = "resolved"
    elif onset_ev and _years_between(onset_ev["date"], dates[-1]) <= 1.0 and last_sev > 0 \
            and first_sev == 0:
        traj = "new"
    elif last_sev > first_sev or slope > 0.15:
        traj = "worsening"
    elif last_sev < first_sev or slope < -0.15:
        traj = "improving"
    else:
        traj = "stable"

    # a system can be steady overall while one marker walks away from range
    drifting = [s for s in (marker_stories.get(m["id"]) for m in system.get("markers", []))
                if s and s.get("trajectory") == "worsening" and s.get("points", 0) >= 2]
    if traj in ("stable", "single") and drifting and last_sev > 0:
        traj = "worsening"
    elif traj == "improving" and drifting:
        traj = "mixed"

    keep: list[dict] = []
    for ev in events:
        tag = "onset" if onset_ev and ev is onset_ev else "step"
        if tag == "onset" or ev["sev"] > 0 or ev is events[-1]:
            keep.append({**ev, "tag": tag})
    seen, milestones = set(), []
    for m in sorted(keep, key=lambda x: (x["date"], -x["sev"])):
        k = (m["date"], m["label"], m["sev"])
        if k in seen:
            continue
        seen.add(k)
        milestones.append(m)
    if len(milestones) > 6:                 # keep the beginning and the recent end
        milestones = milestones[:1] + milestones[-5:]

    story: list[str] = []
    story_zh: list[str] = []
    if onset_ev:
        # "first appears" would suggest the body started it that year; all we can
        # honestly say is that this is the earliest record that shows it
        story.append(f"First recorded {_year(onset_ev['date'])} — "
                     f"{_tidy(onset_ev['detail'] or onset_ev['label'])}.")
        story_zh.append(f"记录中最早出现于{_year(onset_ev['date'])}年 — "
                        f"{_tidy(onset_ev['detail_zh'] or onset_ev['label_zh'])}。")
    else:
        story.append(f"On file since {_year(dates[0])}; nothing abnormal recorded.")
        story_zh.append(f"自{_year(dates[0])}年起有记录，未见异常。")

    n_after = sum(1 for d in dates if onset_ev and d > onset_ev["date"])
    driver = drifting[0] if drifting else None
    if traj == "single":
        story.append(f"One record only ({_year(dates[0])}) — a second one would show "
                     f"whether it is moving.")
        story_zh.append(f"仅一次记录（{_year(dates[0])}年）——再测一次就能看出它是否在变化。")
    elif traj in ("worsening", "mixed"):
        lead = (f"{_count(n_after, 'record')} since, drifting further from range" if n_after
                else "No repeat test on file; current status is uncertain")
        lead_zh = (f"此后{n_after}次记录，仍在继续偏离范围" if n_after
                   else "此后未记录复查，目前状态不确定")
        story.append(lead + (f": {driver['name']} {driver['sentence']}." if driver else "."))
        story_zh.append(lead_zh + (f"：{driver.get('name_zh') or driver['name']}"
                                   f" {driver['sentence_zh']}。" if driver else "。"))
    elif traj == "improving":
        story.append(f"{_count(n_after, 'record')} since, heading back toward range.")
        story_zh.append(f"此后{n_after}次记录，正在回到范围内。")
    elif traj == "resolved":
        story.append(f"Back in range at the last check ({_year(dates[-1])}).")
        story_zh.append(f"最近一次检查（{_year(dates[-1])}年）已回到范围内。")
    elif traj == "stable" and onset_ev:
        story.append(f"{_count(n_after, 'follow-up')} since, essentially unchanged."
                     if n_after else
                     "No follow-up on file; discuss whether reassessment is indicated.")
        story_zh.append(f"此后{n_after}次复查，基本没有变化。" if n_after else
                        "此后未记录复查；可讨论是否需要再次评估。")
    elif traj == "new":
        story.append("Seen only in the most recent records.")
        story_zh.append("仅出现在最近的记录里。")

    # the calmed state, when there is one: a system the reader is already being
    # treated for should not be told "worth a doctor's look" every single visit
    status = system.get("display_state") or system.get("status", "unknown")
    certainty = system.get("certainty_label", "Partial")
    span_en = f", {years:g}y of records" if years else ""
    span_zh = f"，{years:g}年记录" if years else ""
    story.append(f"Today: {STATUS_WORD.get(status, '')} · "
                 f"{CERTAINTY_EN.get(certainty, certainty.lower())}{span_en}.")
    story_zh.append(f"目前：{STATUS_WORD_ZH.get(status, '')} · "
                    f"{CERTAINTY_ZH.get(certainty, certainty)}{span_zh}。")

    word = TRAJECTORY_WORD[traj]
    word_zh = TRAJECTORY_WORD_ZH[traj]
    since = _year((onset_ev or {}).get("date") or dates[0])
    # a headline is read before anything else, so it has to be true on its own
    if traj == "single":
        headline = f"One record, {since}"
        headline_zh = f"仅一次记录，{since}年"
    elif traj == "new":
        headline = f"First recorded {since}"
        headline_zh = f"{since}年首次记录"
    elif traj == "unknown":
        headline, headline_zh = word, word_zh
    elif traj == "worsening":
        headline = f"Recorded values moving away from range; first flag {since}"
        headline_zh = f"记录值偏离范围；首次标记于{since}年"
    elif traj == "resolved":
        headline = f"Back in range, last checked {_year(dates[-1])}"
        headline_zh = f"已回到范围内，最近一次{_year(dates[-1])}年"
    elif traj == "stable" and onset_ev:
        headline = f"First noted {since}; recorded values holding steady"
        headline_zh = f"首次记录于{since}年；记录值大致稳定"
    else:
        headline = f"{word} since {since}"
        headline_zh = f"自{since}年起{word_zh}"

    return {
        "id": sid, "trajectory": traj,
        "trajectory_word": word, "trajectory_word_zh": word_zh,
        "headline": headline, "headline_zh": headline_zh,
        "onset": onset_ev and {"date": onset_ev["date"], "year": _year(onset_ev["date"]),
                               "label": onset_ev["label"],
                               "label_zh": onset_ev.get("label_zh") or onset_ev["label"],
                               "detail": onset_ev["detail"],
                               "detail_zh": onset_ev.get("detail_zh") or onset_ev["detail"],
                               "kind": onset_ev["kind"], "doc": onset_ev.get("doc")},
        "years_tracked": years, "visits": len(dates),
        "severity_curve": [{"date": d, "sev": by_date[d]} for d in dates],
        "slope": round(slope, 3),
        "milestones": milestones, "quotes": quotes[-6:],
        "story": story, "story_zh": story_zh,
        "drifting": [{"id": d["id"], "name": d["name"], "name_zh": d.get("name_zh", ""),
                      "sentence": d["sentence"], "sentence_zh": d["sentence_zh"]}
                     for d in drifting[:3]],
    }


def build(systems: list[dict], markers: dict[str, dict], K, sex: str | None,
          status_fn, documents: list[dict], today: str) -> dict:
    """Attach stories to every marker and system, and return the trend index."""
    marker_stories: dict[str, dict] = {}
    for mid, state in markers.items():
        m = K.markers.get(mid)
        if not m:
            continue
        marker_stories[mid] = marker_story(m, state, sex, status_fn, today)
        state["story"] = {k: marker_stories[mid][k] for k in
                          ("trajectory", "trajectory_word", "trajectory_word_zh",
                           "sentence", "sentence_zh", "onset", "crossings",
                           "pct_change", "span_years", "points")}

    system_stories: dict[str, dict] = {}
    for s in systems:
        st = system_story(s, marker_stories, documents, today)
        system_stories[s["id"]] = st
        s["story"] = st
    return {"markers": marker_stories, "systems": system_stories}
