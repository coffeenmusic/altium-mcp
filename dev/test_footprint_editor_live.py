"""Opt-in live checks on a scratch copy of an open project's PcbLib; no saves."""

import argparse
import asyncio
import ctypes
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))


def editor_windows():
    import win32gui
    import win32process
    from win_processes import pids_by_name
    pids = set(pids_by_name("X2.EXE"))
    windows = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsHungAppWindow.argtypes = [ctypes.c_void_p]
    user32.IsHungAppWindow.restype = ctypes.c_int

    def collect(hwnd, _):
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if pid in pids and "Altium" in title:
            windows.append({"pid": pid, "title": title, "responding": not bool(user32.IsHungAppWindow(hwnd))})
    win32gui.EnumWindows(collect, None)
    return windows


def disk_state(path):
    if not path.is_file():
        return {"exists": False}
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"exists": True, "sha256": digest, "mtime_ns": path.stat().st_mtime_ns}


def document_states(info):
    return {d["path"]: (d["kind"], d["state"]) for d in info["documents"]}


def primitive_state(snapshot, omit=()):
    # Showing an existing footprint can reorder its internal group iterator.
    return sorted(json.dumps({k: v for k, v in p.items() if k not in omit}, sort_keys=True)
                  for p in snapshot["primitives"])


