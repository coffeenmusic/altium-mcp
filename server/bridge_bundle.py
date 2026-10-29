"""Run the bridge script as one generated file instead of as a script project.

Starting the bridge with ScriptingSystem:RunScript on Altium_API.PrjScr takes
about 2.3 s before the script runs; the same units in one file, started with
ScriptingSystem:RunScriptFile, take about 0.8 s (measured on AD 26.11, #42).
The time goes into running a project, not into the amount of code.

One file needs what the project gave for free:

- Everything declared before it is used. DelphiScript binds the names in a
  routine as it reaches it, so a global const or var declared further down,
  or a routine defined further down, is an "Undeclared identifier" once the
  call runs. The units' global declarations go first, and the routines are
  ordered so that each comes after the routines it calls.
- A new name for new content. Altium runs the copy of a file it still holds
  open in its script editor (see sandbox.py), so the file is named after a
  hash of its content: changed units always give a file Altium has not seen.
"""

import hashlib
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

import sandbox

_ROUTINE = re.compile(r"^(function|procedure)\s+(\w+)", re.IGNORECASE)
_DECL = re.compile(r"^(const|var)\s*$", re.IGNORECASE)
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")

# Built files, per script project: the units' (path, mtime, size) they were made from
_built: Dict[Path, Tuple[tuple, Path]] = {}


def project_units(prjscr: Path) -> List[Path]:
    """The .pas files of a script project, in the project's order."""
    text = prjscr.read_text(encoding="utf-8-sig", errors="replace")
    names = re.findall(r"^DocumentPath=(.+?\.pas)\s*$", text, re.MULTILINE | re.IGNORECASE)
    return [prjscr.parent / name for name in names]


def split_unit(text: str) -> Tuple[List[str], List[str]]:
    """A unit's global const/var blocks, and its other lines.

    A const or var block at column 0 is global unless the last unindented
    code line before it is a routine header: routines in these units declare
    their locals at column 0 too, and a header may span several lines.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    decls, rest, last_top = [], [], ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if _DECL.match(line) and not _ROUTINE.match(last_top):
            block = [line]
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
                block.append(lines[i])
                i += 1
            decls.append("\n".join(block).rstrip())
            continue
        rest.append(line)
        if line.strip() and line[:1] not in " \t" and not line.startswith(("//", "{")):
            last_top = line
        i += 1
    return decls, rest


def split_routines(lines: List[str]) -> List[Tuple[str, str]]:
    """(name, text) of each routine, with the comment lines just above it.

    Raises ValueError for code outside any routine: it would have no place
    in a reordered file.
    """
    routines, current, pending = [], None, []
    for line in lines:
        m = _ROUTINE.match(line)
        if m:
            current = [m.group(2), pending + [line]]
            routines.append(current)
            pending = []
        elif not line.strip() or line.startswith(("//", "{")):
            pending.append(line)
        elif current is None:
            raise ValueError(f"code outside a routine: {line.strip()[:60]}")
        else:
            current[1] += pending + [line]
            pending = []
    return [(name, "\n".join(text).strip("\n")) for name, text in routines]


def _calls(routines: List[Tuple[str, str]]) -> List[set]:
    """For each routine, the indexes of the other routines it names."""
    index = {name.lower(): i for i, (name, _) in enumerate(routines)}
    calls = []
    for i, (_, text) in enumerate(routines):
        names = {w.lower() for w in _IDENT.findall(sandbox.strip_strings_and_comments(text))}
        calls.append({index[n] for n in names if n in index and index[n] != i})
    return calls


def order_routines(routines: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Routines with every routine after the ones it calls, otherwise in source order."""
    calls = _calls(routines)
    order, state = [], {}

    def visit(i):
        if i in state:
            return  # done, or a call cycle: the rest stays in source order
        state[i] = "visiting"
        for callee in sorted(calls[i]):
            visit(callee)
        state[i] = "done"
        order.append(i)

    for i in range(len(routines)):
        visit(i)
    return [routines[i] for i in order]


def forward_calls(routines: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """(caller, callee) pairs where the callee comes after the caller."""
    calls = _calls(routines)
    return [(routines[i][0], routines[j][0]) for i in range(len(routines)) for j in calls[i] if j > i]


def build(prjscr: Path) -> str:
    """The project's units as one file: global declarations, then the ordered routines."""
    decls, routines = [], []
    for unit in project_units(prjscr):
        unit_decls, rest = split_unit(unit.read_text(encoding="utf-8-sig"))
        decls += unit_decls
        routines += split_routines(rest)
    ordered = order_routines(routines)
    return (f"// Generated from {prjscr.name} by bridge_bundle.py. Edit the units, not this file.\n\n"
            + "\n\n".join(decls) + "\n\n" + "\n\n".join(text for _, text in ordered) + "\n")


def bundle_file(prjscr: Path, out_dir: Path, keep: int = 3) -> Path:
    """The generated file for the project's current units, written if new."""
    units = project_units(prjscr)
    stamp = tuple((str(u), u.stat().st_mtime_ns, u.stat().st_size) for u in units)
    cached = _built.get(prjscr)
    if cached and cached[0] == stamp and cached[1].exists():
        return cached[1]
    text = build(prjscr)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    path = out_dir / f"{prjscr.stem}_{digest}.pas"
    if not path.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8-sig")
        os.replace(tmp, path)
        _prune(out_dir, prjscr.stem, keep)
    _built[prjscr] = (stamp, path)
    return path


def _prune(out_dir: Path, stem: str, keep: int) -> None:
    """Keep only the newest generated files (another server may still use one)."""
    try:
        files = sorted(out_dir.glob(f"{stem}_*.pas"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
