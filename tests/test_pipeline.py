"""Regression checks for data boundaries, offline rendering and CLI contracts."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import yaml

from src.analyze import analyze
from src.cli import cmd_check, main
from src.derive import _age_at, compute_derived
from src.ingest import (docx_text, image_preview, ingest_dir, make_measurement,
                        normalise_payload, parse_json_entries)
from src.kb import KB_DIR, KnowledgeBase, kb
from src.render import _index_payload, render_site


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.K = kb()

    def rows(self, name, value, when="2020-01-01", unit=""):
        return make_measurement(self.K, name, value, unit, when, "fixture", doc="d001")

    def test_replay_excludes_future_results_exams_and_quotes(self):
        records = {"measurements": self.rows("alt", 25) + self.rows("alt", 90, "2026-01-01"),
                   "exams": [{"exam": "thyroid_ultrasound", "date": "2026-01-01",
                              "status": "abnormal"}],
                   "documents": [{"id": "future", "file": "future.txt", "date": "2026-01-01"}]}
        result = analyze(records, {}, self.K, "2024-01-01")
        self.assertEqual(result["markers"]["alt"]["value"], 25)
        self.assertEqual(result["summary"]["last_record"], "2020-01-01")
        self.assertEqual(result["exams"], {})
        self.assertEqual(result["documents"], [])

    def test_derived_profile_normalisation_and_missing_sex(self):
        rows = self.rows("creatinine", 80, unit="umol/L")
        a = compute_derived(self.K, rows, {"sex": "female", "birth_date": "1986-01-01"})
        b = compute_derived(self.K, rows, {"sex": "F", "birth_date": "1986-01-01"})
        self.assertEqual(a, b)
        self.assertTrue(any(r["marker"] == "egfr" for r in a))
        self.assertEqual(compute_derived(self.K, rows, {"age": 34}), [])
        self.assertEqual(compute_derived(self.K, rows, {"sex": "female", "age": "34"}), [])
        self.assertEqual(compute_derived(self.K, rows, {"sex": "female", "age": "bad"}), [])
        self.assertIsNone(_age_at("1992-99-99", "2024-01-01"))

    def test_derived_label_survives_rendering(self):
        result = analyze({"measurements": self.rows("weight", 80) + self.rows("height", 170)},
                         {}, self.K, "2024-01-01")
        markers = [m for s in _index_payload(result)["systems"] for m in s["markers"]]
        self.assertTrue(next(m for m in markers if m["id"] == "bmi")["derived"])
        self.assertFalse(next(m for m in markers if m["id"] == "weight")["derived"])

    def test_single_medication_and_multi_date_source(self):
        rows = self.rows("alt", 25) + self.rows("alt", 90, "2026-01-01")
        result = analyze({"measurements": rows, "documents": [
            {"id": "d001", "file": "history.json", "date": "2026-01-01"}]},
            {"medications": "aspirin"}, self.K, "2024-01-01")
        self.assertEqual(result["profile"]["medications"], ["aspirin"])
        self.assertEqual(result["documents"][0]["id"], "d001")
        self.assertEqual(result["markers"]["alt"]["value"], 25)

    def test_ocr_string_rows(self):
        row = normalise_payload({"measurements": ["ALT 42 U/L"]})["measurements"][0]
        self.assertEqual(row["value"], "42")
        self.assertEqual(row["name"], "ALT")

    def test_exported_json_roundtrip(self):
        rows = self.rows("alt", 42) + self.rows("ferritin", 22, "2021-02-03")
        p = self.root / "export.json"
        p.write_text(json.dumps({"measurements": rows, "exams": []}))
        got, _ = parse_json_entries(self.K, p, "newdoc")
        self.assertEqual([(r["marker"], r["date"], r["value"]) for r in got],
                         [(r["marker"], r["date"], r["value"]) for r in rows])
        self.assertTrue(all(r["doc"] == "newdoc" for r in got))

    def test_docx_preserves_entities_and_reports_corruption(self):
        p = self.root / "report.docx"
        with ZipFile(p, "w") as z:
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>ALT &lt; 42 &amp; note</w:t></w:r></w:p></w:body></w:document>')
        self.assertIn("ALT < 42 & note", docx_text(p))
        p.write_bytes(b"broken")
        with self.assertRaises(Exception):
            docx_text(p)

    def test_nested_originals_special_paths_and_title(self):
        reports = self.root / "reports #1"
        (reports / "nested").mkdir(parents=True)
        source = reports / "nested" / "a #?.txt"
        source.write_text("first")
        site = self.root / "site"
        payload = {"profile": {"name": '</title><script>bad()</script>', "notes": "__TITLE__"}}
        render_site(payload, site, reports_dir=reports, include_originals=True)
        self.assertEqual((site / "reports" / "nested" / source.name).read_text(), "first")
        html = (site / "index.html").read_text()
        self.assertNotIn('</title><script>bad()', html)
        self.assertIn('"notes":"__TITLE__"', html)
        self.assertNotIn("fonts.googleapis.com", html)
        source.unlink()
        render_site(payload, site, reports_dir=reports, include_originals=True)
        self.assertFalse((site / "reports" / "nested" / source.name).exists())
        render_site(payload, site, reports_dir=reports)
        self.assertFalse((site / "reports").exists())
        self.assertIn("reports%20%231", (site / "archive.html").read_text())

    def test_overlapping_output_rejected_before_deleting(self):
        reports = self.root / "reports"
        reports.mkdir()
        marker = reports / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(ValueError):
            render_site({}, self.root, reports_dir=reports)
        self.assertEqual(marker.read_text(), "keep")

    def test_preview_collision_and_empty_ingest_preservation(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is optional")
        pages = self.root / "pages"
        images = []
        for name, color in (("a", "red"), ("b", "blue")):
            folder = self.root / name
            folder.mkdir()
            path = folder / "same.png"
            Image.new("RGB", (20, 20), color).save(path)
            images.append(image_preview(path, pages)[0])
        self.assertNotEqual(*images)
        empty = self.root / "empty"
        empty.mkdir()
        ingest_dir(empty, self.K, use_vision=False, pages_dir=pages)
        self.assertTrue(all(p.exists() for p in images))

    def test_structured_systems_and_warnings_surface(self):
        reports = self.root / "reports"
        reports.mkdir()
        (reports / "2020-01-01.csv").write_text("date,marker,value,unit\n2020-01-01,ALT,42,U/L\n")
        (reports / "bad.docx").write_bytes(b"broken")
        records = ingest_dir(reports, self.K, use_vision=False)
        self.assertIn("liver", next(d for d in records["documents"] if d["kind"] == "csv")["systems"])
        result = analyze(records, {}, self.K, "2024-01-01")
        self.assertTrue(any("bad.docx" in w for w in result["kb_warnings"]))

    def test_custom_init_both_option_orders(self):
        for before in (True, False):
            profile = self.root / str(before) / "profile.yaml"
            options = ["--profile", str(profile), "--reports", str(self.root / "reports")]
            with contextlib.redirect_stdout(io.StringIO()):
                main(options + ["init"] if before else ["init"] + options)
            self.assertTrue(profile.exists())
            personal = yaml.safe_load(profile.read_text())
            self.assertFalse(personal["demo"])
            self.assertIsNone(personal["birth_date"])
            self.assertIsNone(personal["sex"])
            self.assertEqual(personal["flags"], {})
            self.assertEqual(personal["family_history"], [])
            profile.write_text("name: Keep my profile\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init"] + options)
            self.assertEqual(profile.read_text(), "name: Keep my profile\n")

    def test_open_generated_site_both_option_orders(self):
        data = self.root / "data"
        data.mkdir()
        (data / "analysis.json").write_text(json.dumps({"profile": {}, "systems": []}))
        for before in (True, False):
            site = self.root / str(before)
            options = ["--open", "--data", str(data), "--site", str(site)]
            with patch("src.cli.webbrowser.open", return_value=True) as browser, contextlib.redirect_stdout(io.StringIO()):
                main(options + ["render"] if before else ["render"] + options)
            browser.assert_called_once_with((site / "index.html").resolve().as_uri())

    def test_check_fails_invalid_knowledge_base(self):
        folder = self.root / "knowledge"
        shutil.copytree(KB_DIR, folder)
        path = folder / "rules.yaml"
        rules = yaml.safe_load(path.read_text())
        rules["rules"][0]["when"] = {"all": [{"m": "missing_marker", "op": ">", "v": 0}]}
        path.write_text(yaml.safe_dump(rules))
        bad = KnowledgeBase(folder)
        self.assertTrue(any("missing_marker" in e for e in bad.errors))
        with patch("src.cli.kb", return_value=bad), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                cmd_check(None)
        self.assertEqual(exc.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
