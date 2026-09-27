"""Ingestion: turn a folder of raw reports into normalised records + an archive.

Two outputs, always:

* **measurements / exams** — the numbers and impressions the analyser reasons on
* **documents** — one record per source file, with its own words kept intact
  (title, date, modality, extracted text, conclusions, page previews) so the
  archive page can search the originals and every finding can be traced back.

Anything may be dropped into ``reports/``::

    .txt .md .text     typed, exported or OCR'd text
    .pdf               text-layer PDFs; scanned ones go to the vision model
    .png .jpg .jpeg    photos / screenshots of slips and imaging reports
    .webp .gif .bmp .tif
    .dcm .dicom        DICOM (header + embedded report text, if pydicom is present)
    .csv               date,marker,value,unit[,reference,flag,note]
    .yaml .yml         hand-entered measurements / exams / self-reported values
    .json              a previously exported payload

Text understanding lives in :mod:`src.parse` and is covered by
``python3 littledocter.py selftest``; this module only deals with files and models.
"""
from __future__ import annotations

import ast
import codecs
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import parse
from .kb import KnowledgeBase, kb, norm_text
from .storage import atomic_write, disjoint, reject_links
from .parse import (BP_RE, classify_impression, date_from_name, detect_modality,  # noqa: F401
                    find_date, parse_number, parse_qualitative)

TEXT_EXT = {".txt", ".md", ".text", ".log", ".rtf"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}
DATA_EXT = {".csv", ".tsv"}
YAML_EXT = {".yaml", ".yml"}
JSON_EXT = {".json"}
DICOM_EXT = {".dcm", ".dicom", ".ima"}
DOC_EXT = {".docx"}
SKIP_EXT = {".zip", ".7z", ".rar", ".mp4", ".mov", ".xlsx", ".xls", ".key", ".ppt"}

MAX_TEXT = 60_000          # per document, kept for the archive search
MAX_PREVIEW_PAGES = 12

# Hospital portals in China still hand out GB18030/GBK far more often than UTF-8,
# and a silent mojibake read would quietly delete every Chinese label in the file.
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5")


