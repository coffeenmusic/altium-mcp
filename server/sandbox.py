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

Functions and procedures
------------------------
DelphiScript has no nested routines, and a script's statements run inside the
template. So a script is split into its var block, the functions and
procedures that follow it, and its statements. The var block becomes global,
the routines go after the template's helpers, and the statements become the
template's SbxBody, which Run calls: an Exit in the script then ends SbxBody,
and Run still writes the result (an Exit straight in Run skipped that, and
the tool waited until its timeout).

Snapshots
---------
Before a run the tool copies the Altium documents the script names in its
strings (or the paths it is given) into a per-user folder; a rollback puts
them back. The template logs whether each one had unsaved changes in Altium
before and after the script ran, and the tool records the hash of each file
once the run has ended. A rollback refuses a document that had unsaved
changes before the run (they were never on disk), one changed on disk since
the run (saved later by the user or by another run), and, unless told to, one
that has unsaved changes now that it did not have when the run ended.
"""

import ctypes
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

BODY_BEGIN = "// === BEGIN EXPERIMENT"
BODY_END = "// === END EXPERIMENT"
VARS_BEGIN = "// === USER VARS"
VARS_END = "// === END USER VARS"
ROUTINES_BEGIN = "// === USER ROUTINES"
ROUTINES_END = "// === END USER ROUTINES"
DOCS_BEGIN = "// === SNAPSHOT DOCS"
DOCS_END = "// === END SNAPSHOT DOCS"
DOC_STATE_PREFIX = "DOC STATE: "    # logged by SbxNoteDoc
DOC_AFTER_PREFIX = "DOC AFTER: "    # logged by SbxNoteDocsAfter
EXCEPTION_PREFIX = "EXCEPTION: "   # logged by the template's except block
GUARD_PREFIX = "LOOP GUARD: "       # logged by SandboxTick when it ends the loops
UNDO_PREFIX = "UNDO GROUP LEFT OPEN: "  # logged by SbxCloseUndo
LOG_TOKEN = "%LOG_PATH%"
OUT_TOKEN = "%OUT_PATH%"
GUARD_TOKEN = "%GUARD_SECONDS%"
UNDO_TOKEN = "%UNDO_PATH%"
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

# Every other global name of the template: a script's var block is global
# too, so declaring one of these again is a "Duplicate identifier". Kept in
# sync with SandboxScript/Sandbox.pas by test_template_globals_listed.
TEMPLATE_GLOBALS = [
    "REPLACEALL", "SbxGuardUntil", "SbxGuardTripped", "SbxUndoDocs", "SbxUndoNames", "SbxUndoPath",
    "SandboxLog", "SandboxTick", "SbxSaveUndo", "SbxPreProcess", "SbxPostProcess", "SbxPcbPreProcess",
    "SbxPcbPostProcess", "SbxCloseUndo", "MM", "OpenSchDoc", "SaveDoc", "FmtNum", "FmtMM", "ParseNum",
    "ParseMM", "SbxSnapDocs", "SbxDocState", "SbxNoteDoc", "SbxNoteDocsAfter", "SbxCloseDoc",
    "SbxProjectUnsaved", "SbxNormPath", "SbxFindDoc", "SbxBody", "Run",
]

# Pascal's reserved words, and the directives and type names that failed the
# same way when used as a name, with messages such as "Error in declaration
# block" or "Label name expected" (checked in AD25 for Label, Object, Unit,
# File, Out and Property; the directives broke the production units).
RESERVED_WORDS = {
    "and", "array", "as", "asm", "begin", "case", "class", "const", "constructor", "destructor", "div",
    "do", "downto", "else", "end", "except", "exports", "file", "finalization", "finally", "for",
    "function", "goto", "if", "implementation", "in", "inherited", "initialization", "inline",
    "interface", "is", "label", "library", "mod", "nil", "not", "object", "of", "or", "out", "packed",
    "procedure", "program", "property", "raise", "record", "repeat", "resourcestring", "set", "shl",
    "shr", "string", "then", "threadvar", "to", "try", "type", "unit", "until", "uses", "var", "while",
    "with", "xor",
    # directives and type names
    "absolute", "at", "default", "index", "message", "on", "read", "stored", "variant", "write",
}

# A type: a name, or an array of one (`array[0..9] of Integer`,
# `array of String`, `array[1..N, 0..3] of Double`)
_BOUND = r"\s*-?\w+\s*\.\.\s*-?\w+\s*"
_TYPE = (r"((?i:array)(\s*\[" + _BOUND + r"(," + _BOUND + r")*\]\s*|\s+)(?i:of)\s+)*"
         r"[A-Za-z_][\w.]*")
_ONE_DECL = r"[A-Za-z_]\w*(\s*,\s*[A-Za-z_]\w*)*\s*:\s*" + _TYPE + r"\s*;"
# A declaration line: one or more `Name1, Name2 : Type;` (several may share
# a line, as in `var D: IServerDocument; P, S: String;`)
_DECL_RE = re.compile(r"^\s*(" + _ONE_DECL + r"\s*)+(//.*)?$")
# A line holding only a comment: // ..., { ... } or (* ... *)
_COMMENT_LINE_RE = re.compile(r"^\s*(//.*|\{[^}]*\}|\(\*.*?\*\))\s*$")
_ASSIGN_RE = re.compile(r"(?<![\w.\]\)])([A-Za-z_]\w*)\s*:=")
_LINE_RE = re.compile(r"at Line:\s*(\d+),\s*Char:\s*(\d+)", re.IGNORECASE)


@dataclass
class SplitScript:
    decls: List[str]              # declaration lines of the leading var block
    decl_src: List[int]           # 1-based line of each declaration in the submitted script
    body: List[str]
    body_offset: int              # submitted-script line number = body index (1-based) + offset
    routines: List[str] = field(default_factory=list)   # function/procedure lines after the var block
    routines_offset: int = 0      # submitted-script line number = routines index (1-based) + offset


_ROUTINE_RE = re.compile(r"^\s*(function|procedure)\b", re.IGNORECASE)
_BLOCK_RE = re.compile(r"\b(begin|case|try|asm|record|end)\b", re.IGNORECASE)


def _split_routines(lines: List[str], start: int) -> int:
    """Index of the first line after the functions and procedures that start
    at lines[start] (blank lines first allowed); `start` when there are none.

    A routine ends at the `end` that closes its first `begin`; `case`, `try`
    and `record` blocks inside it are counted along. A routine that never
    ends takes the rest of the script, and the compiler reports it.
    """
    blank = _blank_strings_and_comments("\n".join(lines)).split("\n")
    i = start
    end = start
    while True:
        while i < len(blank) and not blank[i].strip():
            i += 1
        if i == len(blank) or not _ROUTINE_RE.match(blank[i]):
            return end
        depth, opened, j = 0, False, i
        while j < len(blank):
            closed = False
            for m in _BLOCK_RE.finditer(blank[j]):
                if m.group(1).lower() == "end":
                    depth -= 1
                    if opened and depth == 0:
                        closed = True
                        break
                else:
                    depth += 1
                    opened = opened or m.group(1).lower() == "begin"
            j += 1
            if closed:
                break
        i = end = j


def split_var_block(script: str) -> SplitScript:
    """Split an optional leading `var` block, and the functions and procedures
    after it, off a script.

    The block is the word `var` on a line of its own (or followed by the first
    declaration), then declaration lines of the form `Name1, Name2 : Type;`.
    It ends at the first line that is not a declaration. Blank lines and
    lines holding only a comment may come before `var` and between the
    declarations. Routines follow it (or start the script) and end where
    their outer begin..end closes.
    """
    lines = script.split("\n")

    def skippable(line):
        return not line.strip() or _COMMENT_LINE_RE.match(line)

    i = 0
    while i < len(lines) and skippable(lines[i]):
        i += 1
    if i == len(lines) or not re.match(r"^\s*var\b", lines[i], re.IGNORECASE):
        end = _split_routines(lines, 0)
        return SplitScript([], [], lines[end:], end, lines[:end], 0)
    decls, src = [], []

    def add(line: str, number: int) -> None:
        # one declaration per entry, also when a line holds several
        for m in re.finditer(_ONE_DECL, line):
            decls.append(m.group(0).strip())
            src.append(number)

    first = re.sub(r"^\s*var\b", "", lines[i], flags=re.IGNORECASE).strip()
    if first:
        add(first, i + 1)
    i += 1
    while i < len(lines) and (_DECL_RE.match(lines[i]) or skippable(lines[i])):
        if not skippable(lines[i]):
            add(lines[i].strip(), i + 1)
        i += 1
    end = _split_routines(lines, i)
    return SplitScript(decls, src, lines[end:], end, lines[i:end], i)


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


_UNDO_RE = re.compile(r"(?<![\w.])(SchServer\s*\.\s*ProcessControl|PCBServer)\s*\.\s*(Pre|Post)Process\b",
                      re.IGNORECASE)


def track_undo(code: str) -> str:
    """Route the script's PreProcess/PostProcess calls through the template's
    SbxPreProcess/SbxPostProcess (SbxPcb... for PCBServer), which remember
    the undo groups left open so the template can close them at the end.

        SchServer.ProcessControl.PreProcess(Doc, '')  ->  SbxPreProcess(Doc, '')
        PCBServer.PostProcess                         ->  SbxPcbPostProcess

    Strings and comments are left alone, and nothing moves to another line.
    """
    blank = _blank_strings_and_comments(code)
    edits = []
    for m in _UNDO_RE.finditer(blank):
        pcb = m.group(1).lower() == "pcbserver"
        edits.append((m.start(), m.end(), f"Sbx{'Pcb' if pcb else ''}{m.group(2).capitalize()}Process"))
    for start, end, new in reversed(edits):
        code = code[:start] + new + code[end:]
    return code


_PARAM_RE = re.compile(r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:(?!=)")
_IDISPATCH_PARAM_RE = re.compile(r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*IDispatch\b", re.IGNORECASE)
_IDISPATCH_RESULT_RE = re.compile(r"(?:\)|^\s*)\s*:\s*IDispatch\s*;", re.IGNORECASE)


@dataclass
class Routine:
    name: str
    lines: List[str]
    first_line: int               # 1-based line of its header in the submitted script
    names: List[str]              # its parameters and local variables
    untyped: List[str] = field(default_factory=list)  # those declared IDispatch, and Result if it is


def routines(split: SplitScript) -> List[Routine]:
    """The script's functions and procedures with their own names."""
    found = []
    blank = _blank_strings_and_comments("\n".join(split.routines)).split("\n")
    starts = [k for k, ln in enumerate(blank) if _ROUTINE_RE.match(ln)]
    for n, k in enumerate(starts):
        stop = starts[n + 1] if n + 1 < len(starts) else len(blank)
        text = "\n".join(blank[k:stop])
        m = re.match(r"\s*(?:function|procedure)\s+([A-Za-z_]\w*)", text, re.IGNORECASE)
        head = text[m.end() if m else 0:]
        b = re.search(r"\bbegin\b", head, re.IGNORECASE)
        head = head[:b.start()] if b else head
        names = [x.strip() for g in _PARAM_RE.findall(head) for x in g.split(",")]
        untyped = [x.strip() for g in _IDISPATCH_PARAM_RE.findall(head) for x in g.split(",")]
        if _IDISPATCH_RESULT_RE.search(head):
            untyped.append("Result")
        found.append(Routine(m.group(1) if m else "", split.routines[k:stop],
                             split.routines_offset + k + 1, names, untyped))
    return found


