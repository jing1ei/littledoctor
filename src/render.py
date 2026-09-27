"""Render the static site: analysis.json + templates -> site/.

    site/index.html     the body — one screen, an anatomical figure
    site/archive.html   every original document, searchable
    site/pages/         page images of scans, photos and DICOM frames

Each page carries only the data it draws. The body needs states and stories but
not a single line of report text; the archive needs every word but nothing about
findings or actions. Shaping the two payloads keeps both pages small enough to
open instantly, and keeps the browser's memory in the low megabytes.
"""
from __future__ import annotations

import json
import os
import shutil
import filecmp
from html import escape
from pathlib import Path
from urllib.parse import quote
from .storage import atomic_write, atomic_copy, disjoint, reject_links

WEB = Path(__file__).resolve().parent.parent / "web"
BODY = WEB / "body.html"
ARCHIVE = WEB / "archive.html"

# what each page actually reads — anything else is weight
DOC_KEYS_INDEX = ("id", "file", "name", "title", "date", "kind", "modality",
                  "systems", "markers", "exam_ids", "measurements", "exams")
MARKER_KEYS = ("id", "name", "name_zh", "unit", "tier", "status", "side", "display",
               "qualitative", "date", "months_since", "freshness", "band", "band_zh",
               "history", "derived", "computed_from")
EXAM_KEYS = ("id", "name", "name_zh", "status", "date", "months_since", "freshness")
HISTORY_KEYS = ("date", "value")
MARKER_STORY_KEYS = ("sentence", "sentence_zh",
                     "trajectory", "trajectory_word", "trajectory_word_zh")
GAP_KEYS = ("id", "name", "name_zh")
STORY_DROP = ("severity_curve", "slope", "drifting")     # computed, never drawn
NAME_KEYS = ("name", "name_zh", "system")


def _blob(payload: dict) -> str:
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # safe to embed inside <script type="application/json">
    return s.replace("</", "<\\/").replace("<!--", "\\u003C!--")


def _fill(template: Path, payload: dict, title: str) -> str:
    return (template.read_text(encoding="utf-8")
            .replace("__TITLE__", escape(str(title)))
            .replace("__LITTLEDOCTER_DATA__", _blob(payload)))


def _pick(d: dict, keys) -> dict:
    return {k: d[k] for k in keys if k in d}


def _names(index: dict) -> dict:
    """id -> name, both languages, and the system it belongs to."""
    return {k: _pick(v, NAME_KEYS) for k, v in (index or {}).items()}


def _system(s: dict) -> dict:
    out = dict(s)
    out["markers"] = [dict(_pick(m, MARKER_KEYS),
                           history=[_pick(h, HISTORY_KEYS) for h in m.get("history", [])],
                           story=_pick(m.get("story") or {}, MARKER_STORY_KEYS))
                      for m in s.get("markers", [])]
    out["exams"] = [_pick(e, EXAM_KEYS) for e in s.get("exams", [])]
    for gap in ("unknown_markers", "unknown_exams"):
        out[gap] = [_pick(m, GAP_KEYS) for m in s.get(gap, [])]
    # findings live once, at the top level; a system only points at them
    out["findings"] = [f["id"] for f in s.get("findings", []) if f.get("id")]
    story = s.get("story")
    if story:
        out["story"] = {k: v for k, v in story.items() if k not in STORY_DROP}
    return out


def _index_payload(data: dict) -> dict:
    """The body page: states, stories, and enough of a document to link to it."""
    return {
        "release": data.get("release", {}),
        "profile": data.get("profile", {}),
        "summary": data.get("summary", {}),
        "systems": [_system(s) for s in data.get("systems", [])],
        "findings": data.get("findings", []),
        "actions": data.get("actions", []),
        "markers": _names(data.get("markers")),
        "exams": _names(data.get("exams")),
        "documents": [_pick(d, DOC_KEYS_INDEX) for d in data.get("documents", [])],
        # what the reading itself could not see — shown in "About this page", so a
        # missing birth date or an unmatched condition is visible to the reader
        # and not only to whoever ran the command
        "notes": data.get("kb_warnings", []),
        "reports_href": data.get("reports_href", "../reports/"),
    }


