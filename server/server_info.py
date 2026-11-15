"""Dependency-free build identity and startup snapshots for diagnostics."""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import platform
import re
import sys


INSTALLATION_DIR = Path(__file__).resolve().parents[1]


def _project_version(root):
    # Keep pyproject.toml as the version source, including on Python 3.10.
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    project = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
    if project:
        match = re.search(r'''(?m)^version\s*=\s*["']([^"']+)["']\s*(?:#.*)?$''',
                          project.group(1))
        if match:
            return match.group(1)
    return "unknown"


def _source_sha256(root):
    paths = [root / name for name in ("start_server.py", "pyproject.toml", "requirements.txt")]
    paths.extend((root / "server").glob("*.py"))
    paths.append(root / "server" / "symbol_placement_rules.txt")
    for name in ("AltiumScript", "SandboxScript"):
        paths.extend(path for path in (root / "server" / name).rglob("*")
                     if path.suffix.lower() in (".pas", ".prjscr"))
    digest = hashlib.sha256()
    try:
        for path in sorted(paths):
            if not path.is_file():
                continue
            content = path.read_bytes()
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    except OSError:
        return None
    return digest.hexdigest()


def get_build_info(root=None):
    """Describe files currently on disk without starting the MCP server."""
    root = Path(root or INSTALLATION_DIR).resolve()
    return {
        "server_version": _project_version(root),
        "source_sha256": _source_sha256(root),
        "installation_path": str(root),
        "server_path": str(root / "server" / "main.py"),
    }


def capture_runtime_info(root=None):
    """Call once at startup so later file updates do not relabel a live process."""
    try:
        sdk_version = version("mcp")
    except PackageNotFoundError:
        sdk_version = "unknown"
    return {
        **get_build_info(root),
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "mcp_sdk_version": sdk_version,
    }