def check_declarations(split: SplitScript, reserved_words: bool = True) -> List[str]:
    """Names the script declares that cannot compile: reserved words, and
    var-block names the template already declares. reserved_words=False
    (skip_lint) leaves out the reserved-word check, for a word it has wrong."""
    problems = []
    taken = {n.lower(): n for n in TEMPLATE_NAMES + TEMPLATE_GLOBALS}
    reserved = RESERVED_WORDS if reserved_words else set()
    for decl, src in zip(split.decls, split.decl_src):
        for name in declared_names([decl]):
            if name.lower() in reserved:
                problems.append(f"line {src}: '{name}' is a reserved word and cannot name a variable; "
                                f"rename it (e.g. {name}_)")
            elif name.lower() in taken:
                problems.append(f"line {src}: '{name}' is already declared by the sandbox; use it without "
                                f"declaring it, or pick another name")
    for r in routines(split):
        for name in [r.name] + r.names:
            if name.lower() in reserved:
                problems.append(f"line {r.first_line}: '{name}' is a reserved word and cannot be used as "
                                f"a name in {r.name or 'a routine'}; rename it")
        if r.name.lower() in taken:
            problems.append(f"line {r.first_line}: '{r.name}' is already a sandbox name; rename the routine")
    return problems


def check_routines(split: SplitScript) -> List[str]:
    """check_script for each routine, with its parameters, local variables,
    its own name and Result as known names."""
    problems = []
    script_names = declared_names(split.decls) + [r.name for r in routines(split)]
    for r in routines(split):
        problems += check_script(r.lines, script_names + r.names + ["Result"], r.first_line - 1)
    return problems


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