def _archive_payload(data: dict) -> dict:
    """The archive page: every word of every document, and names to search by."""
    return {
        "profile": data.get("profile", {}),
        "summary": data.get("summary", {}),
        "systems": [_pick(s, ("id", "name", "name_zh")) for s in data.get("systems", [])],
        "markers": _names(data.get("markers")),
        "exams": _names(data.get("exams")),
        "documents": data.get("documents", []),
        "reports_href": data.get("reports_href", "../reports/"),
    }


def _sync(src: Path | None, dst: Path) -> Path | None:
    """Mirror a folder of assets, copying only what changed."""
    if not src or not src.exists():
        if dst.exists():
            shutil.rmtree(dst)
        return None
    dst.mkdir(parents=True, exist_ok=True)
    filecmp.clear_cache()
    keep = set()
    for s in src.iterdir():
        if s.name.startswith("."):
            continue
        keep.add(s.name)
        d = dst / s.name
        if s.is_dir():
            if d.is_file():
                d.unlink()
            _sync(s, d)
            continue
        if d.is_dir():
            shutil.rmtree(d)
        if not d.exists() or not filecmp.cmp(s, d, shallow=False):
            atomic_copy(s, d)
    for d in dst.iterdir():                      # drop what no longer exists
        if d.name in keep:
            continue
        if d.is_file():
            d.unlink(missing_ok=True)
        else:
            shutil.rmtree(d)
    return dst


def render_site(data: dict, out_dir: Path, pages_dir: Path | None = None,
                reports_dir: Path | None = None,
                include_originals: bool = False) -> dict:
    out_dir = Path(out_dir).expanduser()
    pages_dir = Path(pages_dir).expanduser() if pages_dir else None
    reports_dir = Path(reports_dir).expanduser() if reports_dir else None
    # Asset mirroring removes obsolete output. Never let it touch an input tree.
    sources = [Path(p).resolve() for p in (pages_dir, reports_dir) if p]
    targets = [out_dir.resolve(), (out_dir / "pages").resolve(),
               (out_dir / "reports").resolve()]
    for src in sources:
        for dst in targets:
            disjoint(src, dst)
    if include_originals and (not reports_dir or not reports_dir.is_dir()):
        raise ValueError("original reports folder is missing; existing site was preserved")
    for asset in [out_dir / "pages", out_dir / "reports", pages_dir,
                  reports_dir if include_originals else None]:
        if asset is not None:
            reject_links(asset)
    for page in (out_dir / "index.html", out_dir / "archive.html"):
        if page.is_symlink():
            raise ValueError(f"refusing to replace a symbolic link: {page}")
    out_dir.mkdir(parents=True, exist_ok=True)

    name = (data.get("profile", {}) or {}).get("name") or "littledocter"
    title = f"{name} · littledocter"
    written: dict[str, Path] = {}

    # where "open the original" points: the copy if we made one, otherwise the
    # source folder itself — a relative path that resolves when the site is
    # opened from this project and deliberately leads nowhere once deployed
    href = "reports/"
    if not include_originals:
        src = Path(reports_dir) if reports_dir else Path("reports")
        try:
            href = f"{os.path.relpath(src.resolve(), out_dir.resolve())}/"
        except ValueError:                       # different drives on Windows
            href = "../reports/"
    data = dict(data, reports_href=quote(href.replace("\\", "/"), safe="/"))
    # Prepare both pages before changing published assets or pages.
    index_html = _fill(BODY, _index_payload(data), title)
    archive_html = _fill(ARCHIVE, _archive_payload(data), title)
    pages = _sync(pages_dir, out_dir / "pages")
    if pages:
        written["pages"] = pages
    if include_originals:
        written["reports"] = _sync(reports_dir, out_dir / "reports")
    elif (out_dir / "reports").exists():
        shutil.rmtree(out_dir / "reports")
    for key, html in (("index", index_html), ("archive", archive_html)):
        written[key] = out_dir / f"{key}.html"
        atomic_write(written[key], html)
    return written
