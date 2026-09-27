"""Knowledge base loading, alias resolution and unit normalisation."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"

# ── text normalisation ───────────────────────────────────────────────────────

_PUNCT = "()（）[]【】{}<>《》・·,，.。:：;；/、\\-–—_'\"“”‘’*#%!?？ \t\u3000"


def norm_text(s: str) -> str:
    """Aggressively normalise a label for alias matching."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("γ", "r").replace("Γ", "r")
    s = s.lower()
    s = "".join(ch for ch in s if ch not in _PUNCT)
    return s.strip()


_POW10 = re.compile(r"[x*·]?(?:10e|1e|10\*?)(3|6|9|12)\b", re.IGNORECASE)


def norm_unit(u: str) -> str:
    if not u:
        return ""
    u = unicodedata.normalize("NFKC", str(u)).strip()
    u = (u.replace("μ", "u").replace("µ", "u").replace("Μ", "u")
           .replace("²", "2").replace("×", "x").replace("·", ".")
           .replace(" ", ""))
    # every way a lab writes a power of ten becomes one token: 10^9/L, 10*9/L,
    # ×10⁹/L (NFKC leaves 109/L), 10E9/L and 1e9/L all normalise to e9/l
    u = u.replace("^", "")
    u = _POW10.sub(lambda m: "e" + m.group(1), u)
    return u.lower()


# ── models ───────────────────────────────────────────────────────────────────

@dataclass
class Marker:
    id: str
    name: str
    name_zh: str
    system: str
    unit: str = ""
    decimals: int = 2
    aliases: list[str] = field(default_factory=list)
    units_alt: dict[str, dict] = field(default_factory=dict)
    ref: dict[str, float] | None = None
    optimal: dict[str, float] | None = None
    ref_by_sex: dict[str, dict] | None = None
    optimal_by_sex: dict[str, dict] | None = None
    direction: str = "context"
    tier: str = "extended"
    fresh_months: int = 12
    note: str = ""
    note_zh: str = ""
    expected: str | None = None
    phase_dependent: bool = False
    sex: str | None = None
    derived: bool = False

    def bounds(self, sex: str | None, kind: str = "ref") -> dict[str, float]:
        by_sex = self.ref_by_sex if kind == "ref" else self.optimal_by_sex
        plain = self.ref if kind == "ref" else self.optimal
        if by_sex and sex and sex in by_sex:
            return dict(by_sex[sex])
        if by_sex and not sex:
            return {}  # Do not invent a reference interval for unknown sex.
        return dict(plain or {})

    def fmt(self, value: float | None) -> str:
        if value is None:
            return "—"
        if self.direction == "qualitative":
            return str(value)
        d = self.decimals if self.decimals is not None else 2
        return f"{value:.{d}f}"


@dataclass
class Exam:
    id: str
    name: str
    name_zh: str
    system: str
    aliases: list[str] = field(default_factory=list)
    validity_months: int = 24
    note: str = ""
    note_zh: str = ""
    sex: str | None = None