# Calls that crashed Altium (AD26) in real sessions. The template's except block
# catches most run-time errors, but a script that fails between
# SchServer.ProcessControl.PreProcess and PostProcess leaves Altium's undo
# history broken until Altium is restarted, and some of these calls take Altium
# down without raising anything the script could catch.
KNOWN_CRASHES = [
    (r"\bGetState_DescriptionString\b", "crashes when called on objects from a schematic iterator"),
    (r"\bDM_PortObjects\b", "crashes"),
    (r"\bDM_PartDesignator\b", "does not exist; use DM_LogicalPartDesignator"),
    (r"\.Tag\b", "schematic objects have no Tag property"),
    (r"\bObj\d\s*:=\s*TStringList\.Create\b",
     "a TStringList held in an IDispatch variable (Obj1..Obj9) crashes when indexed; "
     "use List1..List3 or declare a TStringList in a var block"),
    (r"\bList[123]\s*\.\s*Free\b|\bList[123]\s*:=\s*nil\b",
     "List1..List3 are created and freed by the sandbox; empty them with .Clear"),
]


_PREC_TOKEN_RE = re.compile(
    r":=|<>|<=|>=|[=<>()\[\];,]|\b(?:and|or|xor|then|do|of|else|begin|end|until|if|while|repeat|case)\b",
    re.IGNORECASE)
