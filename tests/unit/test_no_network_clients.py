"""No cloud / HTTP client imports in package."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "maccluster"
FORBIDDEN = {"requests", "httpx", "aiohttp", "urllib3", "openai", "anthropic", "boto3"}


def test_no_forbidden_imports():
    found: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".")[0]
                    if name in FORBIDDEN:
                        found.append(f"{path}:{name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                name = node.module.split(".")[0]
                if name in FORBIDDEN:
                    found.append(f"{path}:{name}")
    assert found == []
