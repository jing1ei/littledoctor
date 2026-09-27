"""Failure-injection checks for filesystem safety and local CLI boundaries."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.cli import main
from src.ingest import ingest_dir, vision_read
from src.render import render_site
from src.storage import atomic_write, output_lock


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='littledocter 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_failed_replace_preserves_old_file_and_cleans_temporary(self):
        p = self.root / 'record.json'
        p.write_text('old')
        with patch('src.storage.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                atomic_write(p, 'new')
        self.assertEqual(p.read_text(), 'old')
        self.assertEqual(list(self.root.iterdir()), [p])
        atomic_write(p, 'new')
        self.assertEqual(p.read_text(), 'new')
        if os.name == 'posix':
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_link_target_untouched(self):
        original = self.root / 'original'
        original.write_text('keep')
        link = self.root / 'link'
        link.symlink_to(original)
        with self.assertRaises(ValueError):
            atomic_write(link, 'replace')
        self.assertEqual(original.read_text(), 'keep')

    def test_concurrent_writer_fails_then_lock_is_reusable(self):
        with output_lock(self.root):
            with self.assertRaises(ValueError):
                with output_lock(self.root):
                    self.fail('second writer acquired lock')
        with output_lock(self.root):
            pass

    def test_ingestion_rejects_outputs_inside_reports(self):
        reports = self.root / 'reports'
        reports.mkdir()
        source = reports / '2026-01-01.txt'
        source.write_text('ALT 42 U/L')
        with self.assertRaises(ValueError):
            ingest_dir(reports, use_vision=False, pages_dir=reports/'pages')
        self.assertEqual(source.read_text(), 'ALT 42 U/L')

    def test_asset_symlink_does_not_write_outside_site(self):
        site = self.root/'site'
        site.mkdir()
        outside = self.root/'outside'
        outside.mkdir()
        (outside/'keep').write_text('keep')
        (site/'pages').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            render_site({}, site)
        self.assertEqual((outside/'keep').read_text(), 'keep')

    def test_missing_originals_preserve_existing_copy(self):
        site = self.root/'site'
        (site/'reports').mkdir(parents=True)
        (site/'reports'/'keep').write_text('keep')
        with self.assertRaises(ValueError):
            render_site({}, site, reports_dir=self.root/'missing', include_originals=True)
        self.assertEqual((site/'reports'/'keep').read_text(), 'keep')

    def test_asset_change_with_same_size_and_timestamp_is_copied(self):
        reports=self.root/'reports';reports.mkdir()
        source=reports/'same.txt';source.write_text('first')
        site=self.root/'site'
        render_site({},site,reports_dir=reports,include_originals=True)
        timestamp=source.stat().st_mtime_ns
        source.write_text('other')
        os.utime(source, ns=(timestamp,timestamp))
        render_site({},site,reports_dir=reports,include_originals=True)
        self.assertEqual((site/'reports'/'same.txt').read_text(),'other')

    def test_invalid_payload_preserves_both_pages_and_assets(self):
        site = self.root/'site'
        render_site({}, site)
        before = (site/'index.html').read_bytes()
        (site/'reports').mkdir()
        (site/'reports'/'keep').write_text('keep')
        with self.assertRaises(ValueError):
            render_site({'profile': {'age': float('nan')}}, site)
        self.assertEqual((site/'index.html').read_bytes(), before)
        self.assertTrue((site/'reports'/'keep').exists())

    def test_failed_ocr_exit_is_not_cached(self):
        helper=self.root/'helper.py'
        helper.write_text('import sys\nprint(\'{"text":"failed output"}\')\nsys.exit(1)\n')
        image=self.root/'page.png'
        image.write_bytes(b'fixture')
        cache=self.root/'cache'
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(vision_read([image], str(helper), cache), [])
        self.assertEqual(list(cache.iterdir()), [])

    def test_cli_bad_json_and_overlap_are_actionable_errors(self):
        data=self.root/'data';data.mkdir()
        (data/'analysis.json').write_text('{bad')
        with self.assertRaises(SystemExit) as exc:
            main(['render','--data',str(data),'--site',str(self.root/'site')])
        self.assertIn('littledocter:',str(exc.exception))
        with self.assertRaises(SystemExit):
            main(['run','--reports',str(data),'--data',str(data)])
        self.assertEqual((data/'analysis.json').read_text(),'{bad')

    def test_invalid_profile_does_not_replace_measurements(self):
        data=self.root/'data';data.mkdir()
        (data/'measurements.json').write_text(json.dumps({'saved':True}))
        profile=self.root/'profile.yaml';profile.write_text('name: [broken')
        reports=self.root/'reports';reports.mkdir()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            main(['run','--reports',str(reports),'--data',str(data),
                  '--site',str(self.root/'site'),'--profile',str(profile),'--no-vision'])
        self.assertEqual(json.loads((data/'measurements.json').read_text()),{'saved':True})


if __name__ == '__main__':
    unittest.main()