def read_text_any(path: Path) -> str:
    """The file's text, in whichever encoding it happens to be written."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ── record factory ───────────────────────────────────────────────────────────

def make_measurement(K: KnowledgeBase, raw_name: str, raw_value: Any, raw_unit: str,
                     when: str, source: str, ref_raw: str = "", flag: str = "",
                     note: str = "", doc: str = "") -> list[dict]:
    """Resolve one raw report row into 0..n measurement records."""
    out: list[dict] = []
    raw_name = (raw_name or "").strip()
    sval = unicodedata.normalize("NFKC", str(raw_value)).strip()

    bp = BP_RE.match(sval)
    if bp and ("血压" in raw_name or norm_text(raw_name) in
               {"bp", "bloodpressure", "bloodpressuremmhg", "presion", "血压mmhg"}):
        for mid, v in (("bp_systolic", bp.group(1)), ("bp_diastolic", bp.group(2))):
            out.extend(make_measurement(K, mid, v, "mmHg", when, source, ref_raw,
                                        flag, note, doc))
        return out

    mid = K.resolve_marker(raw_name)
    if not mid:
        return out
    marker = K.markers[mid]

    if marker.direction == "qualitative":
        q = parse_qualitative(sval)
        if q is None:
            q = classify_impression(sval)
            if q == "unclear":
                return out
            q = "positive" if q == "abnormal" else "negative"
        out.append({
            "marker": mid, "system": marker.system, "value": None, "qualitative": q,
            "unit": "", "date": when, "source": source, "doc": doc,
            "raw_name": raw_name, "raw_value": sval, "ref_raw": ref_raw,
            "flag": flag, "note": note,
        })
        return out

    num = parse_number(sval)
    if num is None:
        return out
    val, warn = K.convert(marker, num, raw_unit)
    out.append({
        "marker": mid, "system": marker.system, "value": round(val, 4),
        "qualitative": None, "unit": marker.unit, "date": when, "source": source,
        "doc": doc, "raw_name": raw_name, "raw_value": sval, "raw_unit": raw_unit,
        "ref_raw": ref_raw, "flag": flag,
        "note": "; ".join(x for x in (note, warn) if x),
    })
    return out


# ── text reports ─────────────────────────────────────────────────────────────

def parse_text_report(K: KnowledgeBase, text: str, source: str,
                      default_date: str | None, doc: str = "",
                      when: str | None = None) -> tuple[list[dict], list[dict]]:
    """Every row and every impression a block of report text is willing to give.

    Pass `when` if the report's date has already been established, so the text is
    not scanned for a date twice.
    """
    when = when or find_date(text) or default_date
    if not when:
        return [], []
    measurements: list[dict] = []
    for raw_line in str(text or "").splitlines():
        for row in parse.parse_rows(raw_line):
            measurements.extend(make_measurement(
                K, row.name, row.value, row.unit, when, source,
                row.ref, row.flag, row.note, doc))
    return measurements, parse_exams(K, text, source, when, doc)


CONCLUSION_LEAD = re.compile(
    r"^(检查所见|影像所见|超声所见|所见|结论|小结|诊断|印象|意见|提示|总结|"
    r"impression|conclusion|findings?|diagnosis|summary)\s*[:：]?\s*", re.IGNORECASE)


def parse_exams(K: KnowledgeBase, text: str, source: str, when: str | None,
                doc: str = "") -> list[dict]:
    """Imaging / procedure findings, wherever the report chose to put them.

    Handles both shapes seen in the wild: the one-liner
    ``腹部超声：轻度脂肪肝`` and the block form where a title line is followed by
    ``所见 / 结论`` paragraphs further down the page.
    """
    if not when:
        return []
    lines = [parse.clean_line(l) for l in str(text or "").splitlines()]
    aliases = K.exam_aliases()
    found: dict[str, dict] = {}

    for i, line in enumerate(lines):
        if not line:
            continue
        line_n = norm_text(line)
        if not line_n:
            continue
        for alias, eid in aliases:
            if len(alias) < 2 or alias not in line_n:
                continue
            parts = re.split(r"[:：]", line, maxsplit=1)
            imp = parts[1].strip() if len(parts) > 1 else line.strip()
            if len(imp) < 6 or imp == line.strip():
                imp = _lookahead_impression(lines, i) or imp
            status = classify_impression(imp)
            prev = found.get(eid)
            if prev and prev["status"] == "abnormal" and status != "abnormal":
                break
            found[eid] = {
                "exam": eid, "system": K.exams[eid].system, "date": when,
                "impression": imp[:400], "status": status, "source": source,
                "doc": doc, "modality": detect_modality(line) or detect_modality(imp),
            }
            break
    return list(found.values())


def _lookahead_impression(lines: list[str], i: int, span: int = 8) -> str | None:
    """The conclusion sentence that belongs to a bare title line."""
    best = None
    for line in lines[i + 1: i + 1 + span]:
        if not line:
            continue
        if parse.parse_rows(line):
            continue
        m = CONCLUSION_LEAD.match(line)
        if m:
            tail = line[m.end():].strip(" -—:：")
            if len(tail) >= 4:
                return tail[:400]
            continue
        if best is None and len(line) >= 6 and not CONCLUSION_LEAD.match(line):
            best = line[:400]
    return best


# ── PDF / image / DICOM ──────────────────────────────────────────────────────

def _pymupdf():
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:  # pragma: no cover
        try:
            import fitz  # type: ignore
            return fitz
        except ImportError:
            return None


def pdf_text(path: Path) -> tuple[str, bool, int]:
    """(text, looks_scanned, page_count)."""
    mod = _pymupdf()
    if mod is None:
        return "", False, 0
    with mod.open(path) as doc:
        chunks = [pg.get_text("text") for pg in doc]
        pages = len(chunks)
    text = "\n".join(chunks)
    dense = len(re.sub(r"\s", "", text))
    return text, dense < 120 * max(1, pages // 3 or 1), pages


def _fresh(out: Path, src: Path) -> bool:
    """True when a rendered preview is already at least as new as its source."""
    try:
        return out.exists() and out.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def pdf_to_images(path: Path, out_dir: Path, dpi: int = 170,
                  max_pages: int = MAX_PREVIEW_PAGES) -> list[Path]:
    mod = _pymupdf()
    if mod is None:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = _preview_key(path)
    done = sorted(out_dir.glob(f"{prefix}_p*.png"),
                  key=lambda p: int(p.stem.rsplit("_p", 1)[1]))
    if done and all(_fresh(d, path) for d in done):
        return done[:max_pages]
    out: list[Path] = []
    with mod.open(path) as doc:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            p = out_dir / f"{prefix}_p{i + 1}.png"
            page.get_pixmap(dpi=dpi).save(p)
            out.append(p)
    return out


def dicom_read(path: Path, out_dir: Path) -> tuple[str, list[Path], dict]:
    """Header text + a rendered preview from a DICOM object, if pydicom is around."""
    try:
        import pydicom  # type: ignore
    except ImportError:
        return "", [], {"warning": "pydicom not installed; DICOM header not read "
                                   "(pip install pydicom)"}
    ds = pydicom.dcmread(str(path), force=True, stop_before_pixels=False)
    fields = ["StudyDate", "SeriesDate", "Modality", "StudyDescription",
              "SeriesDescription", "BodyPartExamined", "ImageComments",
              "PatientSex", "PatientAge"]
    lines = [f"{f}: {getattr(ds, f)}" for f in fields if getattr(ds, f, None)]
    for tag in ("ImpressionText", "TextValue"):
        if getattr(ds, tag, None):
            lines.append(f"Impression: {getattr(ds, tag)}")
    meta = {"modality": str(getattr(ds, "Modality", "") or "").lower() or None}
    previews: list[Path] = []
    try:
        import numpy as np
        from PIL import Image
        arr = ds.pixel_array
        if arr.ndim == 3:
            arr = arr[arr.shape[0] // 2]
        a = arr.astype("float32")
        a = (a - a.min()) / max(1e-6, (a.max() - a.min())) * 255
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"{_preview_key(path)}_dicom.png"
        if not _fresh(p, path):
            Image.fromarray(a.astype(np.uint8)).convert("L").save(p)
        previews.append(p)
    except Exception as exc:  # pixel data is optional
        meta["warning"] = f"no DICOM preview ({exc})"
    return "\n".join(lines), previews, meta


def image_preview(path: Path, out_dir: Path, width: int = 1100) -> list[Path]:
    """A downscaled copy so the archive can show the original slip."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return []
    # a 600 dpi flatbed scan of an A4 slip is ~35 MP and perfectly legitimate here,
    # so lift Pillow's decompression-bomb guard rather than dropping the preview
    if Image.MAX_IMAGE_PIXELS is not None:
        Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS, 400_000_000)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        done = out_dir / f"{_preview_key(path)}.jpg"
        if _fresh(done, path):
            return [done]
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if im.width > width:
                im = im.resize((width, int(im.height * width / im.width)))
            p = done
            im.save(p, quality=82)
        return [p]
    except Exception:
        return []


def docx_text(path: Path) -> str:
    """Word exports, without pulling in python-docx."""
    import zipfile
    from xml.etree import ElementTree
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "".join((node.text or "") if node.tag == ns + "t" else
                   "\t" if node.tag == ns + "tab" else
                   "\n" if node.tag in (ns + "p", ns + "br") else ""
                   for node in root.iter())


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(s)).strip("-")[:60] or "page"


def _preview_key(path: Path) -> str:
    return f"{_slug(path.stem)}_{file_digest(path)}"


# ── vision extraction ────────────────────────────────────────────────────────

VISION_PROMPT = """You are reading a medical document: a laboratory report, a health
check-up booklet page, a hand-written slip, an imaging report or an imaging film.
Extract everything legible, faithfully, and invent nothing.

Return ONE json object, no prose, no markdown fence:
{
  "report_date": "YYYY-MM-DD or null",
  "title": "the document's own title as printed or null",
  "modality": "ultrasound|ct|mri|xray|mammography|endoscopy|ecg|dexa|fundus|pathology|dental|spirometry|lab|null",
  "sex": "female|male|null",
  "age": number or null,
  "measurements": [
    {"name": "exact label as printed (keep Chinese)", "value": "as printed",
     "unit": "as printed or empty", "reference": "reference interval as printed or empty",
     "flag": "H|L|arrow|empty"}
  ],
  "exams": [
    {"name": "imaging / procedure name as printed",
     "impression": "the conclusion text as printed"}
  ],
  "conclusions": ["each conclusion / impression / recommendation sentence as printed"],
  "text": "all remaining legible text, line by line"
}
Rules:
- Keep the original label text; do not translate or normalise names.
- Numbers exactly as printed. Qualitative results as printed (阴性/阳性/-/+).
- Blood pressure as one entry named 血压 with value like 118/76.
- Put ultrasound / CT / MRI / endoscopy / ECG conclusions under "exams".
- For an imaging film with no text, describe nothing: return empty lists and put any
  visible burned-in annotation into "text".
- If a value is unreadable, omit that row rather than guessing.
"""


def file_digest(path: Path, n: int = 16) -> str:
    """sha1 of a file, streamed — films and scans can be hundreds of megabytes."""
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def _cache_key(batch: list[str]) -> str:
    h = hashlib.sha1(VISION_PROMPT.encode("utf-8"))
    for f in batch:
        try:
            h.update(file_digest(Path(f), 40).encode("ascii"))
        except Exception:
            h.update(f.encode("utf-8", "ignore"))
    return h.hexdigest()[:20]


def vision_read(images: list[Path], script: str | None = None,
                cache_dir: Path | None = None) -> list[str]:
    """Raw model text for each batch of page images, cached by content.

    Only the *reading* is cached, never the interpretation: a cached OCR pass is
    re-parsed by the current parser on every run, so parser fixes still land on
    old scans without paying for the model again. Delete the cache folder to
    force a fresh read.
    """
    script = script or os.environ.get("ANALYZE_IMAGE_SCRIPT") or _guess_vision_script()
    cache = Path(cache_dir) if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for i in range(0, len(images), 5):
        batch = [str(p.resolve()) for p in images[i:i + 5]]
        hit = (cache / f"{_cache_key(batch)}.txt") if cache else None
        if hit and hit.exists():
            print(f"    · cached reading for {len(batch)} page(s)")
            out.append(hit.read_text(encoding="utf-8"))
            continue
        if not script or not Path(script).exists():
            print(f"  ! vision extraction unavailable (analyze_image.py not found); "
                  f"skipped {len(batch)} image(s)", file=sys.stderr)
            continue
        req = json.dumps({"task": VISION_PROMPT, "paths": batch}, ensure_ascii=False)
        try:
            proc = subprocess.run([sys.executable, script, "-"], input=req, text=True,
                                  capture_output=True, timeout=900)
        except subprocess.TimeoutExpired:
            print("  ! vision extraction timed out", file=sys.stderr)
            continue
        raw = _tool_text(proc.stdout or "")
        if proc.returncode:
            print(f"  ! vision helper exited with status {proc.returncode}; reading not cached", file=sys.stderr)
            continue
        if "{" not in raw:                  # some wrappers log their answer to stderr
            raw = _tool_text(proc.stderr or "")
        if raw.strip():
            if hit:
                atomic_write(hit, raw)
            out.append(raw)
    return out


def vision_extract(images: list[Path], script: str | None = None,
                   cache_dir: Path | None = None) -> list[dict]:
    payloads: list[dict] = []
    for raw in vision_read(images, script, cache_dir):
        found = [normalise_payload(b) for b in _json_objects(raw)
                 if isinstance(b, dict) and any(
                     k in b for k in ("measurements", "exams", "conclusions", "text",
                                      "report_date"))]
        if found:
            payloads += found
        elif raw.strip():
            # the model answered in prose or OCR only — keep it; the text parser
            # downstream reads it exactly like a .txt report
            payloads.append({"text": raw.strip()[:MAX_TEXT], "prose": True})
    return payloads


def _tool_text(raw: str) -> str:
    """Unwrap the tool's transport shell so the JSON inside is real JSON.

    ``analyze_image.py`` prints a Python repr such as
    ``AimeToolResultText(result='…\\n…')``. Inside that repr every newline and
    every JSON escape is itself escaped, so the payload will not parse until the
    literal is evaluated back into text.
    """
    s = (raw or "").strip()
    m = re.search(r"result\s*=\s*('.*'|\".*\")\s*\)?\s*$", s, re.S)
    if m:
        try:
            return str(ast.literal_eval(m.group(1)))
        except Exception:
            pass
    if "\\n" in s and "\n" not in s.strip("\n"):
        try:                                   # a bare escaped blob
            return codecs.decode(s, "unicode_escape")
        except Exception:
            pass
    return s


def normalise_payload(p: dict) -> dict:
    """Accept the shapes vision models actually return, not just the asked-for one.

    Measurements may arrive as ``{"name","value"}``, as ``"ALT 42 U/L"`` or as
    ``{"甲状腺右侧叶": "45×16×15mm"}``; exams as objects or as bare sentences.
    Everything is coerced to the documented shape and anything unreadable is
    dropped rather than guessed at.
    """
    out = dict(p)

    rows: list[dict] = []
    for row in p.get("measurements") or []:
        if isinstance(row, dict):
            if "name" in row or "项目" in row:
                rows.append({"name": row.get("name") or row.get("项目") or "",
                             "value": row.get("value", row.get("结果", "")),
                             "unit": row.get("unit", row.get("单位", "")) or "",
                             "reference": row.get("reference",
                                                  row.get("参考区间", "")) or "",
                             "flag": row.get("flag", row.get("提示", "")) or ""})
            else:                              # {"label": "value"} pairs
                for k, v in row.items():
                    rows.append({"name": str(k), "value": v, "unit": "",
                                 "reference": "", "flag": ""})
        elif isinstance(row, str):
            for r in parse.parse_rows(row):
                rows.append({"name": r.name, "value": r.value, "unit": r.unit,
                             "reference": r.ref, "flag": r.flag})
    out["measurements"] = rows

    exams: list[dict] = []
    for row in p.get("exams") or []:
        if isinstance(row, dict):
            exams.append({"name": row.get("name") or row.get("项目") or "",
                          "impression": row.get("impression")
                          or row.get("conclusion") or row.get("结论") or ""})
        elif isinstance(row, str) and row.strip():
            exams.append({"name": row.strip(), "impression": row.strip()})
    out["exams"] = exams

    concl: list[str] = []
    raw_concl = p.get("conclusions") or p.get("impression") or []
    if isinstance(raw_concl, str):
        raw_concl = [raw_concl]
    for c in raw_concl:
        if isinstance(c, str) and c.strip():
            concl.append(c.strip())
        elif isinstance(c, dict):
            concl.append(str(c.get("text") or c.get("impression") or "").strip())
    out["conclusions"] = [c for c in concl if len(c) >= 4]

    mod = p.get("modality")
    if mod:                                    # "超声 (Ultrasound)" -> "ultrasound"
        out["modality"] = detect_modality(str(mod)) or (
            str(mod).strip().lower()
            if str(mod).strip().lower() in {n for n, _ in parse.MODALITY} | {"lab"} else None)
    return out


