"""Read 3D body model placement from a saved .PcbLib file.

Altium's script API cannot report how a 3D body's model is placed:
IPCB_Model.GetState returns the rotations and Z offset through out
parameters, and DelphiScript never receives their values. The saved library
stores them in each body record (MODEL.3D.ROTX, ROTY, ROTZ, DZ), so
get_footprint_primitives reads them from the file. A body that is not saved
yet has no placement to read.

A .PcbLib is an OLE compound file: one storage per footprint, whose
Parameters stream names the footprint (PATTERN=) and whose Data stream holds
its primitives. A body record's properties are one null-terminated text of
|KEY=VALUE pairs.
"""

import os
import re
import shutil
import tempfile

import pythoncom
from win32com import storagecon as sc

_OPEN = sc.STGM_READ | sc.STGM_SHARE_DENY_WRITE
_CHILD = sc.STGM_READ | sc.STGM_SHARE_EXCLUSIVE
_STORAGE = 1  # STGTY_STORAGE
_BODY = re.compile(rb"V7_LAYER=[^\x00]*?\|MODELID=[^\x00]*")
_PATTERN = re.compile(rb"\|PATTERN=([^|\x00]*)")


def _fields(record):
    """|KEY=VALUE text -> {KEY: VALUE}, first occurrence of each key."""
    out = {}
    for part in record.decode("latin-1").split("|"):
        key, sep, value = part.partition("=")
        if sep:
            out.setdefault(key.strip().upper(), value.strip())
    return out


def _mils(value):
    """'12.5mil' or '0.3mm' -> mils; None when unreadable."""
    m = re.match(r"\s*(-?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\s*(mil|mm)?\s*$", value or "")
    if not m:
        return None
    number = float(m.group(1))
    return round(number / 0.0254 if m.group(2) == "mm" else number, 3)


def _degrees(value):
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _read_stream(storage, name):
    stream = storage.OpenStream(name, None, _CHILD, 0)
    return stream.Read(stream.Stat()[2])


def _open_root(path):
    """Open the compound file for reading. Altium may hold it open; if it
    cannot be shared, read a temporary copy. Returns (root, temp_path)."""
    try:
        return pythoncom.StgOpenStorage(str(path), None, _OPEN), None
    except pythoncom.com_error:
        fd, temp = tempfile.mkstemp(suffix=".PcbLib")
        os.close(fd)
        shutil.copyfile(path, temp)
        return pythoncom.StgOpenStorage(temp, None, _OPEN), temp


def _parse_bodies(data):
    bodies = []
    for m in _BODY.finditer(data):
        f = _fields(m.group(0))
        bodies.append({
            "model_name": f.get("MODEL.NAME", ""),
            "embedded": f.get("MODEL.EMBED", "").upper() == "TRUE",
            "rotation_x": _degrees(f.get("MODEL.3D.ROTX")),
            "rotation_y": _degrees(f.get("MODEL.3D.ROTY")),
            "rotation_z": _degrees(f.get("MODEL.3D.ROTZ")),
            "model_z_offset": _mils(f.get("MODEL.3D.DZ")),
        })
    return bodies


def _read_bodies(root, footprints):
    wanted = {name.upper() for name in footprints}
    found = {}
    names = [e[0] for e in root.EnumElements() if e[1] == _STORAGE]
    # Storage names are footprint names cut to 31 characters: check those
    # first, then the rest (the PATTERN parameter decides)
    likely = [n for n in names if any(w.startswith(n.upper()) for w in wanted)]
    for name in likely + [n for n in names if n not in likely]:
        if len(found) == len(wanted):
            break
        storage = root.OpenStorage(name, None, _CHILD, None, 0)
        try:
            m = _PATTERN.search(_read_stream(storage, "Parameters"))
        except pythoncom.com_error:
            continue
        pattern = m.group(1).decode("latin-1").upper() if m else ""
        if pattern in wanted and pattern not in found:
            found[pattern] = _parse_bodies(_read_stream(storage, "Data"))
    return found


def body_placements(path, footprints):
    """Model placement of each 3D body of the named footprints in the saved
    file: {FOOTPRINT NAME (upper case): [{model_name, embedded, rotation_x,
    rotation_y, rotation_z, model_z_offset}]} in file order (degrees, mils).
    Footprints that are not in the file are left out; {} when the file
    cannot be read."""
    temp = None
    root = None
    try:
        root, temp = _open_root(path)
        return _read_bodies(root, footprints)
    except (pythoncom.com_error, OSError):
        return {}
    finally:
        root = None
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass


_PLACEMENT = ("rotation_x", "rotation_y", "rotation_z", "model_z_offset")


def merge_placements(bodies, saved):
    """Copy rotations and Z offset from the saved records onto the bodies
    Altium reported. Each body takes the first unused saved record with the
    same model file name, so bodies not saved yet (or changed since) are
    marked rather than given another body's placement."""
    used = set()
    for body in bodies:
        match = None
        for i, record in enumerate(saved or []):
            if i not in used and record["model_name"].upper() == str(body.get("model_file", "")).upper():
                match = i
                break
        if match is None:
            for key in _PLACEMENT:
                body[key] = None
            body["placement"] = "not in the saved library file"
        else:
            used.add(match)
            for key in _PLACEMENT:
                body[key] = saved[match][key]
            body["placement"] = "saved library file"
    return bodies


def add_body_placements(result):
    """Fill the model rotations and Z offset of every bodies_3d list in a
    get_footprint_primitives result from its saved library file."""
    path = result.get("library_path", "")
    footprints = [result] if "bodies_3d" in result else result.get("footprints") or []
    with_bodies = [fp for fp in footprints if fp.get("bodies_3d") and fp.get("footprint_name")]
    if not path or not with_bodies:
        return result
    saved = body_placements(path, [fp["footprint_name"] for fp in with_bodies])
    for fp in with_bodies:
        merge_placements(fp["bodies_3d"], saved.get(fp["footprint_name"].upper()))
    return result
