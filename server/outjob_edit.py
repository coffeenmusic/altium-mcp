"""Creating and editing Altium .OutJob files.

An OutJob is INI text. Outputs are numbered n and containers ("output media")
numbered m inside [OutputGroup1]; OutputEnabled{n}_OutputMedium{m} is the
output's position in the container (0: not in it). Each container's folder and
file settings are the keys ending in m in [PublishSettings] and
[GeneratedFilesSettings]. Removing an output or container renumbers the ones
after it.

The file is edited line by line, so everything not changed stays exactly as
Altium wrote it (configparser would lowercase keys and reorder lines). An
output's configuration is board specific and long, so new outputs are copied
from another OutJob rather than written from nothing.
"""
import copy
import re
from pathlib import Path
from typing import List, Optional, Tuple

GROUP, PUBLISH, GENERATED = "OutputGroup1", "PublishSettings", "GeneratedFilesSettings"

# The keys of one output: OutputType3, OutputEnabled3_OutputMedium2, Configuration3_Item1, ...
_OUTPUT_KEY = re.compile(r"^(OutputType|OutputName|OutputCategory|OutputDocumentPath|OutputVariantName"
                         r"|OutputDefault|PageOptions|PcbPrintPreferences|OutputEnabled|Configuration)"
                         r"(\d+)(_.*)?$")
_MEDIUM_KEY = re.compile(r"^OutputMedium(\d+)(_.*)?$")
_ENABLED_IN = re.compile(r"^OutputEnabled(\d+)_OutputMedium(\d+)$")
_NUMBERED = re.compile(r"^(\D+)(\d+)$")

CONTAINER_TYPES = {"generated_files": "GeneratedFiles", "pdf": "Publish"}

# The keys saying where a container writes and what it calls its files. A new
# container copies the other settings of one of its type, but not these: two
# PDF containers with the same base path and file name write one file.
_LOCATION_KEYS = ("OutputFilePath", "OutputBasePath", "OutputPathMedia", "OutputPathMediaValue",
                  "OutputPathOutputer", "OutputPathOutputerPrefix", "OutputPathOutputerValue",
                  "OutputFileName", "OutputFileNameMulti", "OutputFileNameSpecial", "RelativeOutputPath")

# Settings of a new container when the file has none of its type to copy,
# as Altium writes them (from OutJobs saved by AD25)
_DEFAULTS = {
    "GeneratedFiles": (
        [("OutputFilePath", ""), ("ReleaseManaged", "0"), ("OutputBasePath", ".\\"), ("OutputPathMedia", ""),
         ("OutputPathMediaValue", ""), ("OutputPathOutputer", "[Output Custom]"), ("OutputPathOutputerPrefix", ""),
         ("OutputPathOutputerValue", ""), ("OutputFileName", ""), ("OutputFileNameMulti", ""),
         ("UseOutputNameForMulti", "1"), ("OutputFileNameSpecial", ""), ("OpenOutput", "1")],
        [("RelativeOutputPath", ""), ("OpenOutputs", "1"), ("AddToProject", "1"), ("TimestampFolder", "0"),
         ("UseOutputName", "0"), ("OpenODBOutput", "0"), ("OpenGerberOutput", "0"), ("OpenNCDrillOutput", "0"),
         ("OpenIPCOutput", "0"), ("EnableReload", "0")]),
    "Publish": (
        [("OutputFilePath", ""), ("ReleaseManaged", "0"), ("OutputBasePath", ".\\"), ("OutputPathMedia", ""),
         ("OutputPathMediaValue", ""), ("OutputPathOutputer", "[Output Custom]"), ("OutputPathOutputerPrefix", ""),
         ("OutputPathOutputerValue", ""), ("OutputFileName", ""), ("OutputFileNameMulti", ""),
         ("UseOutputNameForMulti", "0"), ("OutputFileNameSpecial", ""), ("OpenOutput", "1"),
         ("PromptOverwrite", "1"), ("PublishMethod", "0"), ("ZoomLevel", "50"), ("FitSCHPrintSizeToDoc", "1"),
         ("FitPCBPrintSizeToDoc", "1"), ("GenerateNetsInfo", "1"), ("MarkPins", "1"), ("MarkNetLabels", "1"),
         ("MarkPortsId", "1"), ("GenerateTOC", "1"), ("ShowComponentParameters", "1"), ("GlobalBookmarks", "0"),
         ("PDFACompliance", "Disabled"), ("PDFVersion", "Default")],
        [("RelativeOutputPath", ""), ("OpenOutputs", "1")]),
}