def _guess_vision_script() -> str | None:
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        cand = parent / "inner_skills" / "analyze_media" / "analyze_image.py"
        if cand.exists():
            return str(cand)
    return None


def _json_objects(text: str) -> Iterable[dict]:
    """Every top-level JSON object in a noisy string, braces inside strings and all.

    Vision models like to wrap their answer in prose, fences or a stray log line,
    so the decoder is walked forward object by object rather than trusting the
    whole document to parse.
    """
    dec = json.JSONDecoder()
    at = 0
    while True:
        at = text.find("{", at)
        if at < 0:
            return
        try:
            obj, end = dec.raw_decode(text, at)
        except ValueError:
            at += 1                       # not the start of an object after all
            continue
        if isinstance(obj, dict):
            yield obj
        at = end


def payload_to_records(K: KnowledgeBase, payload: dict, source: str,
                       default_date: str | None,
                       doc: str = "") -> tuple[list[dict], list[dict]]:
    when = payload.get("report_date") or default_date
    if when:
        when = find_date(str(when)) or default_date
    if not when:
        return [], []
    ms: list[dict] = []
    for row in payload.get("measurements") or []:
        if not isinstance(row, dict):
            continue
        ms.extend(make_measurement(
            K, row.get("name", ""), row.get("value", ""), row.get("unit", "") or "",
            when, source, str(row.get("reference", "") or ""),
            str(row.get("flag", "") or ""), note="vision-extracted", doc=doc))
    exs: list[dict] = []
    for row in payload.get("exams") or []:
        if not isinstance(row, dict):
            continue
        eid = K.resolve_exam(row.get("name", "")) or _fuzzy_exam(K, row.get("name", ""))
        if not eid:
            continue
        imp = str(row.get("impression", "") or "")
        exs.append({"exam": eid, "system": K.exams[eid].system, "date": when,
                    "impression": imp[:400], "status": classify_impression(imp),
                    "source": source, "doc": doc,
                    "modality": payload.get("modality") or detect_modality(row.get("name", ""))})
    # conclusions the model saw but could not tie to a known exam still matter:
    # they are quoted in the archive and matched to systems by wording
    for line in payload.get("conclusions") or []:
        if not isinstance(line, str) or len(line) < 4:
            continue
        eid = _fuzzy_exam(K, line)
        if not eid or any(e["exam"] == eid for e in exs):
            continue
        exs.append({"exam": eid, "system": K.exams[eid].system, "date": when,
                    "impression": line[:400], "status": classify_impression(line),
                    "source": source, "doc": doc, "modality": payload.get("modality")})
    return ms, exs


def _fuzzy_exam(K: KnowledgeBase, label: str) -> str | None:
    n = norm_text(label)
    if not n:
        return None
    best, best_len = None, 0
    for alias, eid in K.exam_aliases():
        if len(alias) >= 3 and (alias in n or n in alias) and len(alias) > best_len:
            best, best_len = eid, len(alias)
    return best


# ── csv / yaml / json ────────────────────────────────────────────────────────

def parse_csv(K: KnowledgeBase, path: Path, doc: str = "") -> list[dict]:
    out: list[dict] = []
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    fh = io.StringIO(read_text_any(path), newline="")
    for row in csv.DictReader(fh, delimiter=delim):
        keys = {norm_text(k): k for k in row if k}

        def g(*names, default=""):
            for n in names:
                if n in keys:
                    return (row.get(keys[n]) or "").strip()
            return default

        when = find_date(g("date", "日期", "检查日期", "检验日期", "时间", "采样时间"))
        name = g("marker", "name", "项目", "指标", "item", "项目名称", "检验项目")
        if not when or not name:
            continue
        out.extend(make_measurement(
            K, name, g("value", "值", "结果", "result", "检验结果"), g("unit", "单位"),
            when, str(path.name), g("reference", "参考范围", "ref", "参考值"),
            g("flag", "标记", "提示"), note=g("note", "备注"), doc=doc))
    return out


def parse_yaml_entries(K: KnowledgeBase, path: Path,
                       doc: str = "") -> tuple[list[dict], list[dict]]:
    payload = yaml.safe_load(read_text_any(path)) or {}
    return _entries_to_records(K, payload, path.name, doc)


