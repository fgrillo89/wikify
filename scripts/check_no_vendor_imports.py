"""Fail CI if any wikify_simple module other than bindings/file_dispatch.py
references the file-dispatch binding or imports the anthropic SDK.

Run via: ``uv run python scripts/check_no_vendor_imports.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "wikify_simple"
ALLOWED_FILES = {ROOT / "dispatch.py"}

FORBIDDEN_PATTERNS = (
    "import anthropic",
    "from anthropic",
    "WIKIFY_SIMPLE_DISPATCH_DIR",
    "file_dispatch binding",
    "subagent_dispatcher",
)


def main() -> int:
    failures: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in FORBIDDEN_PATTERNS:
            if needle in text:
                failures.append(f"{path}: forbidden pattern {needle!r}")
    if failures:
        print("vendor-import check FAILED:")
        for f in failures:
            print("  ", f)
        return 1
    print(f"vendor-import check OK ({len(list(ROOT.rglob('*.py')))} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