EMPTY_OUTJOB = """[OutputJobFile]
Version=1.0
Caption=
Description=
VaultGUID=
ItemGUID=
ItemHRID=
RevisionGUID=
RevisionId=
VaultHRID=
AutoItemHRID=
NextRevId=
FolderGUID=
LifeCycleDefinitionGUID=
RevisionNamingSchemeGUID=

[OutputGroup1]
Name={name}
Description=
TargetOutputMedium=Print Job
VariantName=[No Variations]
VariantScope=1
CurrentConfigurationName=
TargetPrinter=Microsoft Print to PDF
PrinterOptions=Record=PrinterOptions|Copies=1|Duplex=1|TrueTypeOptions=3|Collate=1|PrintJobKind=1|PrintWhat=0
OutputMedium1=Print Job
OutputMedium1_Type=Printer
OutputMedium1_Printer=
OutputMedium1_PrinterOptions=Record=PrinterOptions|Copies=1|Duplex=1|TrueTypeOptions=3|Collate=1|PrintJobKind=1|PrintWhat=1

[PublishSettings]

[GeneratedFilesSettings]
"""


class OutJobError(ValueError):
    pass


def decode(raw: bytes) -> Tuple[str, str]:
    """The text and the encoding to write it back in."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-8-sig" and not raw.startswith(b"\xef\xbb\xbf"):
            encoding = "utf-8"
        return text, encoding
    return raw.decode("latin-1"), "latin-1"


class OutJob:
    """An OutJob's lines, grouped by section, with numbered keys renumbered
    when an output or container is removed."""

    def __init__(self, text: str, encoding: str = "utf-8"):
        self.encoding = encoding
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.sections: List[Tuple[Optional[str], list]] = [(None, [])]
        for line in text.replace("\r\n", "\n").split("\n"):
            if line.startswith("[") and line.rstrip().endswith("]"):
                self.sections.append((line.strip()[1:-1], []))
            elif "=" in line and not line.startswith(";"):
                k, v = line.split("=", 1)
                self.sections[-1][1].append([k, v])
            else:
                self.sections[-1][1].append([None, line])
        if self.entries(GROUP) is None:
            raise OutJobError("not an OutJob: it has no [OutputGroup1] section")

    def text(self) -> str:
        lines = []
        for name, entries in self.sections:
            if name is not None:
                lines.append(f"[{name}]")
            lines.extend(v if k is None else f"{k}={v}" for k, v in entries)
        return self.newline.join(lines)

    # --- plain access ---------------------------------------------------------

    def entries(self, section: str) -> Optional[list]:
        return next((e for n, e in self.sections if n == section), None)

    def _section(self, section: str) -> list:
        e = self.entries(section)
        if e is None:
            if self.sections[-1][1] and self.sections[-1][1][-1] != [None, ""]:
                self.sections[-1][1].append([None, ""])
            self.sections.append((section, []))
            e = self.sections[-1][1]
        return e

    def get(self, section: str, key: str, default=None):
        return next((v for k, v in (self.entries(section) or []) if k == key), default)

    def set(self, section: str, key: str, value: str, after: Optional[str] = None) -> None:
        entries = self._section(section)
        for e in entries:
            if e[0] == key:
                e[1] = value
                return
        at = len(entries)
        # keep blank lines that end the section after the new key
        while at > 0 and entries[at - 1][0] is None and entries[at - 1][1] == "":
            at -= 1
        if after is not None:
            idx = [i for i, e in enumerate(entries) if e[0] is not None and re.match(after, e[0])]
            if idx:
                at = idx[-1] + 1
        entries.insert(at, [key, value])

    def delete_where(self, section: str, match) -> None:
        entries = self.entries(section)
        if entries is not None:
            entries[:] = [e for e in entries if e[0] is None or not match(e[0])]

    def rename_where(self, section: str, rename) -> None:
        for e in self.entries(section) or []:
            if e[0] is not None:
                e[0] = rename(e[0])

    # --- the model ------------------------------------------------------------

    def containers(self) -> List[dict]:
        out, m = [], 1
        while self.get(GROUP, f"OutputMedium{m}") is not None:
            out.append({"index": m, "name": self.get(GROUP, f"OutputMedium{m}"),
                        "type": self.get(GROUP, f"OutputMedium{m}_Type", "")})
            m += 1
        return out

    def outputs(self) -> List[dict]:
        out, n = [], 1
        while self.get(GROUP, f"OutputType{n}") is not None:
            out.append({"index": n, "type": self.get(GROUP, f"OutputType{n}"),
                        "name": self.get(GROUP, f"OutputName{n}", ""),
                        "enabled": self.get(GROUP, f"OutputEnabled{n}") == "1",
                        "containers": [c["name"] for c in self.containers()
                                       if self.get(GROUP, f"OutputEnabled{n}_OutputMedium{c['index']}", "0")
                                       not in ("0", "")]})
            n += 1
        return out

    def container(self, name: str) -> dict:
        found = [c for c in self.containers() if c["name"].lower() == str(name).lower()]
        if not found:
            raise OutJobError(f"no container named {name!r}; there are: "
                              f"{', '.join(c['name'] for c in self.containers())}")
        return found[0]

    def output(self, name) -> dict:
        outs = self.outputs()
        if isinstance(name, int) and not isinstance(name, bool):
            if 1 <= name <= len(outs):
                return outs[name - 1]
            raise OutJobError(f"no output number {name}; there are {len(outs)}")
        found = [o for o in outs if o["name"].lower() == str(name).lower()]
        if not found:
            raise OutJobError(f"no output named {name!r}; there are: {', '.join(o['name'] for o in outs)}")
        if len(found) > 1:
            raise OutJobError(f"{len(found)} outputs are named {name!r}; give its number "
                              f"({', '.join(str(o['index']) for o in found)}) instead")
        return found[0]

    def output_keys(self, n: int) -> List[Tuple[str, str]]:
        """The keys of output n, as (key with n replaced by '{n}', value)."""
        out = []
        for k, v in self.entries(GROUP):
            m = k is not None and _OUTPUT_KEY.match(k)
            if m and int(m.group(2)) == n:
                out.append((m.group(1) + "{n}" + (m.group(3) or ""), v))
        return out

    def _next_position(self, m: int) -> int:
        used = [int(v) for k, v in self.entries(GROUP)
                if k is not None and (x := _ENABLED_IN.match(k)) and int(x.group(2)) == m and v.isdigit()]
        return max(used, default=0) + 1

    def place(self, n: int, m: int, inside: bool) -> None:
        key = f"OutputEnabled{n}_OutputMedium{m}"
        current = int(self.get(GROUP, key, "0") or 0)
        if inside and not current:
            self.set(GROUP, key, str(self._next_position(m)), after=rf"OutputEnabled{n}(_|$)")
        elif not inside and current:
            self.set(GROUP, key, "0")
            self._close_gap(m, current)

    def _close_gap(self, m: int, position: int) -> None:
        for e in self.entries(GROUP):
            x = e[0] is not None and _ENABLED_IN.match(e[0])
            if x and int(x.group(2)) == m and e[1].isdigit() and int(e[1]) > position:
                e[1] = str(int(e[1]) - 1)

    # --- operations -----------------------------------------------------------

    def add_container(self, name: str, kind: str, folder: str = "", file_name: str = "",
                      open_outputs: Optional[bool] = None) -> int:
        if any(c["name"].lower() == name.lower() for c in self.containers()):
            raise OutJobError(f"there is already a container named {name!r}")
        if kind not in CONTAINER_TYPES:
            raise OutJobError(f"container type is one of {', '.join(CONTAINER_TYPES)}")
        type_ = CONTAINER_TYPES[kind]
        others = [c["name"] for c in self.containers() if c["type"] == type_ == "Publish"]
        if others and not (folder or file_name):
            # PDFs are named by the container: without its own folder or file
            # name it writes the same file as the other PDF container
            raise OutJobError(f"give a folder or file_name: without one the new PDF container writes "
                              f"the same file as {others[0]!r}")
        m = len(self.containers()) + 1
        self.set(GROUP, f"OutputMedium{m}", name, after=r"OutputMedium\d+")
        self.set(GROUP, f"OutputMedium{m}_Type", type_, after=r"OutputMedium\d+")
        for o in self.outputs():
            self.set(GROUP, f"OutputEnabled{o['index']}_OutputMedium{m}", "0",
                     after=rf"OutputEnabled{o['index']}(_|$)")
        # settings: those of a container of the same type here, else Altium's
        like = next((c["index"] for c in self.containers() if c["type"] == type_ and c["index"] != m), None)
        for section, defaults in zip((PUBLISH, GENERATED), _DEFAULTS[type_]):
            if like is not None:
                keys = [(x.group(1), v) for k, v in self.entries(section) or []
                        if k is not None and (x := _NUMBERED.match(k)) and int(x.group(2)) == like]
            else:
                keys = defaults
            if like is not None:
                # not where the copied container writes, nor its file name
                fresh = dict(defaults)
                keys = [(k, fresh[k] if k in _LOCATION_KEYS and k in fresh else v) for k, v in keys]
            for key, value in keys:
                self.set(section, f"{key}{m}", value)
        # A new container does not open what it writes unless asked: a BOM
        # opening in Excel after every run is in the way
        self.set_container(name, folder=folder, file_name=file_name,
                           open_outputs=bool(open_outputs), _fresh=True)
        return m

    def set_container(self, name: str, new_name: str = "", folder: Optional[str] = None,
                      file_name: Optional[str] = None, open_outputs: Optional[bool] = None,
                      _fresh: bool = False) -> None:
        c = self.container(name)
        m = c["index"]
        if c["type"] == "Printer" and (folder or file_name):
            raise OutJobError(f"{c['name']!r} prints on paper: it has no folder or file name")
        if new_name:
            if new_name.lower() != c["name"].lower() and any(
                    x["name"].lower() == new_name.lower() for x in self.containers()):
                raise OutJobError(f"there is already a container named {new_name!r}")
            self.set(GROUP, f"OutputMedium{m}", new_name)
        if folder is not None and (folder or _fresh):
            # a folder of the container's own, relative to the project. Altium
            # writes generated files to base path + custom folder, but a PDF
            # to its base path alone (checked by running both)
            folder = folder.strip("\\/").replace("/", "\\")
            self.set(PUBLISH, f"OutputPathMedia{m}", "")
            self.set(PUBLISH, f"OutputPathMediaValue{m}", "")
            self.set(PUBLISH, f"OutputPathOutputer{m}", "[Output Custom]")
            self.set(PUBLISH, f"OutputPathOutputerPrefix{m}", "")
            if c["type"] == "Publish":
                self.set(PUBLISH, f"OutputBasePath{m}", f".\\{folder}\\" if folder else ".\\")
                self.set(PUBLISH, f"OutputPathOutputerValue{m}", "")
            else:
                self.set(PUBLISH, f"OutputBasePath{m}", ".\\")
                self.set(PUBLISH, f"OutputPathOutputerValue{m}", folder)
        if file_name:
            if c["type"] != "Publish":
                raise OutJobError("file_name is for PDF containers; generated files are named by their output")
            if not file_name.lower().endswith(".pdf"):
                file_name += ".PDF"
            self.set(PUBLISH, f"OutputFileName{m}", file_name)
            self.set(PUBLISH, f"OutputFileNameSpecial{m}", "")
        if open_outputs is not None:
            self.set(PUBLISH, f"OpenOutput{m}", "1" if open_outputs else "0")
            self.set(GENERATED, f"OpenOutputs{m}", "1" if open_outputs else "0")

    def remove_container(self, name: str) -> None:
        c = self.container(name)
        m = c["index"]
        if c["type"] == "Printer":
            raise OutJobError("the Print Job container cannot be removed")
        last = len(self.containers())

        def mine(k):
            x = _MEDIUM_KEY.match(k) or _ENABLED_IN.match(k)
            return bool(x) and int(x.group(1) if _MEDIUM_KEY.match(k) else x.group(2)) == m

        def renumber(k):
            x = _MEDIUM_KEY.match(k)
            if x and int(x.group(1)) > m:
                return f"OutputMedium{int(x.group(1)) - 1}{x.group(2) or ''}"
            x = _ENABLED_IN.match(k)
            if x and int(x.group(2)) > m:
                return f"OutputEnabled{x.group(1)}_OutputMedium{int(x.group(2)) - 1}"
            return k

        self.delete_where(GROUP, mine)
        self.rename_where(GROUP, renumber)
        for section in (PUBLISH, GENERATED):
            self.delete_where(section, lambda k: bool(x := _NUMBERED.match(k)) and int(x.group(2)) == m)
            self.rename_where(section, lambda k: f"{x.group(1)}{int(x.group(2)) - 1}"
                              if (x := _NUMBERED.match(k)) and m < int(x.group(2)) <= last else k)
        if self.get(GROUP, "TargetOutputMedium") == c["name"]:
            self.set(GROUP, "TargetOutputMedium", self.containers()[0]["name"])

    def copy_output(self, source: "OutJob", output, new_name: str = "", containers=()) -> int:
        src = source.output(output)
        n = len(self.outputs()) + 1
        for key, value in source.output_keys(src["index"]):
            if _ENABLED_IN.match(key.replace("{n}", "0")):
                continue    # the source's containers mean nothing here
            self.set(GROUP, key.replace("{n}", str(n)), value)
        if new_name:
            self.set(GROUP, f"OutputName{n}", new_name)
        for c in self.containers():
            self.set(GROUP, f"OutputEnabled{n}_OutputMedium{c['index']}", "0", after=rf"OutputEnabled{n}(_|$)")
        for name in _names(containers):
            self.place(n, self.container(name)["index"], True)
        return n

    def remove_output(self, output) -> None:
        o = self.output(output)
        n = o["index"]
        for c in self.containers():
            self.place(n, c["index"], False)

        def renumber(k):
            x = _OUTPUT_KEY.match(k)
            if x and int(x.group(2)) > n:
                return f"{x.group(1)}{int(x.group(2)) - 1}{x.group(3) or ''}"
            return k

        self.delete_where(GROUP, lambda k: bool(x := _OUTPUT_KEY.match(k)) and int(x.group(2)) == n)
        self.rename_where(GROUP, renumber)

    def set_output(self, output, new_name: str = "", enabled: Optional[bool] = None,
                   document_path: Optional[str] = None, containers=None) -> None:
        """containers: {name: true/false} to put the output in or take it out
        of each, or a list of names to put it in (as copy_output takes it)."""
        if containers is None or isinstance(containers, dict):
            placing = containers or {}
            if not all(isinstance(k, str) for k in placing):
                raise OutJobError("containers: the container names must be text")
        else:
            placing = {name: True for name in _names(containers)}
        o = self.output(output)
        n = o["index"]
        if new_name:
            self.set(GROUP, f"OutputName{n}", new_name)
        if enabled is not None:
            self.set(GROUP, f"OutputEnabled{n}", "1" if enabled else "0")
        if document_path is not None:
            self.set(GROUP, f"OutputDocumentPath{n}", document_path)
        for name, inside in placing.items():
            self.place(n, self.container(name)["index"], bool(inside))

    def set_output_setting(self, output, key: str, value: str) -> str:
        """Set Key=value inside the output's configuration; the key must exist."""
        o = self.output(output)
        n = o["index"]
        hits = []
        for e in self.entries(GROUP):
            if e[0] is not None and re.fullmatch(rf"Configuration{n}_Item\d+", e[0]):
                parts = e[1].split("|")
                for i, part in enumerate(parts):
                    if part.split("=", 1)[0] == key:
                        hits.append((e, parts, i))
        if not hits:
            raise OutJobError(f"output {o['name']!r} has no setting {key!r}")
        old = hits[0][1][hits[0][2]].split("=", 1)[1]
        for e, parts, i in hits:
            parts[i] = f"{key}={value}"
            e[1] = "|".join(parts)
        return old


