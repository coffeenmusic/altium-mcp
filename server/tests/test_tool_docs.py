"""Every tool main.py registers is documented, and the read-only ones are
marked so (no Altium needed: only the registered tools are listed).

Run from server/:  python -m unittest tests.test_tool_docs -v
"""
import asyncio
import json
import os
import sys
import unittest

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, SERVER)

import main  # noqa: E402

ROOT = os.path.join(SERVER, "..")
READ_ONLY = ("get_screenshot", "objects_in_area", "clearance")
CHANGING = ("drag_copper",)


class ToolDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {t.name: t for t in asyncio.run(main.mcp.list_tools())}

    def test_manifest_and_readme_list_every_tool(self):
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as f:
            manifest = {t["name"] for t in json.load(f)["tools"]}
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertEqual(sorted(set(self.tools) - manifest), [])
        self.assertEqual(sorted(t for t in self.tools if f"`{t}`" not in readme), [])

    def test_read_only_marks(self):
        for name in READ_ONLY:
            annotations = self.tools[name].annotations
            self.assertTrue(annotations and annotations.readOnlyHint, name)
        for name in CHANGING:
            annotations = self.tools[name].annotations
            self.assertFalse(annotations and annotations.readOnlyHint, name)


if __name__ == "__main__":
    unittest.main()