def parse_json_entries(K: KnowledgeBase, path: Path,
                       doc: str = "") -> tuple[list[dict], list[dict]]:
    payload = json.loads(read_text_any(path) or "{}")
    if isinstance(payload, dict) and (
            any(isinstance(r, dict) and "marker" in r for r in payload.get("measurements", []))
            or any(isinstance(r, dict) and "exam" in r for r in payload.get("exams", []))):
        ms, exs = [], []
        for row in payload.get("measurements", []):
            when = find_date(str(row.get("date", "")))
            if not when or row.get("derived"):
                continue
            ms.extend(make_measurement(K, row.get("marker", ""),
                                      row.get("qualitative") or row.get("value"),
                                      row.get("unit", ""), when, path.name,
                                      row.get("ref_raw", ""), row.get("flag", ""),
                                      row.get("note", ""), doc))
        for row in payload.get("exams", []):
            when = find_date(str(row.get("date", "")))
            eid = K.resolve_exam(row.get("exam", ""))
            if when and eid:
                exs.append(dict(row, exam=eid, system=K.exams[eid].system,
                                date=when, source=path.name, doc=doc))
        return ms, exs
    if isinstance(payload, dict) and ("measurements" in payload or "exams" in payload) \
            and "entries" not in payload:
        return payload_to_records(K, payload, path.name, date_from_name(path.stem), doc)
    return _entries_to_records(K, payload, path.name, doc)


def _entries_to_records(K: KnowledgeBase, payload: Any, source: str,
                        doc: str) -> tuple[list[dict], list[dict]]:
    entries = payload.get("entries", []) if isinstance(payload, dict) else payload
    ms: list[dict] = []
    exs: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        when = find_date(str(entry.get("date", "")))
        if not when:
            continue
        for name, val in (entry.get("measurements") or {}).items():
            unit = ""
            if isinstance(val, dict):
                unit = str(val.get("unit", "") or "")
                val = val.get("value")
            ms.extend(make_measurement(K, name, val, unit, when, source,
                                       note=str(entry.get("note", "") or ""), doc=doc))
        for name, imp in (entry.get("exams") or {}).items():
            eid = K.resolve_exam(name) or _fuzzy_exam(K, name)
            if not eid:
                continue
            exs.append({"exam": eid, "system": K.exams[eid].system, "date": when,
                        "impression": str(imp)[:400],
                        "status": classify_impression(str(imp)),
                        "source": source, "doc": doc,
                        "modality": detect_modality(str(name))})
    return ms, exs


# ── documents ────────────────────────────────────────────────────────────────

def _title_of(text: str, fallback: str) -> str:
    for line in str(text or "").splitlines():
        s = parse.clean_line(line)
        if len(s) < 4 or parse.NOISE_LINE.search(s):
            continue
        if parse.parse_rows(s):
            continue
        return s[:90]
    return fallback


def _doc_record(idx: int, rel: str, path: Path, kind: str) -> dict:
    try:
        size = path.stat().st_size
        digest = file_digest(path, 12)
    except Exception:
        size, digest = 0, ""
    return {
        "id": f"d{idx:03d}", "file": rel, "name": path.name, "kind": kind,
        "bytes": size, "sha1": digest, "date": None, "date_source": "none",
        "title": path.stem.replace("_", " "), "modality": None, "pages": 1,
        "previews": [], "text": "", "chars": 0, "conclusions": [], "systems": [],
        "measurements": 0, "exams": 0, "markers": [], "exam_ids": [],
        "extraction": "text", "warnings": [],
    }


# ── orchestration ────────────────────────────────────────────────────────────

def _prune_pages(pages_dir: Path, docs: list[dict]) -> None:
    """Rendered previews are reused between runs; drop the orphans."""
    if not pages_dir.exists():
        return
    keep = {Path(pv).name for d in docs for pv in d.get("previews", [])}
    for f in pages_dir.iterdir():
        if f.is_file() and f.name not in keep:
            f.unlink(missing_ok=True)


