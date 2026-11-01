"""Unit tests for project_components (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_project_components -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from project_components import compact_json, select  # noqa: E402


def comp(designator, sheet="Main.SchDoc", parameters=None):
    c = {"designator": designator, "comment": "10k", "footprint": "0402", "library_reference": "RES", "sheet": sheet}
    if parameters is not None:
        c["parameters"] = parameters
    return c


DATA = {
    "project_path": "x.PrjPcb",
    "component_count": 5,
    "components": [comp("R1"), comp("R2", "Power.SchDoc"), comp("R10"), comp("C1", "Power.SchDoc"), comp("U1")],
}


class SelectTest(unittest.TestCase):
    def names(self, reply):
        return [c["designator"] for c in reply["components"]]

    def test_everything_when_it_fits(self):
        r = select(DATA)
        self.assertEqual(self.names(r), ["R1", "R2", "R10", "C1", "U1"])
        self.assertEqual((r["component_count"], r["matched"], r["returned"], r["next_offset"]), (5, 5, 5, None))
        self.assertEqual(r["project_path"], "x.PrjPcb")

    def test_designators_take_wildcards_and_ignore_case(self):
        self.assertEqual(self.names(select(DATA, designators=["r*", "U1"])), ["R1", "R2", "R10", "U1"])
        self.assertEqual(self.names(select(DATA, designators=["R?"])), ["R1", "R2"])

    def test_sheet(self):
        r = select(DATA, sheet="power.schdoc")
        self.assertEqual(self.names(r), ["R2", "C1"])
        self.assertEqual((r["component_count"], r["matched"]), (5, 2))

    def test_limit_and_offset(self):
        r = select(DATA, limit=2)
        self.assertEqual((self.names(r), r["next_offset"]), (["R1", "R2"], 2))
        r = select(DATA, offset=r["next_offset"], limit=2)
        self.assertEqual((self.names(r), r["next_offset"]), (["R10", "C1"], 4))
        r = select(DATA, offset=r["next_offset"], limit=2)
        self.assertEqual((self.names(r), r["next_offset"]), (["U1"], None))

    def test_offset_past_the_end(self):
        r = select(DATA, offset=50)
        self.assertEqual((r["returned"], r["next_offset"], r["matched"]), (0, None, 5))

    def test_size_cap_visits_every_component_once(self):
        data = {"components": [comp(f"R{i}") for i in range(500)]}
        seen, offset, pages = [], 0, 0
        while offset is not None:
            r = select(data, offset=offset, max_chars=4000)
            self.assertLessEqual(len(compact_json(r["components"])), 4000)
            seen += self.names(r)
            offset = r["next_offset"]
            pages += 1
        self.assertEqual(seen, [f"R{i}" for i in range(500)])
        self.assertGreater(pages, 5)

    def test_a_component_larger_than_the_cap_still_comes_back(self):
        big = comp("U1", parameters=[{"name": "Note", "value": "x" * 500}])
        r = select({"components": [big, comp("R1")]}, max_chars=100)
        self.assertEqual((self.names(r), r["next_offset"]), (["U1"], 1))

    def test_parameter_names_keep_only_those(self):
        data = {"components": [comp("R1", parameters=[{"name": "MPN", "value": "A"}, {"name": "Comment", "value": "B"}])]}
        r = select(data, parameter_names=["mpn"])
        self.assertEqual(r["components"][0]["parameters"], [{"name": "MPN", "value": "A"}])
        self.assertEqual(len(data["components"][0]["parameters"]), 2)


if __name__ == "__main__":
    unittest.main()
