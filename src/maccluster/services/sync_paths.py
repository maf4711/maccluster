"""Path validation shared by SSH and RDMA transfer boundaries."""

from pathlib import Path


def validate_relpath(rel: object) -> str:
    """Reject paths that escape a sync root or corrupt a line-based manifest."""
    if not isinstance(rel, str) or not rel:
        raise ValueError(f"unsafe manifest rel {rel!r}: empty or not a string")
    if rel.startswith("/"):
        raise ValueError(f"unsafe manifest rel {rel!r}: absolute path")
    if any(part in ("", ".", "..") for part in rel.split("/")):
        raise ValueError(f"unsafe manifest rel {rel!r}: '.', '..' or empty component")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel):
        raise ValueError(f"unsafe manifest rel {rel!r}: control character")
    rel.encode("utf-8")
    return rel


def contained_path(root: Path, rel: str) -> Path:
    """Keep parents inside root, without dereferencing the final symlink."""
    path = root / validate_relpath(rel)
    if not path.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"sync path escapes root through a symlink: {rel!r}")
    return path
