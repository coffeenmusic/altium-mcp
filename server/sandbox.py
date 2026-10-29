"""Building, checking and running run_altium_script sandbox scripts.

Pure-Python parts (template filling, the variable block, the pre-run check and
line mapping) are unit-tested in tests/test_sandbox.py. The Windows parts talk
to the running Altium (X2.EXE) only through its command line and through
messages to its own dialog windows, so they never depend on which window has
the keyboard focus.

Why each run gets its own file
------------------------------
Running Sandbox.pas as a script *project* (-RScriptingSystem:RunScript with
ProjectName=...) makes Altium compile the copy it holds open in its script
editor, not the file on disk. After a failure that copy is the broken script,
so later runs repeat the old error or return an old result (see PR #13).
ScriptingSystem:RunScriptFile on a file name that was never used before has no
such copy to fall back on.

What a failing script looks like
--------------------------------
Compile errors and run-time errors (including a call on a nil interface,
which DelphiScript reports as "Undeclared identifier: <method>") both open a
#32770 dialog owned by X2.EXE whose text ends in "Continue execution?". While
it is open every later script run is ignored. Answering it with Cancel stops
the script; -REditScript:Stop then clears any paused debugger state.

Why loops are guarded
---------------------
A script that is still running cannot be stopped at all: it runs on Altium's
UI thread, and both -REditScript:Stop and answering dialogs are window
messages that thread no longer reads. A loop that never ends therefore
freezes Altium until X2.EXE is killed (issue #37). build_script makes every
while and repeat loop also call the template's SandboxTick, which ends all
loops once a deadline has passed, so the script finishes on its own.
"""

import ctypes
import itertools
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

BODY_BEGIN = "// === BEGIN EXPERIMENT"
BODY_END = "// === END EXPERIMENT"
VARS_BEGIN = "// === USER VARS"
VARS_END = "// === END USER VARS"
EXCEPTION_PREFIX = "EXCEPTION: "   # logged by the template's except block
GUARD_PREFIX = "LOOP GUARD: "       # logged by SandboxTick when it ends the loops
LOG_TOKEN = "%LOG_PATH%"
OUT_TOKEN = "%OUT_PATH%"
GUARD_TOKEN = "%GUARD_SECONDS%"
BODY_INDENT = " " * 8
VARS_INDENT = " " * 4

# Names a script may assign without declaring them: the template's globals and
# Run's own locals. Kept in sync with SandboxScript/Sandbox.pas by
# test_template_declares_scratch_names.
TEMPLATE_NAMES = (
    ["S%d" % i for i in range(1, 10)] + ["I%d" % i for i in range(1, 10)]
    + ["B1", "B2", "B3", "F1", "F2", "F3", "F4"]
    + ["Obj%d" % i for i in range(1, 10)] + ["List1", "List2", "List3"]
    + ["IntMan", "DbDoc", "ResultText", "LogLines", "LogPath", "OutPath", "OutLines"]
)

_DECL_RE = re.compile(r"^\s*[A-Za-z_]\w*(\s*,\s*[A-Za-z_]\w*)*\s*:\s*[A-Za-z_][\w.]*\s*;\s*(//.*)?$")
_ASSIGN_RE = re.compile(r"(?<![\w.\]\)])([A-Za-z_]\w*)\s*:=")
_LINE_RE = re.compile(r"at Line:\s*(\d+),\s*Char:\s*(\d+)", re.IGNORECASE)


@dataclass
class SplitScript:
    decls: List[str]              # declaration lines of the leading var block
    decl_src: List[int]           # 1-based line of each declaration in the submitted script
    body: List[str]
    body_offset: int              # submitted-script line number = body index (1-based) + offset


def split_var_block(script: str) -> SplitScript:
    """Split an optional leading `var` block off a script.

    The block is the word `var` on a line of its own (or followed by the first
    declaration), then declaration lines of the form `Name1, Name2 : Type;`.
    It ends at the first line that is not a declaration.
    """
    lines = script.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i == len(lines) or not re.match(r"^\s*var\b", lines[i], re.IGNORECASE):
        return SplitScript([], [], lines, 0)
    decls, src = [], []
    first = re.sub(r"^\s*var\b", "", lines[i], flags=re.IGNORECASE).strip()
    if first:
        decls.append(first)
        src.append(i + 1)
    i += 1
    while i < len(lines) and (_DECL_RE.match(lines[i]) or not lines[i].strip()):
        if lines[i].strip():
            decls.append(lines[i].strip())
            src.append(i + 1)
        i += 1
    return SplitScript(decls, src, lines[i:], i)


