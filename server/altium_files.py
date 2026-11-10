"""Text files exchanged with the Altium scripts.

Altium's TStringList.SaveToFile writes in the Windows ANSI code page, and
LoadFromFile reads that code page unless the file starts with a byte-order
mark. When the ANSI code page is UTF-8 (the "Beta: Use Unicode UTF-8"
region setting) SaveToFile also writes a UTF-8 byte-order mark, which
json.loads rejects (issue #20). So read the way LoadFromFile does, and write
UTF-8 with a byte-order mark, which LoadFromFile reads correctly whatever the
code page. Python's own default encoding is not used: it follows neither
rule (UTF-8 mode, or Python 3.15+, makes it UTF-8 on every machine).
"""
import codecs
import ctypes
import os
from pathlib import Path


def read_altium_text(path) -> str:
    """Text of a file written by the Altium scripts."""
    data = Path(path).read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        text = data[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    else:
        text = data.decode("mbcs", errors="replace")  # the ANSI code page
    return text.replace("\r\n", "\n")


def write_altium_text(path, text: str) -> None:
    """Write text for the Altium scripts to read."""
    Path(path).write_text(text, encoding="utf-8-sig")


def long_path(path):
    """path as Altium names the documents it opens: backslashes, and 8.3
    short names (a folder such as ABCDEF~1) expanded. Altium finds an open
    document by the path as written, so given forward slashes or a short
    name it opens a second copy of a document that is already open. Unlike
    os.path.realpath this leaves mapped drives and links as they are. A path
    that does not exist only gets backslashes."""
    if not path:
        return path
    path = os.path.normpath(str(path))
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetLongPathNameW(str(path), buf, len(buf))
    return buf.value if 0 < n < len(buf) else path


def spec_with_long_library(text):
    """A batch spec's text with each LIBRARY| path put through long_path, or
    None when every one is already in that form. The script opens or focuses
    the library by the path as written, so a short (8.3) name or forward
    slashes there would open a second copy of a library that is open."""
    lines = text.split("\n")
    changed = False
    for i, line in enumerate(lines):
        fields = line.split("|")
        if len(fields) < 2 or fields[0].strip().upper() != "LIBRARY":
            continue
        path = fields[1].strip()
        longer = long_path(path)
        if path and longer != path:
            fields[1] = longer
            lines[i] = "|".join(fields)
            changed = True
    return "\n".join(lines) if changed else None
