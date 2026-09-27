"""Build a deterministic source-only release; never includes personal or generated data."""
from __future__ import annotations

import argparse
import hashlib
import stat
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import __version__  # noqa: E402

FILES = ('.gitignore', 'AGENTS.md', 'README.md', 'littledocter.py',
         'requirements.txt', 'requirements-dev.txt', 'data/profile.example.yaml',
         'reports/README.md')
FOLDERS = ('src', 'web', 'knowledge', 'sample_reports', 'tests', 'tools', 'docs')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT.parent / f'littledocter-{__version__}-source.zip')
    args = parser.parse_args()
    destination = args.out.expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents:
        parser.error('write the release outside the source repository')
    files = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        for path in (ROOT / folder).rglob('*'):
            if path.is_symlink():
                parser.error(f'symbolic link cannot be packaged: {path}')
            if path.is_file() and '__pycache__' not in path.parts and not path.name.startswith('.') and path.suffix != '.pyc':
                files.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files):
            if path.is_symlink():
                parser.error(f'symbolic link cannot be packaged: {path}')
            item = zipfile.ZipInfo(str(Path('littledocter') / path.relative_to(ROOT)))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, path.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('release ZIP integrity verification failed')
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(f'{checksum}  {destination.name}\n', encoding='utf-8')
    print(f'{destination}\nSHA256 {checksum}\n{len(files)} source files')


if __name__ == '__main__':
    main()
