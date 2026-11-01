"""Offline tests for pcblib_file (no Altium needed).

Run: python -m unittest discover -s server/tests -p "test_pcblib_file.py"
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

import pythoncom
from win32com import storagecon as sc

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import pcblib_file as pf  # noqa: E402

CREATE = sc.STGM_CREATE | sc.STGM_READWRITE | sc.STGM_SHARE_EXCLUSIVE


def body(name, rotx="0.000", rotz="0.000", dz="0mil", embed="TRUE"):
    text = ("V7_LAYER=MECHANICAL13|NAME= |KIND=0|STANDOFFHEIGHT=0mil|OVERALLHEIGHT=35.4331mil"
            "|MODELID={81E1FACD-F016-4B2C-BF65-3CCE43694708}|MODEL.EMBED=%s|MODEL.NAME=%s"
            "|MODEL.2D.ROTATION=0.000|MODEL.3D.ROTX=%s|MODEL.3D.ROTY=0.000|MODEL.3D.ROTZ=%s"
            "|MODEL.3D.DZ=%s|MODEL.MODELTYPE=0" % (embed, name, rotx, rotz, dz))
    raw = text.encode("latin-1") + b"\x00"
    # A body record: type byte, binary header, the property text, then
    # binary outline data
    return b"\x0c" + struct.pack("<I", len(raw)) + b"\xff" * 9 + raw + b"\x04\x00\x90\x07\x18\xc1"


def write_library(path, footprints):
    """footprints: {storage name: (pattern, data bytes)}"""
    root = pythoncom.StgCreateDocfile(str(path), CREATE, 0)
    for storage_name, (pattern, data) in footprints.items():
        st = root.CreateStorage(storage_name, CREATE, 0, 0)
        params = ("|PATTERN=%s|HEIGHT=0mil|DESCRIPTION=" % pattern).encode("latin-1") + b"\x00"
        s = st.CreateStream("Parameters", CREATE, 0, 0)
        s.Write(struct.pack("<I", len(params)) + params)
        s = st.CreateStream("Data", CREATE, 0, 0)
        s.Write(data)
        s = None
        st.Commit(0)
        st = None
    root.Commit(0)
    root = None


class PcbLibFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "Test, Lib.PcbLib"
        long_name = "QFN50P500X300X160-17N-LONGER-THAN-31"
        write_library(self.path, {
            "OSC": ("OSC", body("ECS-2520.STEP", rotx="90.000", rotz="90.000")),
            "TWO": ("TWO", body("A.step", dz="0.5mm") + body("", embed="FALSE")),
            long_name[:31]: (long_name, body("LONG.step", rotz="270.000")),
        })

    def tearDown(self):
        self.dir.cleanup()

    def test_reads_rotation_and_offset_per_footprint(self):
        saved = pf.body_placements(self.path, ["osc", "TWO", "QFN50P500X300X160-17N-LONGER-THAN-31", "MISSING"])
        self.assertEqual(saved["OSC"], [{"model_name": "ECS-2520.STEP", "embedded": True, "rotation_x": 90.0,
                                         "rotation_y": 0.0, "rotation_z": 90.0, "model_z_offset": 0.0}])
        self.assertEqual([b["model_name"] for b in saved["TWO"]], ["A.step", ""])
        self.assertAlmostEqual(saved["TWO"][0]["model_z_offset"], 19.685, places=3)
        self.assertFalse(saved["TWO"][1]["embedded"])
        self.assertEqual(saved["QFN50P500X300X160-17N-LONGER-THAN-31"][0]["rotation_z"], 270.0)
        self.assertNotIn("MISSING", saved)

    def test_unreadable_file_gives_nothing(self):
        self.assertEqual(pf.body_placements(Path(self.dir.name) / "none.PcbLib", ["OSC"]), {})

    def test_merge_matches_by_model_name_in_order(self):
        bodies = [{"model_file": "a.STEP"}, {"model_file": "new.step"}, {"model_file": "A.step"}]
        saved = [{"model_name": "A.step", "rotation_x": 1.0, "rotation_y": 0.0, "rotation_z": 0.0, "model_z_offset": 0.0},
                 {"model_name": "A.step", "rotation_x": 2.0, "rotation_y": 0.0, "rotation_z": 0.0, "model_z_offset": 0.0}]
        pf.merge_placements(bodies, saved)
        self.assertEqual([b["rotation_x"] for b in bodies], [1.0, None, 2.0])
        self.assertEqual(bodies[1]["placement"], "not in the saved library file")
        self.assertEqual(bodies[0]["placement"], "saved library file")

    def test_adds_placements_to_single_and_all_dumps(self):
        single = {"library_path": str(self.path), "footprint_name": "OSC",
                  "bodies_3d": [{"model_file": "ECS-2520.STEP"}]}
        pf.add_body_placements(single)
        self.assertEqual(single["bodies_3d"][0]["rotation_z"], 90.0)

        every = {"library_path": str(self.path), "footprints": [
            {"footprint_name": "OSC", "bodies_3d": [{"model_file": "ECS-2520.STEP"}]},
            {"footprint_name": "TWO", "bodies_3d": [{"model_file": "A.step"}, {"model_file": ""}]},
            {"footprint_name": "EMPTY", "bodies_3d": []}]}
        pf.add_body_placements(every)
        self.assertEqual(every["footprints"][0]["bodies_3d"][0]["rotation_x"], 90.0)
        self.assertEqual(every["footprints"][1]["bodies_3d"][1]["placement"], "saved library file")

    def test_inventory_is_left_alone(self):
        inventory = {"library_path": str(self.path), "footprints": [{"name": "OSC", "pads": 4}]}
        self.assertEqual(pf.add_body_placements(dict(inventory)), inventory)


if __name__ == "__main__":
    unittest.main()