_NOT_CMP_RE = re.compile(r"\bnot\s+(?!\()[A-Za-z_][\w.]*(?:\([^()]*\)|\[[^\]]*\])*\s*(?:<>|<=|>=|=|<|>)",
                         re.IGNORECASE)


def precedence_problems(code: str, line_offset: int = 0) -> List[str]:
    """Comparisons that Pascal's precedence splits in the wrong place.

    `and`, `or` and `not` bind tighter than `=`, `<`, ...: `A = 1 and B = 2`
    is `A = (1 and B) = 2`, which AD25 rejects with "Then expected", and
    `not A = B` is `(not A) = B`, which runs and gives the wrong answer.
    `code` has its strings and comments stripped.
    """
    problems = []
    frames = [[False, None]]          # per parenthesis level: has comparison, line of and/or
    line = 1

    def check(frame):
        if frame[0] and frame[1] is not None:
            problems.append(f"line {frame[1] + line_offset}: a comparison next to and/or without "
                            f"parentheses: 'A = 1 and B = 2' reads as 'A = (1 and B) = 2'. Write "
                            f"'(A = 1) and (B = 2)'")

    pos = 0
    for m in _PREC_TOKEN_RE.finditer(code):
        line += code.count("\n", pos, m.start())
        pos = m.start()
        tok = m.group(0).lower()
        if tok in "([":
            frames.append([False, None])
        elif tok in ")]":
            if len(frames) > 1:
                check(frames.pop())
        elif tok in ("<>", "<=", ">=", "=", "<", ">"):
            frames[-1][0] = True
        elif tok in ("and", "or", "xor"):
            if frames[-1][1] is None:
                frames[-1][1] = line
        elif tok == ",":              # ends an argument: 'Max(A = 1, B or C)' is fine
            check(frames[-1])
            frames[-1] = [False, None]
        else:                         # a statement or clause boundary
            for f in frames:
                check(f)
            frames = [[False, None]]
    for f in frames:
        check(f)
    for lineno, text in enumerate(code.splitlines(), 1):
        m = _NOT_CMP_RE.search(text)
        if m:
            problems.append(f"line {lineno + line_offset}: '{' '.join(m.group(0).split())} ...' reads as "
                            f"'(not ...) = ...', which runs and gives the wrong answer. Write "
                            f"'not (A = B)' or 'A <> B'")
    return problems


def _record_read_re(untyped: List[str]):
    return re.compile(r"\b(" + "|".join(map(re.escape, untyped)) + r")\s*\.\s*"
                      r"(Corner|BoundingRectangle\w*)\b(?!\s*:=)", re.IGNORECASE)


def lint_script(body_lines: List[str], line_offset: int = 0, decls: List[str] = (),
                routine_list: List[Routine] = ()) -> List[str]:
    """Calls known to crash Altium, and document edits that are never closed.

    decls are the script's own declarations: a variable declared IDispatch is
    checked as Obj1..Obj9 are. So are the IDispatch parameters and locals of
    each routine in routine_list, on that routine's lines. Strings and
    comments are ignored. Returns a list of problems; empty means the script
    may run.
    """
    code = strip_strings_and_comments("\n".join(body_lines))
    untyped = ["Obj%d" % i for i in range(1, 10)] + [
        name for d in decls if re.search(r":\s*IDispatch\s*;", d, re.IGNORECASE)
        for name in declared_names([d])]
    # A record property (TLocation, TCoordRect) read through an IDispatch
    # variable crashed Altium; through a typed variable (ISch_GraphicalObject,
    # IPCB_Component, ...) it reads fine, and the production units do so
    record_read = _record_read_re(untyped)
    # Submitted-script line -> the record-read check with that routine's own names
    scoped = {}
    for r in routine_list:
        if r.untyped:
            check = _record_read_re(untyped + r.untyped)
            for n in range(r.first_line, r.first_line + len(r.lines)):
                scoped[n] = check
    problems = []
    for lineno, line in enumerate(code.splitlines(), 1):
        for pat, why in KNOWN_CRASHES:
            if re.search(pat, line, re.IGNORECASE):
                problems.append(f"line {lineno + line_offset}: {why}")
        m = scoped.get(lineno + line_offset, record_read).search(line)
        if m:
            problems.append(f"line {lineno + line_offset}: reading .{m.group(2)} through an IDispatch "
                            f"variable ({m.group(1)}) crashes; declare it with its interface type, "
                            f"e.g. ISch_GraphicalObject or IPCB_Component. Writing "
                            f"'.Corner := Point(..)' works")
    problems += precedence_problems(code, line_offset)
    # PCBServer.PreProcess is usually written without parentheses
    pre = len(re.findall(r"\.PreProcess\b", code, re.IGNORECASE))
    post = len(re.findall(r"\.PostProcess\b", code, re.IGNORECASE))
    if pre != post:
        problems.append(f"{pre} PreProcess but {post} PostProcess call(s): an edit that is never "
                        "closed breaks Altium's undo history")
    return problems


