#!/usr/bin/env python3
"""launchd entry: same as `docsync` but with an absolute import path."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from docsync.cli import main  # noqa: E402

raise SystemExit(main())