class KnowledgeBase:
    def __init__(self, kb_dir: Path | str = KB_DIR):
        self.dir = Path(kb_dir)
        self.markers: dict[str, Marker] = {}
        self.exams: dict[str, Exam] = {}
        self.systems: list[dict] = []
        self.rules: list[dict] = []
        self.schedule: list[dict] = []
        self._alias_marker: dict[str, str] = {}
        self._alias_exam: dict[str, str] = {}
        self._exam_aliases: tuple[tuple[str, str], ...] = ()
        self.alias_conflicts: set[str] = set()
        self._load()

    # -- loading ------------------------------------------------------------
    def _yaml(self, name: str) -> dict:
        with open(self.dir / name, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _load(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []
        derived_doc = self._yaml("derived.yaml")
        for raw in list(self._yaml("markers.yaml").get("markers", [])) + \
                list(derived_doc.get("markers", [])):
            m = self._make(Marker, raw, "marker")
            if m.id in self.markers:
                self.errors.append(f"duplicate marker '{m.id}'")
            self.markers[m.id] = m
        for raw in self._yaml("exams.yaml").get("exams", []):
            e = self._make(Exam, raw, "exam")
            if e.id in self.exams:
                self.errors.append(f"duplicate exam '{e.id}'")
            self.exams[e.id] = e
        self.systems = self._yaml("systems.yaml").get("systems", [])
        self.rules = self._yaml("rules.yaml").get("rules", [])
        self.schedule = self._yaml("screening.yaml").get("schedule", [])
        self._load_conditions()
        self._validate()
        self._build_alias_index()

    def _load_conditions(self) -> None:
        """Substrings that file a known condition under the right system."""
        self.condition_map: list[tuple[str, str]] = []
        try:
            doc = self._yaml("conditions.yaml")
        except FileNotFoundError:
            return
        ids = {s["id"] for s in self.systems}
        for entry in doc.get("map", []) or []:
            sid = entry.get("system")
            if sid not in ids:
                self.warnings.append(f"conditions.yaml: unknown system '{sid}'")
                self.errors.append(self.warnings[-1])
                continue
            for phrase in entry.get("match", []) or []:
                key = norm_text(phrase)
                if key:
                    self.condition_map.append((key, sid))
        # longest phrase wins: "fatty liver" must beat "liver"
        self.condition_map.sort(key=lambda kv: -len(kv[0]))

    def resolve_condition(self, text: str) -> str | None:
        """Which system a person's own words about a condition belong to."""
        low = norm_text(text)
        if not low:
            return None
        for key, sid in self.condition_map:
            if key in low:
                return sid
        return None

    def _make(self, model, raw: dict, what: str):
        """Build a model from YAML, reporting unknown keys instead of crashing."""
        fields = model.__dataclass_fields__
        extra = [k for k in raw if k not in fields]
        if extra:
            self.warnings.append(
                f"{what} {raw.get('id', '?')}: ignored unknown key(s) {', '.join(extra)}")
        return model(**{k: v for k, v in raw.items() if k in fields})

    def _validate(self) -> None:
        """Drop references to markers/exams that do not exist, and report them."""
        ids = {s["id"] for s in self.systems}
        targets = set(self.markers) | set(self.exams)
        for entries, what in ((self.systems, "system"), (self.rules, "rule"),
                              (self.schedule, "schedule")):
            seen = set()
            for entry in entries:
                if entry["id"] in seen:
                    self.errors.append(f"duplicate {what} '{entry['id']}'")
                seen.add(entry["id"])
        for item in list(self.markers.values()) + list(self.exams.values()):
            if item.system not in ids:
                self.errors.append(f"{item.id}: unknown system '{item.system}'")

        def predicate(node, where):
            if isinstance(node, list):
                for child in node:
                    predicate(child, where)
            elif isinstance(node, dict):
                for key in ("all", "any", "none"):
                    if key in node:
                        predicate(node[key], where)
                for key, allowed in (("m", self.markers), ("exam", self.exams),
                                     ("missing", targets), ("present", targets)):
                    if key in node and node[key] not in allowed:
                        self.errors.append(f"{where}: unknown {key} '{node[key]}'")

        for s in self.systems:
            for key in ("core", "extended"):
                good = [m for m in s.get(key, []) or [] if m in self.markers]
                bad = [m for m in s.get(key, []) or [] if m not in self.markers]
                s[key] = good
                self.warnings += [f"system {s['id']}.{key}: unknown marker '{b}'" for b in bad]
                self.errors += [f"system {s['id']}.{key}: unknown marker '{b}'" for b in bad]
            good_e = [e for e in s.get("exams", []) or [] if e in self.exams]
            bad_e = [e for e in s.get("exams", []) or [] if e not in self.exams]
            s["exams"] = good_e
            self.warnings += [f"system {s['id']}.exams: unknown exam '{b}'" for b in bad_e]
            self.errors += [f"system {s['id']}.exams: unknown exam '{b}'" for b in bad_e]
            s["maintenance"] = self._flatten_prose(
                s.get("maintenance") or [], f"system {s['id']}.maintenance")
        for entry in self.schedule:
            t = entry.get("target")
            if t not in self.markers and t not in self.exams:
                self.warnings.append(f"schedule {entry.get('id')}: unknown target '{t}'")
                self.errors.append(self.warnings[-1])
            predicate(entry.get("when"), f"schedule {entry.get('id')}")
        for r in self.rules:
            if r.get("system") not in ids:
                self.errors.append(f"rule {r.get('id')}: unknown system '{r.get('system')}'")
            predicate(r.get("when"), f"rule {r.get('id')}")
            for nxt in r.get("next", []):
                if nxt.get("test") not in targets:
                    self.errors.append(f"rule {r.get('id')}: unknown test '{nxt.get('test')}'")
            if r.get("do"):
                r["do"] = self._flatten_prose(r["do"], f"rule {r.get('id')}.do")

    def _flatten_prose(self, items: list, where: str) -> list[str]:
        """An unquoted colon turns a YAML sentence into a dict — repair and warn."""
        out: list[str] = []
        for item in items:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for k, v in item.items():
                    out.append(f"{k} — {v}" if v is not None else str(k))
                self.warnings.append(
                    f"{where}: an unquoted ':' split a sentence into a mapping "
                    f"({list(item)[0]!r}) — quote the line in the YAML")
            else:
                out.append(str(item))
        return out

    def _build_alias_index(self) -> None:
        seen: dict[str, str] = {}
        for m in self.markers.values():
            for a in [m.id, m.name, m.name_zh, *m.aliases]:
                k = norm_text(a)
                if not k or len(k) < 1:
                    continue
                if k in seen and seen[k] != m.id:
                    self.alias_conflicts.add(k)
                    continue
                seen[k] = m.id
        for k in self.alias_conflicts:
            seen.pop(k, None)
        self._alias_marker = seen

        # exams get the same treatment as markers: an alias claimed by two exams
        # ("CT") is dropped rather than silently handed to whichever loaded first
        seen_e: dict[str, str] = {}
        clash_e: set[str] = set()
        for e in self.exams.values():
            for a in [e.id, e.name, e.name_zh, *e.aliases]:
                k = norm_text(a)
                if not k:
                    continue
                if k in seen_e and seen_e[k] != e.id:
                    clash_e.add(k)
                    continue
                seen_e[k] = e.id
        for k in clash_e:
            seen_e.pop(k, None)
        self.exam_alias_conflicts = clash_e
        self.errors += [f"ambiguous marker alias '{a}'" for a in sorted(self.alias_conflicts)]
        self.errors += [f"ambiguous exam alias '{a}'" for a in sorted(clash_e)]
        self._alias_exam = seen_e
        # longest first, built once: both the line scanner and the fuzzy
        # matcher want "腹部超声" to win over "超声"
        self._exam_aliases = tuple(sorted(seen_e.items(), key=lambda kv: -len(kv[0])))

    # -- resolution ---------------------------------------------------------
    def resolve_marker(self, label: str) -> str | None:
        """Map an arbitrary report label onto a marker id."""
        if not label:
            return None
        cands = [label]
        # 谷丙转氨酶(ALT) -> 谷丙转氨酶 / ALT
        inner = re.findall(r"[（(\[【]([^）)\]】]+)[)）\]】]", label)
        outer = re.sub(r"[（(\[【][^）)\]】]*[)）\]】]", "", label)
        cands.extend(inner)
        cands.append(outer)
        # strip leading numbering "1.谷丙转氨酶"
        cands.append(re.sub(r"^\s*\d+[.、)]\s*", "", label))
        for c in cands:
            k = norm_text(c)
            if k in self._alias_marker:
                return self._alias_marker[k]
        # last resort: longest alias contained in the label
        # (skips ratio-style labels, where containment is misleading)
        low = norm_text(label)
        if not low or "比值" in label or "/" in label or "ratio" in low:
            return None
        best, best_len = None, 0
        for alias, mid in self._alias_marker.items():
            if len(alias) >= 4 and alias in low and len(alias) > best_len:
                best, best_len = mid, len(alias)
        return best

    def resolve_exam(self, label: str) -> str | None:
        k = norm_text(label)
        return self._alias_exam.get(k)

    def exam_aliases(self) -> tuple[tuple[str, str], ...]:
        """Every exam alias with its id, longest first — shared, read-only."""
        return self._exam_aliases

    def system(self, sid: str) -> dict:
        for s in self.systems:
            if s["id"] == sid:
                return s
        return {"id": sid, "name": sid, "core": [], "extended": [], "exams": []}

    # -- units --------------------------------------------------------------
    def convert(self, marker: Marker, value: float, unit: str) -> tuple[float, str]:
        """Convert a raw value into the marker's canonical unit.

        Returns (value, note) where note is '' on success, or a short warning.
        """
        if value is None:
            return value, "no-value"
        canon = norm_unit(marker.unit)
        raw = norm_unit(unit)
        if not raw or raw == canon:
            return value, ""
        for alt, tf in (marker.units_alt or {}).items():
            if norm_unit(alt) == raw:
                return value * float(tf.get("factor", 1.0)) + float(tf.get("offset", 0.0)), ""
        return value, f"unit '{unit}' unrecognised (expected {marker.unit})" if unit else ""


_KB: KnowledgeBase | None = None


def kb() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB
