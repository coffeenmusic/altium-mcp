"""Whether a file can be written now, and which programs hold it.

Made for database libraries: a .DbLib reads its parts from a spreadsheet or
an Access file (its ConnectionString's Data Source), and writing rows there
fails while another program holds it. Excel holds a workbook it has open;
Altium's database driver holds the file while it reads it. Windows' Restart
Manager lists the processes that hold a file, without opening it.
"""

import ctypes
import os
import re
import stat
from ctypes import wintypes
from typing import List, Optional


def dblib_source(dblib_path: str) -> Optional[str]:
    """The data file a .DbLib reads: its ConnectionString's Data Source (or
    Dbq), resolved against the .DbLib's folder when it is relative. None for a
    server database or when there is none."""
    with open(dblib_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r"^ConnectionString=(.*)$", text, re.MULTILINE)
    if not m:
        return None
    src = re.search(r"(?:Data Source|Dbq)\s*=\s*(\"[^\"]*\"|[^;]*)", m.group(1), re.IGNORECASE)
    if not src:
        return None
    path = src.group(1).strip().strip('"')
    if not path or not re.search(r"\.(xlsx|xlsm|xls|accdb|mdb|csv)$", path, re.IGNORECASE):
        return None
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(os.path.abspath(dblib_path)), path)
    # DatabasePathRelative=1 keeps the file next to the .DbLib when the
    # absolute path no longer exists (the library was moved)
    if not os.path.exists(path) and re.search(r"^DatabasePathRelative=1", text, re.MULTILINE):
        near = os.path.join(os.path.dirname(os.path.abspath(dblib_path)), os.path.basename(path))
        if os.path.exists(near):
            path = near
    return path


_REASONS = {5: "access_denied", 32: "in_use", 33: "in_use"}   # Windows error codes


def write_access(path: str) -> str:
    """'' when the file opens for writing with nobody else sharing it, else
    why not: "read_only" (its read-only attribute), "in_use" (another
    program has it open), "access_denied" (no permission) or "error N".
    The test open is exclusive, so for that instant (it is closed at once)
    nobody else can open the file."""
    if getattr(os.stat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_READONLY:
        return "read_only"
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)  # read|write, no sharing, open existing
    if h == wintypes.HANDLE(-1).value:
        err = ctypes.get_last_error()
        return _REASONS.get(err, f"error {err}")
    k.CloseHandle(h)
    return ""


def can_write(path: str) -> bool:
    """True when the file opens for writing with nobody else sharing it."""
    return write_access(path) == ""


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("Process", _UniqueProcess), ("strAppName", wintypes.WCHAR * 256),
                ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", wintypes.DWORD),
                ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]


def holders(path: str) -> List[dict]:
    """Processes holding the file: [{pid, name}] (empty when none, or when
    Windows cannot tell)."""
    rm = ctypes.windll.rstrtmgr
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(64)
    if rm.RmStartSession(ctypes.byref(session), 0, key):
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None):
            return []
        info = (_ProcessInfo * 16)()
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(16), wintypes.DWORD()
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), info, ctypes.byref(reasons)):
            return []
        return [{"pid": info[i].Process.dwProcessId, "name": info[i].strAppName}
                for i in range(count.value)]
    finally:
        rm.RmEndSession(session)


def office_owner_file(path: str) -> Optional[str]:
    """Excel's '~$name' file next to a workbook it has open, if there is one."""
    folder, name = os.path.split(path)
    owner = os.path.join(folder, "~$" + name[2:] if len(name) > 2 else "~$" + name)
    for candidate in (owner, os.path.join(folder, "~$" + name)):
        if os.path.exists(candidate):
            return candidate
    return None


def lock_report(path: str) -> dict:
    """Whether `path` can be written now, and who holds it."""
    if not os.path.exists(path):
        return {"path": path, "exists": False}
    held = holders(path)
    # When Windows already names a holder, the file is not opened at all, so
    # the check does not lock it, even for an instant
    reason = "in_use" if held else write_access(path)
    writable = reason == ""
    report = {"path": path, "exists": True, "writable": writable, "held_by": held}
    if not writable:
        report["reason"] = reason
    owner = office_owner_file(path)
    if owner:
        report["office_owner_file"] = owner
        if writable and not held:
            report["office_owner_note"] = ("Nothing holds the workbook, so this is left over from an "
                                           "Excel session that ended without closing it; it does not "
                                           "block writing.")
    if reason == "read_only":
        report["advice"] = "The file is read-only (its attribute): clear it in the file's properties first."
    elif reason == "access_denied":
        report["advice"] = ("Windows denies write access: check the file's and folder's permissions, or "
                            "whether a sync or backup tool locks it.")
    elif not writable:
        names = {h["name"].lower() for h in held}
        if any("altium" in n or "x2" in n for n in names):
            report["advice"] = ("Altium holds the file, likely its database driver reading parts. Check "
                                "again once Altium is idle; if it stays held, close the library's "
                                "documents in Altium.")
        elif any("excel" in n for n in names) or owner:
            report["advice"] = "Excel has the workbook open: save and close it there first."
        else:
            report["advice"] = "Another program holds the file: close it there first."
    return report