def ingest_dir(reports_dir: Path, K: KnowledgeBase | None = None,
               use_vision: bool = True, pages_dir: Path | None = None,
               cache_dir: Path | None = None) -> dict:
    K = K or kb()
    reports_dir = Path(reports_dir).expanduser()
    if not reports_dir.is_dir():
        raise ValueError(f"reports folder does not exist: {reports_dir}")
    pages_dir = Path(pages_dir) if pages_dir else reports_dir.parent / "data" / "pages"
    cache_dir = Path(cache_dir) if cache_dir else pages_dir.parent / "cache" / "vision"
    disjoint(reports_dir, pages_dir)
    disjoint(reports_dir, cache_dir)
    reject_links(pages_dir)
    reject_links(cache_dir)

    measurements: list[dict] = []
    exams: list[dict] = []
    docs: list[dict] = []
    pending: list[tuple[Path, dict]] = []      # (image, doc record)

    paths = sorted(p for p in reports_dir.rglob("*")
                   if p.is_file() and not any(part.startswith(".")
                                              for part in p.relative_to(reports_dir).parts))

    for i, p in enumerate(paths, 1):
        ext = p.suffix.lower()
        if ext in SKIP_EXT:
            continue
        rel = str(p.relative_to(reports_dir))
        fallback = date_from_name(p.stem)
        rec = _doc_record(i, rel, p, "file")
        docs.append(rec)
        try:
            if p.is_symlink():
                raise ValueError("symbolic-link report skipped; use a regular file")
            if ext in TEXT_EXT:
                text = read_text_any(p)
                _absorb_text(K, rec, text, rel, fallback, measurements, exams, "text")
            elif ext in DOC_EXT:
                _absorb_text(K, rec, docx_text(p), rel, fallback, measurements, exams,
                             "docx")
            elif ext == ".pdf":
                if _pymupdf() is None:
                    raise RuntimeError("PyMuPDF not installed; PDF not read")
                text, scanned, pages = pdf_text(p)
                rec["pages"] = pages or 1
                if not scanned:
                    _absorb_text(K, rec, text, rel, fallback, measurements, exams,
                                 "pdf-text")
                if scanned or (not rec["measurements"] and not rec["exams"]):
                    rec["kind"] = "pdf-scan"
                    rec["extraction"] = "vision"
                    imgs = pdf_to_images(p, pages_dir)
                    if rec["pages"] > MAX_PREVIEW_PAGES:
                        rec["warnings"].append(f"only the first {MAX_PREVIEW_PAGES} pages previewed/read as images")
                    rec["previews"] = [str(x.relative_to(pages_dir.parent)) for x in imgs]
                    if use_vision:
                        pending += [(img, rec) for img in imgs]
                    else:
                        rec["warnings"].append("scanned PDF, vision disabled")
            elif ext in IMG_EXT:
                rec["kind"] = "image"
                rec["extraction"] = "vision"
                rec["modality"] = detect_modality(p.stem)
                prev = image_preview(p, pages_dir)
                rec["previews"] = [str(x.relative_to(pages_dir.parent)) for x in prev]
                if not prev:
                    rec["warnings"].append("could not make a preview of this image")
                if use_vision:
                    pending.append((p, rec))
                else:
                    rec["warnings"].append("image, vision disabled")
                rec["date"], rec["date_source"] = fallback, "filename" if fallback else "none"
            elif ext in DICOM_EXT:
                rec["kind"] = "dicom"
                text, prev, meta = dicom_read(p, pages_dir)
                rec["modality"] = meta.get("modality")
                if meta.get("warning"):
                    rec["warnings"].append(meta["warning"])
                rec["previews"] = [str(x.relative_to(pages_dir.parent)) for x in prev]
                _absorb_text(K, rec, text, rel, fallback, measurements, exams, "dicom")
                if use_vision and prev and not rec["exams"]:
                    pending += [(x, rec) for x in prev]
            elif ext in DATA_EXT:
                rec["kind"] = "csv"
                rec["extraction"] = "structured"
                m = parse_csv(K, p, rec["id"])
                measurements += m
                _tally(rec, m, [])
                rec["text"] = read_text_any(p)[:MAX_TEXT]
                rec["chars"] = len(rec["text"])
                rec["date"] = max((x["date"] for x in m), default=fallback)
                rec["date_source"] = "content" if m else rec["date_source"]
            elif ext in YAML_EXT or ext in JSON_EXT:
                rec["kind"] = ext.lstrip(".")
                rec["extraction"] = "structured"
                m, e = (parse_yaml_entries(K, p, rec["id"]) if ext in YAML_EXT
                        else parse_json_entries(K, p, rec["id"]))
                measurements += m
                exams += e
                _tally(rec, m, e)
                rec["text"] = read_text_any(p)[:MAX_TEXT]
                rec["chars"] = len(rec["text"])
                dates = [x["date"] for x in m] + [x["date"] for x in e]
                rec["date"] = max(dates, default=fallback)
                rec["date_source"] = "content" if dates else rec["date_source"]
            else:
                rec["kind"] = "unsupported"
                rec["warnings"].append(f"unsupported extension {ext or '(none)'}")
        except Exception as exc:                     # one bad file must not stop the run
            rec["kind"] = "error"
            rec["warnings"].append(f"{type(exc).__name__}: {exc}")

    if pending:
        print(f"  · vision extraction on {len(pending)} page image(s)…")
        by_doc: dict[str, list[Path]] = {}
        recs = {r["id"]: r for r in docs}
        for img, rec in pending:
            by_doc.setdefault(rec["id"], []).append(img)
        for did, imgs in by_doc.items():
            rec = recs[did]
            try:
                payloads = vision_extract(imgs, cache_dir=cache_dir)
            except Exception as exc:
                rec["warnings"].append(f"vision extraction failed: {exc}")
                continue
            if not payloads:
                rec["warnings"].append("vision returned nothing")
            for payload in payloads:
                m, e = payload_to_records(K, payload, rec["file"],
                                          rec["date"] or date_from_name(rec["name"]),
                                          did)
                measurements += m
                exams += e
                _tally(rec, m, e)
                if payload.get("title"):
                    rec["title"] = str(payload["title"])[:90]
                rec["modality"] = rec["modality"] or payload.get("modality")
                extra = "\n".join(
                    [str(payload.get("text") or "")]
                    + [str(c) for c in (payload.get("conclusions") or [])])
                if extra.strip():
                    rec["text"] = (rec["text"] + "\n" + extra).strip()[:MAX_TEXT]
                    rec["chars"] = len(rec["text"])
                for c in payload.get("conclusions") or []:
                    if isinstance(c, str) and len(c) >= 4:
                        rec["conclusions"].append({
                            "text": c[:400], "systems": parse.systems_in(c),
                            "status": classify_impression(c)})
                when = (m or e or [{}])[0].get("date") if (m or e) else None
                if when:
                    rec["date"], rec["date_source"] = when, "content"

            # whatever the model wrote, read it once more as if it were a .txt
            # report: OCR lines, prose answers and burned-in film text all pass
            # through the same parser as typed reports
            if rec["text"] and not (rec["measurements"] or rec["exams"]):
                when = rec["date"] or date_from_name(rec["name"])
                m, e = parse_text_report(K, rec["text"], rec["file"], when, did)
                measurements += m
                exams += e
                _tally(rec, m, e)
                if m or e:
                    rec["warnings"].append("read as text, not structured JSON")
            if rec["text"] and not rec["conclusions"]:
                rec["conclusions"] = parse.parse_conclusions(rec["text"])
            if not rec["date"]:
                when = find_date(rec["text"]) or date_from_name(rec["name"])
                if when:
                    rec["date"] = when
                    rec["date_source"] = "content"
            if not rec["title"] or rec["title"] == Path(rec["name"]).stem.replace("_", " "):
                rec["title"] = _title_of(rec["text"], rec["title"])
            rec["modality"] = rec["modality"] or detect_modality(rec["text"][:1500])
            rec["_systems"] = (rec.get("_systems") or []) + [
                s for c in rec["conclusions"] for s in c.get("systems", [])]

    for rec in docs:
        rec["systems"] = sorted({s for c in rec["conclusions"] for s in c["systems"]}
                                | set(rec.pop("_systems", []) or []))
        rec["conclusions"] = rec["conclusions"][:24]

    measurements = dedupe(measurements, ("marker", "date", "value", "qualitative"))
    exams = dedupe(exams, ("exam", "date", "status"))
    measurements.sort(key=lambda r: (r["date"], r["marker"]))
    exams.sort(key=lambda r: (r["date"], r["exam"]))
    docs.sort(key=lambda r: (r["date"] or "0000", r["file"]))
    return {
        "measurements": measurements, "exams": exams,
        "documents": docs,
        "pages_dir": str(pages_dir),
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
    }


