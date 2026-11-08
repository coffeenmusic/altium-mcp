"""Unit tests for outjob_edit (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_outjob_edit -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import outjob_edit as oe  # noqa: E402
from outjob_parser import parse_outjob  # noqa: E402

CAM = "\r\n".join("""[OutputJobFile]
Version=1.0

[OutputGroup1]
Name=CAM.OutJob
TargetOutputMedium=CAM Data
OutputMedium1=Print Job
OutputMedium1_Type=Printer
OutputMedium2=CAM Data
OutputMedium2_Type=GeneratedFiles
OutputMedium3=ODB
OutputMedium3_Type=GeneratedFiles
OutputType1=Gerber
OutputName1=Gerber Files
OutputEnabled1=1
OutputEnabled1_OutputMedium1=0
OutputEnabled1_OutputMedium2=1
OutputEnabled1_OutputMedium3=0
Configuration1_Name1=OutputConfigurationParameter1
Configuration1_Item1=GerberUnit=Imperial|NumberOfDecimals=5|Record=GerberView
OutputType2=NC Drill
OutputName2=NC Drill Files
OutputEnabled2=1
OutputEnabled2_OutputMedium1=0
OutputEnabled2_OutputMedium2=2
OutputEnabled2_OutputMedium3=0
Configuration2_Name1=OutputConfigurationParameter1
Configuration2_Item1=Units=Imperial|Record=DrillView
OutputType3=ODB
OutputName3=ODB++ Files
OutputEnabled3=1
OutputEnabled3_OutputMedium1=0
OutputEnabled3_OutputMedium2=0
OutputEnabled3_OutputMedium3=1

[PublishSettings]
OutputBasePath2=.\\
OutputPathOutputer2=[Output Custom]
OutputPathOutputerValue2=CAM
OpenOutput2=1
OutputBasePath3=.\\
OutputPathOutputer3=[Output Custom]
OutputPathOutputerValue3=ODB
OpenOutput3=1