def declared_names(decls: List[str]) -> List[str]:
    names = []
    for d in decls:
        names += [n.strip() for n in d.split(":", 1)[0].split(",") if n.strip()]
    return names


def strip_strings_and_comments(code: str) -> str:
    """Pascal source with string literals and comments blanked out (line count kept)."""
    out = []
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "'":
            j = i + 1
            while j < n:
                if code[j] == "'" and j + 1 < n and code[j + 1] == "'":
                    j += 2
                    continue
                if code[j] == "'" or code[j] == "\n":
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
        elif c == "{" or code.startswith("(*", i):
            end = "}" if c == "{" else "*)"
            j = code.find(end, i + 1)
            j = n if j < 0 else j + len(end)
            out.append("\n" * code.count("\n", i, j))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _blank_strings_and_comments(code: str) -> str:
    """Pascal source with string literals and comments replaced by spaces.

    Unlike strip_strings_and_comments every character keeps its offset, so a
    position found in the result is the same position in `code`.
    """
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "'":
            j = i + 1
            while j < n and code[j] != "\n":
                if code[j] == "'" and j + 1 < n and code[j + 1] == "'":
                    j += 2
                    continue
                if code[j] == "'":
                    break
                j += 1
            end = min(j + 1, n)
        elif code.startswith("//", i):
            j = code.find("\n", i)
            end = n if j < 0 else j
        elif c == "{" or code.startswith("(*", i):
            close = "}" if c == "{" else "*)"
            j = code.find(close, i + 1)
            end = n if j < 0 else j + len(close)
        else:
            i += 1
            continue
        for k in range(i, end):
            if out[k] != "\n":
                out[k] = " "
        i = end
    return "".join(out)


_LOOP_RE = re.compile(r"(?<![\w.])(while|until)\b", re.IGNORECASE)
_DO_RE = re.compile(r"\bdo\b", re.IGNORECASE)
# What can follow the condition of `until`: the statement's end
_UNTIL_END_RE = re.compile(r"[()\[\];]|\b(?:end|else|except|finally|until)\b", re.IGNORECASE)


def _until_end(blank: str, start: int) -> int:
    depth = 0
    for m in _UNTIL_END_RE.finditer(blank, start):
        tok = m.group(0)
        if tok in "([":
            depth += 1
        elif tok in ")]":
            depth -= 1
        elif depth <= 0:
            return m.start()
    return len(blank)