@dataclass
class BuiltScript:
    text: str
    split: SplitScript
    body_first_line: int          # 1-based line in `text` of the script body's first line
    vars_first_line: int          # 1-based line in `text` of the first user declaration
    routines_first_line: int = 0  # 1-based line in `text` of the first routine line


def _pascal_string(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def build_script(template: str, script: str, log_path: str, out_path: str,
                 guard_seconds: int = 60, undo_path: str = "",
                 snapshot_docs: List[str] = ()) -> BuiltScript:
    """Fill the template: user declarations, routines and script body with
    their loops guarded and their undo groups tracked, the log/result/undo
    paths, the loop guard's deadline, and the snapshotted documents whose
    state is logged before the script runs."""
    split = split_var_block(script)
    decls = split.decls
    body = track_undo(guard_loops("\n".join(split.body), split.body_offset + 1)).split("\n")
    routine_lines = (track_undo(guard_loops("\n".join(split.routines), split.routines_offset + 1)).split("\n")
                     if split.routines else [])
    for begin, end, what in ((BODY_BEGIN, BODY_END, "experiment"), (VARS_BEGIN, VARS_END, "user-vars"),
                             (ROUTINES_BEGIN, ROUTINES_END, "user-routines"),
                             (DOCS_BEGIN, DOCS_END, "snapshot-docs")):
        if template.count(begin) != 1 or template.count(end) != 1:
            raise ValueError(f"template must contain the {what} markers exactly once")
    text = (template.replace(LOG_TOKEN, log_path).replace(OUT_TOKEN, out_path)
            .replace(GUARD_TOKEN, str(int(guard_seconds))).replace(UNDO_TOKEN, undo_path))

    def fill(src, begin, end, lines, indent):
        pre, rest = src.split(begin, 1)
        marker, rest = rest.split("\n", 1)
        _, post = rest.split(end, 1)
        post_indent = re.search(r"([ \t]*)$", pre).group(1)
        block = "".join(indent + ln + "\n" if ln.strip() else "\n" for ln in lines)
        first = pre.count("\n") + 2          # line after the begin marker
        return pre + begin + marker + "\n" + block + post_indent + end + post, first

    text, vars_first = fill(text, VARS_BEGIN, VARS_END, decls, VARS_INDENT)
    # Routines keep their own indentation, so columns map back unchanged
    text, routines_first = fill(text, ROUTINES_BEGIN, ROUTINES_END, routine_lines, "")
    text, _ = fill(text, DOCS_BEGIN, DOCS_END, [f"SbxNoteDoc({_pascal_string(p)});" for p in snapshot_docs],
                   VARS_INDENT)
    text, body_first = fill(text, BODY_BEGIN, BODY_END, body, BODY_INDENT)
    return BuiltScript(text, split, body_first, vars_first, routines_first)


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
    k = line - built.routines_first_line
    if sp.routines and 0 <= k < len(sp.routines):
        return {"where": "script", "line": k + 1 + sp.routines_offset, "char": char}
    k = line - built.body_first_line
    if 0 <= k < len(sp.body):
        return {"where": "script", "line": k + 1 + sp.body_offset, "char": max(1, char - len(BODY_INDENT))}
    return {"where": "sandbox template", "line": line, "char": char}


# --------------------------------------------------------------------------- snapshots

# Documents a script may change, and the kind Client.OpenDocument takes for
# each when a rollback opens it again (a project is opened as a project)
DOC_KINDS = {".schdoc": "SCH", ".pcbdoc": "PCB", ".schlib": "SCHLIB", ".pcblib": "PCBLIB",
             ".outjob": "OUTPUTJOB", ".prjpcb": "PROJECT"}
_DOC_PATH_RE = re.compile(r"'((?:[^']|'')+\.(?:" + "|".join(e[1:] for e in DOC_KINDS) + r"))'", re.IGNORECASE)


def doc_paths(script: str) -> List[str]:
    """Absolute paths of existing Altium documents named in the script's
    string literals, in order, without repeats."""
    found, seen = [], set()
    for m in _DOC_PATH_RE.finditer(script):
        path = m.group(1).replace("''", "'")
        if os.path.isabs(path) and os.path.isfile(path) and path.lower() not in seen:
            seen.add(path.lower())
            found.append(path)
    return found


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_paths(paths: List[str], normalise=lambda p: p) -> List[str]:
    """`paths` through `normalise`, in order, without repeats (ignoring case)."""
    found, seen = [], set()
    for p in paths:
        p = normalise(p)
        if p.lower() not in seen:
            seen.add(p.lower())
            found.append(p)
    return found


def nothing_snapshotted_note(paths_given: bool) -> str:
    """What a run's result says when it copied no document."""
    why = ("snapshot=[] was passed." if paths_given else
           "the script names no document by its full path (a script working on GetCurrentPCBBoard "
           "or the focused document names none). Pass snapshot=[paths] to copy documents before "
           "a run.")
    return "Nothing was snapshotted, so rollback_altium_script cannot undo this run: " + why


def take_snapshot(directory: Path, paths: List[str]) -> List[dict]:
    """Copy `paths` into `directory` and write its manifest; returns the entries."""
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for n, path in enumerate(paths):
        copy = directory / f"{n}_{os.path.basename(path)}"
        shutil.copy2(path, copy)
        entries.append({"path": path, "copy": copy.name, "bytes": copy.stat().st_size,
                        "sha256": file_sha256(copy)})
    write_manifest(directory, entries)
    return entries


def read_manifest(directory: Path) -> Optional[List[dict]]:
    try:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_manifest(directory: Path, entries: List[dict]) -> None:
    (directory / "manifest.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")


def record_run_end(directory: Path, log_lines: List[str]) -> List[dict]:
    """Add to the manifest what the run left: each document's state in Altium
    before and after the script (None when not logged), and the hash of the
    file on disk now. A rollback refuses a file whose hash has changed since:
    it was saved later, by the user or by another run."""
    entries = read_manifest(directory) or []
    before = doc_states(log_lines)
    after = doc_states(log_lines, DOC_AFTER_PREFIX)
    for e in entries:
        e["state_before"] = before.get(e["path"].lower())
        e["state_after"] = after.get(e["path"].lower())
        try:
            e["sha256_after"] = file_sha256(e["path"])
        except OSError:
            e["sha256_after"] = None      # the run deleted or locked it
    write_manifest(directory, entries)
    return entries


def changed_since_run(entries: List[dict]) -> List[str]:
    """Snapshotted files whose content on disk differs from when the run ended
    (or whose end state was never recorded)."""
    changed = []
    for e in entries:
        if "sha256_after" not in e:
            changed.append(e["path"])
            continue
        try:
            now = file_sha256(e["path"])
        except OSError:
            now = None
        if now != e["sha256_after"]:
            changed.append(e["path"])
    return changed


def doc_states(log_lines: List[str], prefix: str = None) -> dict:
    """Document path (lower case) -> 'closed', 'open' or 'unsaved', from the
    log lines SbxNoteDoc (before the script) or SbxNoteDocsAfter
    (prefix=DOC_AFTER_PREFIX) writes."""
    prefix = prefix or DOC_STATE_PREFIX
    states = {}
    for ln in log_lines:
        if ln.startswith(prefix):
            state, _, path = ln[len(prefix):].partition("|")
            states[path.strip().lower()] = state.strip()
    return states


def _is_project(path: str) -> bool:
    return DOC_KINDS.get(os.path.splitext(path)[1].lower()) == "PROJECT"


def norm_path(path: str) -> str:
    """As the template's SbxNormPath: lower case, backslashes, no repeated
    separators (a UNC path keeps its leading two)."""
    p = path.strip().replace("/", "\\").lower()
    lead = "\\\\" if p.startswith("\\\\") else ""
    return lead + re.sub(r"\\{2,}", r"\\", p[len(lead):])


def _skip_list(paths: List[str]) -> str:
    return "".join(f"|{norm_path(p).upper()}|" for p in paths)


def state_script(paths: List[str]) -> str:
    """A sandbox script that reports each of `paths` as 'state|path' (closed,
    open or unsaved), and for a project each open document of it with unsaved
    changes that is not in `paths`, as 'child|project|document'."""
    lines = []
    for p in paths:
        q = _pascal_string(p)
        lines.append(f"S1 := S1 + SbxDocState({q}) + '|' + {q} + #13#10;")
        if _is_project(p):
            lines += [f"S2 := SbxProjectUnsaved({q}, {_pascal_string(_skip_list(paths))});",
                      f"S1 := S1 + 'children|' + {q} + '|' + S2 + #13#10;"]
    lines.append("ResultText := S1;")
    return "\n".join(lines)


def parse_states(text: str):
    """state_script's result as ({path lower: state}, {project lower: [documents]})."""
    states, children = {}, {}
    for ln in (text or "").splitlines():
        kind, _, rest = ln.partition("|")
        if kind == "children":
            proj, _, docs = rest.partition("|")
            children[proj.lower()] = [d for d in docs.split("*") if d]
        elif kind in ("closed", "open", "unsaved"):
            states[rest.lower()] = kind
    return states, children


def close_script(paths: List[str]) -> str:
    """A sandbox script that closes `paths` in Altium without saving and
    reports, one line each, 'closed|path' (was not open), 'open|path|name'
    (was open, now closed; name is the path as Altium had it, which may be
    spelled otherwise) or 'stillopen|path'. Documents close before projects:
    closing a project first would close its sheets with it, and a sheet with
    unsaved changes would make Altium ask to save it."""
    lines = []
    for p in sorted(paths, key=_is_project):
        q = _pascal_string(p)
        lines += ["Obj1 := SbxFindDoc(" + q + ");",
                  "S2 := '';",
                  "if Obj1 <> nil then S2 := Obj1.FileName;",
                  f"S1 := S1 + SbxCloseDoc({q}) + '|' + {q} + '|' + S2 + #13#10;"]
    lines.append("ResultText := S1;")
    return "\n".join(lines)


def reopen_script(paths: List[str]) -> str:
    """A sandbox script that opens `paths` in Altium again."""
    lines = []
    for p in paths:
        q = _pascal_string(p)
        kind = DOC_KINDS.get(os.path.splitext(p)[1].lower(), "")
        if kind == "PROJECT":
            lines.append(f"GetWorkspace.DM_OpenProject({q}, True);")
        else:
            lines += [f"Obj1 := Client.OpenDocument('{kind}', {q});",
                      "if Obj1 <> nil then Client.ShowDocument(Obj1);"]
    lines.append("ResultText := 'reopened';")
    return "\n".join(lines)


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


def snapshot_dialog(d: Dialog, directory: Path, keep: int = 50) -> Optional[str]:
    """Save a picture of a dialog before it is answered; return the file path.

    Altium's own message boxes (TMessageForm) draw their text as a label that
    is not a window, so the message cannot be read from outside Altium - the
    picture is the only record of it. PrintWindow lets the dialog paint itself
    into a bitmap, so windows covering it do not end up in the picture.
    """
    from ctypes import wintypes
    from PIL import Image
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    # Physical pixels, so the bitmap is not cropped on scaled displays
    old_ctx = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(d.hwnd, ctypes.byref(rect))
    finally:
        if old_ctx:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old_ctx))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 20 or h < 20:
        return None
    # A freshly opened dialog may not have painted yet
    # (RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW)
    user32.RedrawWindow(d.hwnd, None, None, 0x0001 | 0x0080 | 0x0100)
    time.sleep(0.15)
    hdc_win = user32.GetWindowDC(d.hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old = gdi32.SelectObject(hdc, bmp)
    try:
        if not (user32.PrintWindow(d.hwnd, hdc, 2) or user32.PrintWindow(d.hwnd, hdc, 0)):  # 2 = PW_RENDERFULLCONTENT
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h          # negative height: rows top-down
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        # A window that is not per-monitor DPI aware paints at its logical
        # size and leaves the rest of the bitmap black
        box = img.getbbox()
        if box:
            img = img.crop(box)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"dialog_{time.strftime('%Y%m%d_%H%M%S')}_{d.hwnd}.png"
        img.save(path)
    finally:
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(d.hwnd, hdc_win)
    try:
        pics = sorted(directory.glob("dialog_*.png"), key=lambda p: p.stat().st_mtime)
        for p in pics[:-keep]:
            p.unlink()
    except OSError:
        pass
    return str(path)


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

    @property
    def undo(self) -> Path:
        """Undo groups the script has open, one per line, kept current by the
        template while it runs."""
        return self.script.with_name(self.script.stem + "_undo.txt")

    @property
    def run_id(self) -> str:
        return self.script.stem


# Run file name prefix of a user's script; helper, probe and undo runs have their own
SCRIPT_TAG = "Sbx"


@dataclass
class Sandbox:
    """One Altium instance's sandbox: where run files go and how Altium is started.

    Run files go to runs_dir, which Altium must be able to read. Snapshots
    hold copies of the user's documents and only this server reads them, so
    they go to a per-user folder (snapshots_dir), not to the shared one.
    """
    altium_exe: str
    template: Path
    runs_dir: Path
    keep_runs: int = 20
    snapshots_dir: Optional[Path] = None
    dialogs_dir: Optional[Path] = None

    def snapshot_dir(self, run_id: str) -> Optional[Path]:
        """Copies of the documents taken before a run, with manifest.json."""
        if self.snapshots_dir is None or not re.fullmatch(r"[\w-]+", run_id or ""):
            return None
        return self.snapshots_dir / run_id

    def new_run_files(self, tag: str = SCRIPT_TAG) -> RunFiles:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{next(_RUN_COUNTER)}"
        return RunFiles(self.runs_dir / f"{stem}.pas", self.runs_dir / f"{stem}_log.txt",
                        self.runs_dir / f"{stem}_result.txt")

    def write(self, files: RunFiles, script: str, guard_seconds: int = 60,
              snapshot_docs: List[str] = ()) -> BuiltScript:
        built = build_script(self.template.read_text(encoding="utf-8"), script, str(files.log),
                             str(files.result), guard_seconds, str(files.undo), snapshot_docs)
        files.script.write_text(built.text, encoding="utf-8")
        return built

    def run_quick(self, script: str, timeout: float = 30.0,
                  closed_dialogs: Optional[list] = None) -> Optional[str]:
        """Run a small helper script; its ResultText, or None when it did not finish.

        With closed_dialogs (a list), any other Altium dialog that opens while
        the script runs is closed too and added to it, as {"title", "picture"}:
        a helper that closes documents must not hang on a Save prompt. Closing
        such a prompt is its Cancel, so the document stays open and nothing is
        saved or lost.
        """
        files = self.new_run_files("Helper")
        if not self._run_until_done(files, script, timeout, closed_dialogs):
            return None
        return read_when_ready(files.result, lambda p: p.read_text(encoding="utf-8", errors="replace"))

    def launch(self, files: RunFiles) -> None:
        cmd = (f'"{self.altium_exe}" -RScriptingSystem:RunScriptFile('
               f'FileName={files.script}^|ProcName=Run)')
        subprocess.Popen(cmd, shell=True)

    def stop_debugger(self) -> None:
        subprocess.Popen(f'"{self.altium_exe}" -REditScript:Stop', shell=True)

    def _run_until_done(self, files: RunFiles, script: str, timeout: float,
                        closed_dialogs: Optional[list] = None) -> bool:
        # Dialogs open before the launch are the user's; leave them alone
        before = {d.hwnd for d in altium_dialogs()} if closed_dialogs is not None else set()
        seen = set()
        self.write(files, script)
        self.launch(files)
        end = time.time() + timeout
        while time.time() < end:
            if files.result.exists():
                return True
            for d in altium_dialogs():
                if d.is_error_box:
                    answer_dialog(d)
                elif closed_dialogs is not None and d.hwnd not in before:
                    if d.hwnd not in seen:
                        seen.add(d.hwnd)
                        picture = None
                        if self.dialogs_dir is not None:
                            try:
                                picture = snapshot_dialog(d, self.dialogs_dir)
                            except Exception:
                                picture = None
                        closed_dialogs.append({"title": d.title, "picture": picture})
                    answer_dialog(d)
            time.sleep(0.5)
        return False

    def probe(self, timeout: float = 15.0) -> bool:
        """Run a trivial script and report whether it finished."""
        return self._run_until_done(self.new_run_files("Probe"), "ResultText := 'probe';", timeout)

    def close_leftover_undo(self, files: RunFiles, timeout: float = 15.0) -> List[str]:
        """Close the undo groups a script left open when it died before its
        end (stopped in the debugger), from the list it kept in files.undo.
        Returns what was closed."""
        try:
            open_groups = [ln.strip() for ln in files.undo.read_text(encoding="utf-8", errors="replace")
                           .splitlines() if ln.strip()]
        except OSError:
            return []
        if not open_groups:
            return []
        lines = []
        for entry in reversed(open_groups):
            kind, _, path = entry.partition("|")
            if kind == "PCB":
                lines.append("PCBServer.PostProcess;")
            else:
                quoted = path.replace("'", "''")
                lines += [f"Obj1 := SchServer.GetSchDocumentByPath('{quoted}');",
                          "if Obj1 <> nil then SchServer.ProcessControl.PostProcess(Obj1, '');"]
        lines.append("ResultText := 'closed';")
        if self._run_until_done(self.new_run_files("Undo"), "\n".join(lines), timeout):
            return open_groups
        return []

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
        """Keep only the newest run files, and the snapshots of the newest
        script runs. Helper, probe and undo runs are kept apart, so they never
        push a script run's snapshot out."""
        def newest_first(paths):
            return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)

        try:
            runs = list(self.runs_dir.glob("*.pas"))
            scripts = newest_first([p for p in runs if p.stem.split("_", 1)[0] == SCRIPT_TAG])
            others = newest_first([p for p in runs if p.stem.split("_", 1)[0] != SCRIPT_TAG])
            for old in scripts[self.keep_runs:] + others[self.keep_runs:]:
                for p in (old, old.with_name(old.stem + "_log.txt"), old.with_name(old.stem + "_result.txt"),
                          old.with_name(old.stem + "_undo.txt")):
                    try:
                        p.unlink()
                    except OSError:
                        pass
            # Snapshots used to be kept beside the run files, readable by every account
            for old in self.runs_dir.glob("*_snapshot"):
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
        if self.snapshots_dir is None:
            return
        try:
            snaps = newest_first([p for p in self.snapshots_dir.iterdir() if p.is_dir()])
            for old in snaps[self.keep_runs:]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