[GeneratedFilesSettings]
RelativeOutputPath2=.\\
OpenOutputs2=1
RelativeOutputPath3=.\\
OpenOutputs3=1
""".split("\n"))


def job():
    return oe.OutJob(CAM)


def placed(j, n):
    return {c["name"]: j.get(oe.GROUP, f"OutputEnabled{n}_OutputMedium{c['index']}")
            for c in j.containers()}


class OutJobEditTest(unittest.TestCase):
    def test_round_trip_is_exact(self):
        self.assertEqual(job().text(), CAM)

    def test_remove_output_renumbers_and_closes_the_gap(self):
        j = job()
        j.remove_output("Gerber Files")
        self.assertEqual([o["name"] for o in j.outputs()], ["NC Drill Files", "ODB++ Files"])
        self.assertEqual(placed(j, 1)["CAM Data"], "1")      # NC Drill moved up to position 1
        self.assertEqual(j.get(oe.GROUP, "Configuration1_Item1"), "Units=Imperial|Record=DrillView")
        self.assertIsNone(j.get(oe.GROUP, "OutputType3"))

    def test_remove_container_renumbers_every_section(self):
        j = job()
        j.remove_container("CAM Data")
        self.assertEqual([c["name"] for c in j.containers()], ["Print Job", "ODB"])
        self.assertEqual(j.get(oe.PUBLISH, "OutputPathOutputerValue2"), "ODB")
        self.assertIsNone(j.get(oe.PUBLISH, "OutputPathOutputerValue3"))
        self.assertEqual(placed(j, 3), {"Print Job": "0", "ODB": "1"})
        self.assertEqual(j.get(oe.GROUP, "TargetOutputMedium"), "Print Job")
        with self.assertRaises(oe.OutJobError):
            j.remove_container("Print Job")

    def test_add_containers_and_copy_an_output_into_one(self):
        j = job()
        j.add_container("Fab", "pdf", folder="Fab", file_name="fab")
        j.add_container("More", "generated_files", folder="Out/More")
        fab, more = j.container("Fab")["index"], j.container("More")["index"]
        self.assertEqual(j.get(oe.PUBLISH, f"OutputBasePath{fab}"), ".\\Fab\\")    # a PDF goes to its base path
        self.assertEqual(j.get(oe.PUBLISH, f"OutputFileName{fab}"), "fab.PDF")
        self.assertEqual(j.get(oe.PUBLISH, f"OutputPathOutputerValue{more}"), "Out\\More")
        self.assertEqual(j.get(oe.GENERATED, f"OpenOutputs{more}"), "0")           # new ones do not open
        n = j.copy_output(job(), "Gerber Files", new_name="Gerber copy", containers=["More"])
        self.assertEqual(placed(j, n)["More"], "1")
        self.assertEqual(placed(j, n)["CAM Data"], "0")
        self.assertEqual(j.get(oe.GROUP, f"Configuration{n}_Item1"), j.get(oe.GROUP, "Configuration1_Item1"))
        with self.assertRaises(oe.OutJobError):
            j.add_container("fab", "pdf")

    def test_a_new_container_does_not_copy_where_another_writes(self):
        j = job()
        j.set_container("CAM Data", folder="Cam")
        j.add_container("Fab", "pdf", folder="Fab", file_name="fab")
        more = j.add_container("More", "generated_files")
        self.assertEqual(j.get(oe.PUBLISH, f"OutputPathOutputerValue{more}"), "")   # not CAM Data's "Cam"
        self.assertEqual(j.get(oe.PUBLISH, f"OutputBasePath{more}"), ".\\")
        # a second PDF container needs a place or a name of its own
        with self.assertRaises(oe.OutJobError) as caught:
            j.add_container("Fab 2", "pdf")
        self.assertIn("Fab", str(caught.exception))
        self.assertNotIn("Fab 2", [c["name"] for c in j.containers()])
        fab2 = j.add_container("Fab 2", "pdf", folder="Other")
        self.assertEqual(j.get(oe.PUBLISH, f"OutputFileName{fab2}"), "")             # not "fab.PDF"
        self.assertEqual(j.get(oe.PUBLISH, f"OutputBasePath{fab2}"), ".\\Other\\")

    def test_set_output_and_setting(self):
        j = job()
        j.set_output("ODB++ Files", containers={"CAM Data": True, "ODB": False})
        self.assertEqual(placed(j, 3), {"Print Job": "0", "CAM Data": "3", "ODB": "0"})
        self.assertEqual(j.set_output_setting("Gerber Files", "GerberUnit", "Metric"), "Imperial")
        self.assertIn("GerberUnit=Metric|", j.get(oe.GROUP, "Configuration1_Item1"))
        with self.assertRaises(oe.OutJobError):
            j.set_output_setting("Gerber Files", "NoSuchKey", "1")

    def test_apply_reports_each_failure_and_runs_the_rest(self):
        j = job()
        done, errors = oe.apply(j, [{"op": "remove_output", "output": "Nope"},
                                    {"op": "set_container", "name": "ODB", "new_name": "ODB++"},
                                    {"op": "frobnicate"}, {"op": "remove_container"}])
        self.assertEqual(len(done), 1)
        self.assertEqual(len(errors), 3)
        self.assertEqual(j.container("ODB++")["index"], 3)

    def test_the_parser_reads_the_result(self):
        j = job()
        j.add_container("BOM", "generated_files", folder="BOM")
        j.copy_output(job(), "NC Drill Files", containers=["BOM"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.OutJob")
            oe.write(path, j)
            r = parse_outjob(path, include_raw=False)
        self.assertEqual([c["name"] for c in r["containers"]], ["Print Job", "CAM Data", "ODB", "BOM"])
        self.assertEqual(r["outputs"][-1]["connected_containers"], ["BOM"])

    def test_new_outjob(self):
        j = oe.new("Fresh.OutJob")
        self.assertEqual([c["type"] for c in j.containers()], ["Printer"])
        self.assertEqual(j.outputs(), [])


if __name__ == "__main__":
    unittest.main()
