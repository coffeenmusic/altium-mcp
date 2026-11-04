"""Appending rows to a database library's Excel table (.xlsx).

Pure Python, unit-tested in tests/test_dblib_write.py. The workbook is
changed as little as possible, so nothing Excel or the library's author set
up is lost: only the sheet's XML gets the new <row> elements (each cell
styled like the cell above it in that column), the sheet's dimension and
autofilter, and any table on the sheet are extended to cover them. Every
other part of the zip is copied byte for byte. Values are written as text
(inline strings), as a DbLib reads them, or as numbers when given as JSON
numbers.

The result is written next to the file and moved over it only after it reads
back right: the old rows unchanged and the new ones as given.
"""
import os
import re
import shutil
import tempfile
import zipfile
from typing import Dict, List, Tuple
from xml.sax.saxutils import escape

import dblib


class WriteError(ValueError):
    pass


def _col_letters(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_part(z: zipfile.ZipFile, sheet: str) -> str:
    book = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid = None
    for m in re.finditer(r"<sheet\b[^>]*>", book):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        if name and name.group(1).lower() == sheet.lower():
            rid = re.search(r'\br:id="([^"]*)"|\bid="([^"]*)"', tag)
            rid = rid.group(1) or rid.group(2)
    if rid is None:
        raise WriteError(f"no sheet '{sheet}' in the workbook")
    for m in re.finditer(r"<Relationship\b[^>]*>", rels):
        tag = m.group(0)
        if re.search(rf'\bId="{re.escape(rid)}"', tag):
            target = re.search(r'\bTarget="([^"]*)"', tag).group(1).lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise WriteError(f"sheet '{sheet}' has no part")


def _rows(sheet_xml: str):
    """(row number, row xml) of every row in sheetData."""
    return [(int(m.group(1)), m.group(0))
            for m in re.finditer(r'<row\b[^>]*\br="(\d+)"[^>]*?(?:/>|>.*?</row>)', sheet_xml, re.S)]


def _cell_styles(row_xml: str) -> Dict[str, Tuple[str, bool]]:
    """{column letters: (style index, holds a number)} of a row's cells."""
    out = {}
    for m in re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"[^>]*>', row_xml):
        tag = m.group(0)
        s = re.search(r'\bs="(\d+)"', tag)
        t = re.search(r'\bt="(\w+)"', tag)
        out[m.group(1)] = (s.group(1) if s else "", t is None or t.group(1) == "n")
    return out


# Excel's driver (ACE, IMEX=1) types a column from its first 8 data rows:
# numeric when every value there is a number. A text cell in such a column
# then reads as empty, so Altium never sees it.
GUESS_ROWS = 8


def _cell_types(row_xml: str) -> Dict[str, bool]:
    """{column letters: holds a number} of a row's cells that have a value."""
    out = {}
    for m in re.finditer(r'<c\b([^>]*\br="([A-Z]+)\d+"[^>]*?)(/>|>(.*?)</c>)', row_xml, re.S):
        attrs, col, inner = m.group(1), m.group(2), m.group(4) or ""
        if "<v>" not in inner and "<t" not in inner:
            continue
        t = re.search(r'\bt="(\w+)"', attrs)
        out[col] = t is None or t.group(1) == "n"
    return out


def numeric_columns(sheet_xml: str) -> Dict[str, bool]:
    """{column letters: typed numeric by the driver}, from the first data
    rows (the header row excluded)."""
    rows = _rows(sheet_xml)[1:1 + GUESS_ROWS]
    seen: Dict[str, List[bool]] = {}
    for _, row in rows:
        for col, numeric in _cell_types(row).items():
            seen.setdefault(col, []).append(numeric)
    return {col: all(v) for col, v in seen.items() if v}


def text_in_numeric_columns(xlsx: str, sheet: str) -> List[dict]:
    """Cells holding text in a column the driver types numeric: Altium
    reads them as empty. [{row, column}]"""
    with zipfile.ZipFile(xlsx) as z:
        xml = z.read(_sheet_part(z, sheet)).decode("utf-8")
    numeric = numeric_columns(xml)
    out = []
    for n, row in _rows(xml)[1:]:
        for col, is_number in _cell_types(row).items():
            if numeric.get(col) and not is_number:
                out.append({"row": n, "column": col})
    return out


def as_number(value) -> object:
    """A text that is a number ("25", "0,5", "10%") as the number Excel
    stores (10% is 0.1); other values unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip().replace(" ", "")
    percent = text.endswith("%")
    text = text.rstrip("%").replace(",", ".")
    if not re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?", text, re.I):
        return value
    number = float(text) / (100 if percent else 1)
    if not percent and number.is_integer() and "." not in text:
        return int(number)
    return round(number, 12)


def _extend_ref(ref: str, last_row: int) -> str:
    m = re.match(r"([A-Z]+\d+):([A-Z]+)(\d+)$", ref)
    if not m:
        return ref
    return f"{m.group(1)}:{m.group(2)}{max(int(m.group(3)), last_row)}"


def _cell(col: str, row: int, value, style: str) -> str:
    s = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        value = str(value)
    if isinstance(value, (int, float)):
        return f'<c r="{col}{row}"{s}><v>{value!r}</v></c>' if isinstance(value, float) \
            else f'<c r="{col}{row}"{s}><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{col}{row}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def append_rows(xlsx: str, sheet: str, headers: Dict[int, str], rows: List[dict], out_path: str) -> List[int]:
    """Write xlsx with rows appended to sheet into out_path; the new rows'
    numbers. headers is {column index: name}, from the header row."""
    col_of = {h: _col_letters(i) for i, h in headers.items()}
    with zipfile.ZipFile(xlsx) as z:
        part = _sheet_part(z, sheet)
        xml = z.read(part).decode("utf-8")
        existing = _rows(xml)
        last = max((n for n, _ in existing), default=0)
        styles = _cell_styles(existing[-1][1]) if len(existing) > 1 else {}
        numeric_cols = numeric_columns(xml)
        new_xml, numbers = [], []
        for i, row in enumerate(rows):
            n = last + 1 + i
            numbers.append(n)
            cells = []
            for i in sorted(headers):
                h = headers[i]
                if h in row and row[h] not in (None, ""):
                    style = styles.get(col_of[h], ("", False))[0]
                    # text in a numeric column would read back empty
                    value = as_number(row[h]) if numeric_cols.get(col_of[h]) else row[h]
                    cells.append(_cell(col_of[h], n, value, style))
            new_xml.append(f'<row r="{n}">{"".join(cells)}</row>')
        added = "".join(new_xml)
        if "</sheetData>" in xml:
            xml = xml.replace("</sheetData>", added + "</sheetData>", 1)
        elif "<sheetData/>" in xml:
            xml = xml.replace("<sheetData/>", f"<sheetData>{added}</sheetData>", 1)
        else:
            raise WriteError("the sheet has no sheetData")
        new_last = last + len(rows)
        xml = re.sub(r'(<dimension\b[^>]*\bref=")([^"]*)(")',
                     lambda m: m.group(1) + _extend_ref(m.group(2), new_last) + m.group(3), xml, count=1)
        xml = re.sub(r'(<autoFilter\b[^>]*\bref=")([^"]*)(")',
                     lambda m: m.group(1) + _extend_ref(m.group(2), new_last) + m.group(3), xml, count=1)
        # Tables on the sheet (Insert > Table) grow with it
        changed = {part: xml.encode("utf-8")}
        rels_part = part.rsplit("/", 1)[0] + "/_rels/" + part.rsplit("/", 1)[1] + ".rels"
        if rels_part in z.namelist():
            rels = z.read(rels_part).decode("utf-8")
            for m in re.finditer(r'Target="([^"]*tables/[^"]*)"', rels):
                target = os.path.normpath(os.path.join(os.path.dirname(part), m.group(1))).replace("\\", "/")
                if target in z.namelist():
                    t = z.read(target).decode("utf-8")
                    t = re.sub(r'(<table\b[^>]*\bref=")([^"]*)(")',
                               lambda mm: mm.group(1) + _extend_ref(mm.group(2), new_last) + mm.group(3), t, count=1)
                    t = re.sub(r'(<autoFilter\b[^>]*\bref=")([^"]*)(")',
                               lambda mm: mm.group(1) + _extend_ref(mm.group(2), new_last) + mm.group(3), t, count=1)
                    changed[target] = t.encode("utf-8")
        with zipfile.ZipFile(out_path, "w") as out:
            for info in z.infolist():
                data = changed.get(info.filename)
                if data is None:
                    out.writestr(info, z.read(info.filename))
                else:
                    out.writestr(info, data)
    return numbers


def add_rows(dblib_path: str, table: str, rows: List[dict], backup_dir: str) -> dict:
    """Append rows to a DbLib table, checked; the backup's path and keys."""
    lib = dblib.read_dblib(dblib_path)
    xlsx = lib["database"]
    if not xlsx.lower().endswith((".xlsx", ".xlsm")):
        raise WriteError(f"only Excel tables can be written; this DbLib uses {xlsx}")
    wanted = table if table.endswith("$") else table + "$"
    tables = [t for t in lib["tables"] if t.lower() == wanted.lower()]
    if not tables:
        raise WriteError(f"no enabled table '{table}' in {lib['name']}; tables: {', '.join(lib['tables'])}")
    sheet = tables[0].rstrip("$")
    headers, before = dblib.read_xlsx_table(xlsx, sheet)
    if not headers:
        raise WriteError(f"sheet {sheet} has no header row")
    key = dblib.key_field(lib, tables[0], before) or headers[min(headers)]
    names = set(headers.values())
    if not rows:
        raise WriteError("rows is empty")
    keys = set(r.get(key, "").strip() for r in before)
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise WriteError(f"row {i + 1} must be an object of column: value")
        unknown = [c for c in row if c not in names]
        if unknown:
            raise WriteError(f"row {i + 1}: no column {', '.join(unknown)} in {sheet}; "
                             f"columns: {', '.join(headers[c] for c in sorted(headers))}")
        k = str(row.get(key, "")).strip()
        if not k:
            raise WriteError(f"row {i + 1} has no {key} (the key)")
        if k in keys or k in seen:
            raise WriteError(f"row {i + 1}: {key} '{k}' is already in {sheet}")
        seen.add(k)
    os.makedirs(backup_dir, exist_ok=True)
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(backup_dir, f"{os.path.splitext(os.path.basename(xlsx))[0]}_{stamp}.xlsx")
    shutil.copy2(xlsx, backup)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(xlsx))
    os.close(fd)
    try:
        numbers = append_rows(xlsx, sheet, headers, rows, tmp)
        after = dblib.read_xlsx_sheet(tmp, sheet)
        if after[:len(before)] != before:
            raise WriteError("the existing rows did not read back unchanged; nothing was written")
        for row, got in zip(rows, after[len(before):]):
            for col, value in row.items():
                want = "" if value is None else str(value).strip()
                if isinstance(value, float):
                    want = dblib._number(value, dblib.decimal_separator())
                if as_number(want) != want and as_number(got.get(col, "")) == as_number(want):
                    continue        # the same number, shown with the column's format
                if got.get(col, "").strip() != want:
                    raise WriteError(f"{col} of {row.get(key)} read back as '{got.get(col)}', not '{want}'; "
                                     "nothing was written")
        if len(after) != len(before) + len(rows):
            raise WriteError("the new rows did not read back; nothing was written")
        os.replace(tmp, xlsx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"database": xlsx, "sheet": sheet, "key": key, "added": [str(r[key]) for r in rows],
            "rows": numbers, "backup": backup}
