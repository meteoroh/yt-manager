#!/usr/bin/env python3
import sys
from pathlib import Path

# Ensure src/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from yt_manager.tools.migrate_filenames import main

if __name__ == "__main__":
    main()