async def run(args):
    root = REPO / ".altium-mcp" / f"footprint_editor_{time.strftime('%Y%m%d_%H%M%S')}"
    root.mkdir(parents=True)
    report = {"root": str(root), "documents_saved": False, "steps": [], "windows": editor_windows()}

    def record(label, value):
        (root / f"{label}.json").write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="ascii")

    # Gate before importing the bridge, creating scratch files or dispatching.
    if not report["windows"] or any(not w["responding"] for w in report["windows"]):
        report.update(status="blocked", reason="Altium is absent or unresponsive; no Altium commands were dispatched")
        record("report", report)
        print(json.dumps(report, indent=2))
        return 2
    if not args.exercise:
        report.update(status="not_run", reason="Pass --exercise with --project and --library")
        record("report", report)
        print(json.dumps(report, indent=2))
        return 0
    if not args.project or not args.library:
        raise ValueError("--exercise requires --project and --library")

    import main
    from altium_files import write_altium_text
    original = None
    original_primitives = None
    previous_editor = None
    scratch = None
    opened = False
    unknown = False

    async def call(label, function, *values, **options):
        nonlocal unknown
        # A completed library-open response can precede a brief UI repaint.
        # Require a stable responsive window rather than treating that as a
        # frozen executor or queueing another command while it paints.
        stable = 0
        for _ in range(40):
            windows = editor_windows()
            stable = stable + 1 if windows and all(w["responding"] for w in windows) else 0
            if stable >= 3:
                break
            await asyncio.sleep(.5)
        else:
            unknown = True
            raise RuntimeError("Altium did not become responsive; no further command dispatched")
        result = json.loads(await function(None, *values, **options))
        record(label, result)
        report["steps"].append({"label": label, **{k: result.get(k) for k in
                                 ("success", "error", "updated_count", "verified_count", "saved")}})
        print(json.dumps(report["steps"][-1]), flush=True)
        if result.get("outcome_unknown") or result.get("close_outcome_unknown"):
            unknown = True
            raise RuntimeError("Executor outcome unknown; no more Altium commands will be sent")
        return result

    def require(result):
        assert result.get("success", True) and not result.get("error"), result
        assert result.get("saved", False) is False, result
        return result

    async def snapshot(label):
        return require(await call(label, main.get_footprint_primitives, str(scratch), name))

    try:
        project = str(Path(args.project).resolve())
        library = Path(args.library).resolve()
        require(await call("select_project", main.select_project, project))
        original = require(await call("original_before", main.get_project_info))
        assert PureWindowsPath(original["project_path"]) == PureWindowsPath(project)
        assert any(PureWindowsPath(d["path"]) == PureWindowsPath(library) and d["state"] != "closed"
                   for d in original["documents"]), "The source library must already be open in this project"
        originals = [Path(d["path"]) for d in original["documents"]]
        report["original_disk_before"] = {str(p): disk_state(p) for p in originals}
        record("original_disk_before", report["original_disk_before"])
        focused = require(await call("focus_library", main.open_document, str(library)))
        previous_editor = focused.get("previous_document_path")
        original_primitives = require(await call("original_library_before", main.get_footprint_primitives, str(library), "*"))
        scratch = root / "Scratch_\u03a9.PcbLib"
        shutil.copy2(library, scratch)
        report["scratch_disk_before"] = disk_state(scratch)
        require(await call("open_scratch", main.open_document, str(scratch)))
        opened = True
        constants = require(await call("constants", main.get_pcb_api_constants, ["eRoundedRectangular", "eMechanical13", "eCacheManual"]))
        name = "__MCP_EDITOR_" + str(time.time_ns())
        spec = root / "create.txt"
        lines = [f"FPLIB|{scratch}", f"FOOTPRINT|{name}|Temporary verification fixture"]
        for n in range(1, 7):
            lines.append(f"PAD|{n}|{n * 50}|0|0|Top Layer|0|0|0|0|0|40|24|{constants['constants']['eRoundedRectangular']}|10")
        lines += ["TRACK|0|100|100|100|5|Top Overlay", "TRACK|0|200|100|200|5|Mechanical Layer 15",
                  "ARC|0|0|100|0|90|5|Top Overlay", "FILL|0|300|100|400|0|Top Overlay"]
        write_altium_text(spec, "\n".join(lines) + "\n")
        created = require(await call("create_fixture", main.create_footprints_batch, str(spec)))
        assert created["created"] == 1 and created["primitive_errors"] == 0
        before = await snapshot("fixture_before")
        operations = [{"target": {k: p[k] for k in ("type", "layer", *main.footprint_editor.GEOMETRY[p["type"]])},
                       "layer": "Mechanical Layer 13" if p["type"] != "fill" else "Mechanical 15"}
                      for p in before["primitives"] if p["type"] in main.footprint_editor.GEOMETRY]
        missing = json.loads(json.dumps(operations[0]))
        missing["target"][main.footprint_editor.GEOMETRY[missing["target"]["type"]][0]] += 10000
        refused = await call("reject_missing_primitive", main.edit_footprint_primitives, str(scratch), name,
                             operations=[operations[0], missing])
        assert refused["success"] is False and refused["may_have_partial_changes"] is False
        assert primitive_state(await snapshot("after_primitive_refusal")) == primitive_state(before)
        require(await call("edit_layers", main.edit_footprint_primitives, str(scratch), name, operations=operations))
        layers = await snapshot("after_layer_edit")
        assert primitive_state(before, ("layer",)) == primitive_state(layers, ("layer",))
        names = [str(n) for n in range(1, 7)]
        require(await call("manual_pads", main.set_footprint_pad_settings, str(scratch), name, names,
                           mask_expansion_mm=.05, paste_expansion_mm=0))
        manual = await snapshot("after_manual_pads")
        for pad in (p for p in manual["primitives"] if p["type"] == "pad"):
            assert pad["mask_mode"] == 2 and pad["paste_mode"] == 2 and pad["locked"]
            assert abs(pad["mask_expansion"] * .0254 - .05) <= .0000254
            assert abs(pad["paste_expansion"]) < .0001
            old = next(p for p in before["primitives"] if p["type"] == "pad" and p["name"] == pad["name"])
            settings = {"mask_mode", "paste_mode", "mask_expansion", "paste_expansion", "pad_settings", "cache_readable"}
            assert {k: v for k, v in old.items() if k not in settings} == {k: v for k, v in pad.items() if k not in settings}
        refused = await call("reject_missing_pad", main.set_footprint_pad_settings, str(scratch), name,
                             ["1", "MCP_MISSING"], mask_expansion_mm=.1)
        assert refused["success"] is False and refused["may_have_partial_changes"] is False
        assert primitive_state(await snapshot("after_pad_refusal")) == primitive_state(manual)
        require(await call("preserve_omitted_paste", main.set_footprint_pad_settings, str(scratch), name, names,
                           mask_expansion_mm=.06))
        omitted = await snapshot("after_omitted_paste")
        assert all(p["paste_mode"] == 2 and abs(p["paste_expansion"]) < .0001
                   for p in omitted["primitives"] if p["type"] == "pad")
        require(await call("rule_reset", main.set_footprint_pad_settings, str(scratch), name, names, mode="rule"))
        reset = await snapshot("after_rule_reset")
        assert all(p["mask_mode"] == 1 and p["paste_mode"] == 1 for p in reset["primitives"] if p["type"] == "pad")
        write_altium_text(spec, f"FPLIB|{scratch}\nFOOTPRINT|MCP_INVALID|Never created\nTRACK|0|0|10|0|5|MCP_UNKNOWN_LAYER\n")
        refused = await call("reject_unknown_batch_layer", main.create_footprints_batch, str(spec))
        assert refused["success"] is False and refused["created"] == 0 and not refused["may_have_partial_changes"]
        clean_only = await call("reject_clean_close", main.close_document, str(scratch))
        assert clean_only["success"] is False and not clean_only["may_have_partial_changes"]
        require(await call("discard_scratch", main.close_document, str(scratch), require_clean=False))
        opened = False
        assert disk_state(scratch) == report["scratch_disk_before"]
        original_after = require(await call("original_library_after", main.get_footprint_primitives, str(library), "*"))
        assert original_after == original_primitives
        require(await call("restore_project", main.select_project, project))
        info_after = require(await call("original_after", main.get_project_info))
        assert document_states(info_after) == document_states(original)
        report["original_disk_after"] = {str(p): disk_state(p) for p in originals}
        assert report["original_disk_after"] == report["original_disk_before"]
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc), executor_outcome_unknown=unknown)
    finally:
        if opened and not unknown:
            try:
                require(await call("cleanup_scratch", main.close_document, str(scratch), require_clean=False))
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        if previous_editor and not unknown:
            try:
                require(await call("restore_editor", main.open_document, previous_editor))
            except Exception as exc:
                report["restore_error"] = str(exc)
        record("report", report)
        print(json.dumps({"status": report["status"], "root": str(root), "documents_saved": False}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true")
    parser.add_argument("--project")
    parser.add_argument("--library")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
