#!/usr/bin/env python3
"""littledocter — entry point.

    python3 littledocter.py run                          analyse reports/ and rebuild the page
    python3 littledocter.py run --reports sample_reports use the bundled demo record
    python3 littledocter.py check                        verify the knowledge base
    python3 littledocter.py marker ferritin              explain one marker + your history
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