def guard_loops(code: str, first_line: int) -> str:
    """Make every while and repeat loop in `code` also call SandboxTick.

        while C do        ->  while SandboxTick(L) and (C) do
        repeat .. until C ->  repeat .. until (not SandboxTick(L)) or (C)

    L is the line of the loop's `while` or `until` in the submitted script
    (`first_line` is the line of the first line of `code`). The condition keeps its own lines, so line
    numbers in Altium's messages still map back to the submitted script; only
    the column moves on a line that holds a loop condition. for loops are left
    alone: their bounds are fixed when they start.
    """
    blank = _blank_strings_and_comments(code)
    edits = []
    for m in _LOOP_RE.finditer(blank):
        start = m.end()
        if m.group(1).lower() == "while":
            do = _DO_RE.search(blank, start)
            if not do:
                continue  # incomplete: leave it to the compiler
            end = do.start()
        else:
            end = _until_end(blank, start)
        while start < end and code[start].isspace():
            start += 1
        while end > start and code[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        line = first_line + code.count("\n", 0, m.start())
        cond = code[start:end]
        if m.group(1).lower() == "while":
            edits.append((start, end, f"SandboxTick({line}) and ({cond})"))
        else:
            edits.append((start, end, f"(not SandboxTick({line})) or ({cond})"))
    for start, end, new in reversed(edits):
        code = code[:start] + new + code[end:]
    return code


def check_script(body_lines: List[str], extra_names: List[str], line_offset: int = 0) -> List[str]:
    """Problems that would stop the script from compiling, found before Altium sees it.

    Only assignments to plain names are checked (`X := ...`, `for X := ...`):
    a name that is neither a sandbox variable nor declared in the script's
    `var` block always fails with "Undeclared identifier". Property and array
    assignments (`Obj.X :=`, `A[i] :=`) are not checked. A script using `with`
    is not checked at all: inside `with Obj do X := ...`, X is Obj's property.
    """
    known = {n.lower() for n in TEMPLATE_NAMES + extra_names}
    code = strip_strings_and_comments("\n".join(body_lines))
    if re.search(r"\bwith\b", code, re.IGNORECASE):
        return []
    problems = []
    for lineno, line in enumerate(code.splitlines(), 1):
        for m in _ASSIGN_RE.finditer(line):
            name = m.group(1)
            if name.lower() not in known:
                problems.append(f"line {lineno + line_offset}: '{name}' is not declared. Use a sandbox variable "
                                f"or declare it in a leading var block (var {name} : Integer;)")
    return problems


@dataclass
class BuiltScript:
    text: str
    split: SplitScript
    body_first_line: int          # 1-based line in `text` of the script body's first line
    vars_first_line: int          # 1-based line in `text` of the first user declaration


def build_script(template: str, script: str, log_path: str, out_path: str,
                 guard_seconds: int = 60) -> BuiltScript:
    """Fill the template: user declarations, script body with its loops
    guarded, the log/result paths and the loop guard's deadline."""
    split = split_var_block(script)
    decls = split.decls
    body = guard_loops("\n".join(split.body), split.body_offset + 1).split("\n")
    if template.count(BODY_BEGIN) != 1 or template.count(BODY_END) != 1:
        raise ValueError("template must contain the experiment markers exactly once")
    if template.count(VARS_BEGIN) != 1 or template.count(VARS_END) != 1:
        raise ValueError("template must contain the user-vars markers exactly once")
    text = (template.replace(LOG_TOKEN, log_path).replace(OUT_TOKEN, out_path)
            .replace(GUARD_TOKEN, str(int(guard_seconds))))

    def fill(src, begin, end, lines, indent):
        pre, rest = src.split(begin, 1)
        marker, rest = rest.split("\n", 1)
        _, post = rest.split(end, 1)
        post_indent = re.search(r"([ \t]*)$", pre).group(1)
        block = "".join(indent + ln + "\n" if ln.strip() else "\n" for ln in lines)
        first = pre.count("\n") + 2          # line after the begin marker
        return pre + begin + marker + "\n" + block + post_indent + end + post, first

    text, vars_first = fill(text, VARS_BEGIN, VARS_END, decls, VARS_INDENT)
    text, body_first = fill(text, BODY_BEGIN, BODY_END, body, BODY_INDENT)
    return BuiltScript(text, split, body_first, vars_first)


def map_error_line(built: BuiltScript, message: str) -> Optional[dict]:
    """Translate 'at Line:N, Char:C' in an Altium error message to the submitted script.

    The returned line counts from the first line of the submitted script
    (including its var block), so it matches what the caller wrote. Char is
    the column in that line where the declaration or statement starts at
    column 1 after the tool's indentation is removed.
    """
    m = _LINE_RE.search(message)
    if not m:
        return None
    line, char = int(m.group(1)), int(m.group(2))
    sp = built.split
    k = line - built.vars_first_line
    if sp.decls and 0 <= k < len(sp.decls):
        return {"where": "var block", "line": sp.decl_src[k], "char": max(1, char - len(VARS_INDENT))}
    k = line - built.body_first_line
    if 0 <= k < len(sp.body):
        return {"where": "script", "line": k + 1 + sp.body_offset, "char": max(1, char - len(BODY_INDENT))}
    return {"where": "sandbox template", "line": line, "char": char}


# --------------------------------------------------------------------------- Windows side

WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
IDCANCEL = 2


def _process_name(hwnd) -> str:
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_text(hwnd) -> str:
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


@dataclass
class Dialog:
    hwnd: int
    cls: str
    title: str
    text: str = ""

    @property
    def is_script_error(self) -> bool:
        return self.cls == "#32770" and "continue execution" in self.text.lower()

    @property
    def is_error_box(self) -> bool:
        """A script-error dialog, or an OK-only Error/Information box such as the
        one a refused run raises. Confirm and Warning boxes may be the user's."""
        return self.is_script_error or (self.cls == "TMessageForm" and self.title in ("Error", "Information"))


def altium_dialogs() -> List[Dialog]:
    """Visible dialog windows owned by X2.EXE that can block a script run."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[Dialog] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def top(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value not in ("#32770", "TMessageForm", "TForm_RunScriptParameters"):
            return True
        title = _window_text(hwnd)
        if cls.value == "TMessageForm" and title not in ("Error", "Warning", "Information", "Confirm"):
            return True
        if _process_name(hwnd).lower() != "x2.exe":
            return True
        parts = []

        def child(h, __):
            c = ctypes.create_unicode_buffer(32)
            user32.GetClassNameW(h, c, 32)
            if c.value == "Static":
                t = _window_text(h).strip()
                if t:
                    parts.append(t)
            return True

        user32.EnumChildWindows(hwnd, proto(child), 0)
        found.append(Dialog(int(hwnd), cls.value, title, " ".join(" ".join(parts).split())))
        return True

    user32.EnumWindows(proto(top), 0)
    return found


def altium_hung() -> bool:
    """True while Altium's UI thread is not answering window messages.

    That is the case while a script (or a long command) is still running, and
    for good when a script loops forever. Nothing sent to Altium then has any
    effect: script launches queue up, and dialogs cannot be answered.
    """
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hung = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def top(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _process_name(hwnd).lower() == "x2.exe":
            if user32.IsHungAppWindow(hwnd):
                hung.append(hwnd)
                return False
        return True

    user32.EnumWindows(proto(top), 0)
    return bool(hung)


def answer_dialog(d: Dialog) -> None:
    """Cancel a script-error dialog; close any other dialog."""
    user32 = ctypes.windll.user32
    if d.is_script_error:
        user32.PostMessageW(d.hwnd, WM_COMMAND, IDCANCEL, 0)
    else:
        user32.PostMessageW(d.hwnd, WM_CLOSE, 0, 0)


# One counter per server process: a Sandbox is created for every call, so a
# per-instance counter would restart at 1 and two calls within the same
# second would get the same file names
_RUN_COUNTER = itertools.count(1)


def read_when_ready(path: Path, reader, timeout: float = 5.0) -> str:
    """Read a file Altium may still have open for writing.

    The result file exists as soon as SaveToFile creates it, and while Altium
    still holds it the read fails with PermissionError, so retry briefly.
    """
    end = time.time() + timeout
    while True:
        try:
            return reader(path)
        except PermissionError:
            if time.time() > end:
                raise
            time.sleep(0.2)


@dataclass
class RunFiles:
    script: Path
    log: Path
    result: Path


@dataclass
class Sandbox:
    """One Altium instance's sandbox: where run files go and how Altium is started."""
    altium_exe: str
    template: Path
    runs_dir: Path
    keep_runs: int = 20

    def new_run_files(self, tag: str = "Sbx") -> RunFiles:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{next(_RUN_COUNTER)}"
        return RunFiles(self.runs_dir / f"{stem}.pas", self.runs_dir / f"{stem}_log.txt",
                        self.runs_dir / f"{stem}_result.txt")

    def write(self, files: RunFiles, script: str, guard_seconds: int = 60) -> BuiltScript:
        built = build_script(self.template.read_text(encoding="utf-8"), script, str(files.log),
                             str(files.result), guard_seconds)
        files.script.write_text(built.text, encoding="utf-8")
        return built

    def launch(self, files: RunFiles) -> None:
        cmd = (f'"{self.altium_exe}" -RScriptingSystem:RunScriptFile('
               f'FileName={files.script}^|ProcName=Run)')
        subprocess.Popen(cmd, shell=True)

    def stop_debugger(self) -> None:
        subprocess.Popen(f'"{self.altium_exe}" -REditScript:Stop', shell=True)

    def probe(self, timeout: float = 15.0) -> bool:
        """Run a trivial script and report whether it finished."""
        files = self.new_run_files("Probe")
        self.write(files, "ResultText := 'probe';")
        self.launch(files)
        end = time.time() + timeout
        while time.time() < end:
            if files.result.exists():
                return True
            for d in altium_dialogs():
                if d.is_error_box:
                    answer_dialog(d)
            time.sleep(0.5)
        return False

    def recover(self, attempts: int = 3) -> dict:
        """Answer Altium's error boxes, stop the debugger and confirm with a probe run.

        Not only "Continue execution?" is answered: after a syntax error Altium
        follows that dialog with an Error box, and a refused run leaves an
        Information box; any of them blocks all later script runs. Confirm and
        Warning boxes are left alone - they may be the user's.
        """
        for n in range(1, attempts + 1):
            for _ in range(3):
                found = [d for d in altium_dialogs() if d.is_error_box]
                for d in found:
                    answer_dialog(d)
                if not found:
                    break
                time.sleep(0.7)
            self.stop_debugger()
            time.sleep(2.0)
            if self.probe():
                return {"recovered": True, "attempts": n}
        return {"recovered": False, "attempts": attempts}

    def prune(self) -> None:
        """Keep only the newest run files."""
        try:
            files = sorted(self.runs_dir.glob("*.pas"), key=lambda p: p.stat().st_mtime)
            for old in files[:-self.keep_runs]:
                for p in (old, old.with_name(old.stem + "_log.txt"), old.with_name(old.stem + "_result.txt")):
                    try:
                        p.unlink()
                    except OSError:
                        pass
        except OSError:
            pass