def _names(containers) -> List[str]:
    """Container names given as a list (or one name as text)."""
    if isinstance(containers, str):
        return [containers]
    if not isinstance(containers, (list, tuple)) or not all(isinstance(c, str) for c in containers):
        raise OutJobError("containers is a list of container names")
    return list(containers)


def apply(job: OutJob, operations: list, load=None) -> Tuple[List[str], List[str]]:
    """Run operations in order; return what was done and the errors.
    load(path) returns the OutJob an operation copies from.

    Each operation runs on a copy of the job, taken over only when it
    succeeds, so a failed one leaves no half-done edit behind. The caller
    writes nothing when there are errors."""
    done, errors = [], []
    for i, op in enumerate(operations or [], 1):
        trial = copy.deepcopy(job)
        try:
            done.append(_run(trial, op, load))
            job.sections = trial.sections
        except KeyError as e:
            errors.append(f"operation {i}: missing {e.args[0]!r}")
        except (OutJobError, OSError) as e:
            errors.append(f"operation {i}: {e}")
        except (TypeError, ValueError, AttributeError) as e:
            errors.append(f"operation {i}: invalid argument ({e})")
    return done, errors


def _run(job: OutJob, op, load) -> str:
    """Run one operation on job; return what was done."""
    if not isinstance(op, dict) or "op" not in op:
        raise OutJobError("each operation is a dict with an 'op'")
    kind = op["op"]
    if kind == "add_container":
        m = job.add_container(op["name"], op.get("type", "generated_files"), op.get("folder", ""),
                              op.get("file_name", ""), op.get("open_outputs"))
        return f"added container {op['name']!r} ({m})"
    if kind == "set_container":
        job.set_container(op["name"], op.get("new_name", ""), op.get("folder"), op.get("file_name"),
                          op.get("open_outputs"))
        return f"changed container {op['name']!r}"
    if kind == "remove_container":
        job.remove_container(op["name"])
        return f"removed container {op['name']!r}"
    if kind == "copy_output":
        source = job if not op.get("from_outjob") else load(op["from_outjob"])
        n = job.copy_output(source, op["output"], op.get("new_name", ""), op.get("containers", []))
        return f"copied output {op['output']!r} as {job.outputs()[n - 1]['name']!r} ({n})"
    if kind == "remove_output":
        job.remove_output(op["output"])
        return f"removed output {op['output']!r}"
    if kind == "set_output":
        job.set_output(op["output"], op.get("new_name", ""), op.get("enabled"), op.get("document_path"),
                       op.get("containers"))
        return f"changed output {op['output']!r}"
    if kind == "set_output_setting":
        old = job.set_output_setting(op["output"], op["key"], str(op["value"]))
        return f"{op['output']!r}: {op['key']} {old!r} -> {op['value']!r}"
    raise OutJobError(f"unknown op {kind!r}")


def read(path) -> OutJob:
    return OutJob(*decode(Path(path).read_bytes()))


def write(path, job: OutJob) -> None:
    """Write through a temporary file, so a failed write leaves the old one."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(job.text().encode(job.encoding))
    tmp.replace(path)


def new(name: str) -> OutJob:
    return OutJob(EMPTY_OUTJOB.format(name=name).replace("\n", "\r\n"))