def _absorb_text(K: KnowledgeBase, rec: dict, text: str, rel: str,
                 fallback: str | None, measurements: list[dict], exams: list[dict],
                 kind: str) -> None:
    """Read a block of text into records and fill in the document's own metadata."""
    text = str(text or "")
    rec["kind"] = kind
    rec["text"] = text[:MAX_TEXT]
    rec["chars"] = len(text)
    when = find_date(text)
    rec["date"] = when or fallback
    rec["date_source"] = "content" if when else ("filename" if fallback else "none")
    rec["title"] = _title_of(text, rec["title"])
    rec["modality"] = detect_modality(text[:1500]) or rec["modality"]
    concl = parse.parse_conclusions(text)
    rec["conclusions"] = concl
    rec["_systems"] = [s for c in concl for s in c["systems"]]
    m, e = parse_text_report(K, text, rel, fallback, rec["id"], when=rec["date"])
    measurements += m
    exams += e
    _tally(rec, m, e)
    if not rec["date"]:
        rec["warnings"].append("no date found; rows dropped")


def _tally(rec: dict, m: list[dict], e: list[dict]) -> None:
    rec["measurements"] += len(m)
    rec["exams"] += len(e)
    rec["markers"] = sorted(set(rec["markers"]) | {x["marker"] for x in m})
    rec["exam_ids"] = sorted(set(rec["exam_ids"]) | {x["exam"] for x in e})
    rec.setdefault("_systems", []).extend(x["system"] for x in m + e if x.get("system"))


def dedupe(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        k = tuple(r.get(x) for x in keys)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out
