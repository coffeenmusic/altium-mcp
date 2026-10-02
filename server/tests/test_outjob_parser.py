"""Unit tests for outjob_parser (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_outjob_parser -v
or from server/:          python -m unittest tests.test_outjob_parser -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from outjob_parser import layer_name, parse_layer_set, parse_outjob, parse_pipe_record  # noqa: E402

SAMPLE = r"""[OutputJobFile]
Version=1.0

[OutputGroup1]
Name=Fabrication.OutJob
VariantName=[No Variations]
OutputMedium1=Print To Printer
OutputMedium1_Type=Printer
OutputMedium2=Fabrication Files
OutputMedium2_Type=GeneratedFiles
OutputType1=Gerber
OutputName1=Gerber Files
OutputCategory1=Fabrication
OutputDocumentPath1=
OutputVariantName1=
OutputEnabled1=1
OutputEnabled1_OutputMedium1=0
OutputEnabled1_OutputMedium2=1
Configuration1_Name1=OutputConfigurationParameter1
Configuration1_Item1=AddToAllPlots.Set=SerializeLayerHash.Version~2,ClassName~TPlotLayerStateArray,16908289~1|GerberUnit=Imperial|NumberOfDecimals=5|OriginPosition=Absolute|Plot.Set=SerializeLayerHash.Version~2,ClassName~TPlotLayerStateArray,16777217~1,16842751~1,16973830~1,16908289~1,16973848~1,16908290~0|PlotBoardProfile=True|PlotUsedDrillDrawingLayerPairs=False|Record=GerberView
OutputType2=ODB
OutputName2=ODB++ Files
OutputEnabled2=0
OutputEnabled2_OutputMedium1=0
OutputEnabled2_OutputMedium2=0
OutputType3=Test Points
OutputName3=Test Point Report
OutputEnabled3=1
OutputEnabled3_OutputMedium2=2
Configuration3_Name1=OutputConfigurationParameter1
Configuration3_Item1=GenerateIPCFormat=True|OriginPosition=Relative|OutputBoardOutline=False|Record=TestPointView|Units=Imperial

[PublishSettings]
OutputFilePath2=C:\Somewhere\Else\Project Outputs\Fabrication\
OutputFileNameMulti2==PCB_PART_NUMBER+'_'+PCB_REV
ReleaseManaged2=0

[GeneratedFilesSettings]
RelativeOutputPath2=C:\Somewhere\Else\Project Outputs\Fabrication\
AddToProject2=1
"""


class OutJobParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls.path = tempfile.mkstemp(suffix=".OutJob")
        with os.fdopen(fd, "w", encoding="cp1252", newline="\r\n") as f:
            f.write(SAMPLE)
        cls.data = parse_outjob(cls.path)

    @classmethod
    def tearDownClass(cls):
        os.remove(cls.path)

    def test_layer_names(self):
        self.assertEqual(layer_name(0x01000001), "Top Layer")
        self.assertEqual(layer_name(0x0100FFFF), "Bottom Layer")
        self.assertEqual(layer_name(0x01000002), "Mid Layer 1")
        self.assertEqual(layer_name(0x01020001), "Mechanical 1")
        self.assertEqual(layer_name(0x01020018), "Mechanical 24")
        self.assertEqual(layer_name(0x01030018), "Top Pad Master")
        self.assertTrue(layer_name(0x0103FFFF).startswith("Unknown layer"))

    def test_layer_set_skips_disabled_entries(self):
        layers = parse_layer_set("SerializeLayerHash.Version~2,ClassName~X,16908289~1,16908290~0")
        self.assertEqual([l["name"] for l in layers], ["Mechanical 1"])

    def test_pipe_record(self):
        rec = parse_pipe_record("A=1|B=x=y|Record=GerberView")
        self.assertEqual(rec, {"A": "1", "B": "x=y", "Record": "GerberView"})

    def test_containers(self):
        names = [c["name"] for c in self.data["containers"]]
        self.assertEqual(names, ["Print To Printer", "Fabrication Files"])
        fab = self.data["containers"][1]
        self.assertEqual(fab["file_name_expression"], "=PCB_PART_NUMBER+'_'+PCB_REV")
        self.assertTrue(fab["add_to_project"])
        self.assertIn("Somewhere", fab["output_path"])

    def test_gerber_summary(self):
        gerber = self.data["outputs"][0]["gerber"]
        plotted = [l["name"] for l in gerber["plotted_layers"]]
        self.assertEqual(plotted, ["Top Layer", "Bottom Layer", "Top Overlay", "Mechanical 1", "Top Pad Master"])
        self.assertEqual([l["name"] for l in gerber["add_to_all_plots"]], ["Mechanical 1"])
        self.assertEqual(gerber["units"], "Imperial")
        self.assertTrue(gerber["plot_board_profile"])
        self.assertFalse(gerber["drill_drawing_plot_all_used_pairs"])

    def test_connections_and_settings(self):
        outputs = {o["name"]: o for o in self.data["outputs"]}
        self.assertEqual(outputs["Gerber Files"]["connected_containers"], ["Fabrication Files"])
        self.assertEqual(outputs["ODB++ Files"]["connected_containers"], [])
        self.assertFalse(outputs["ODB++ Files"]["enabled"])
        self.assertEqual(outputs["Test Point Report"]["test_points"]["OriginPosition"], "Relative")

    def test_rejects_non_outjob(self):
        fd, path = tempfile.mkstemp(suffix=".OutJob")
        with os.fdopen(fd, "w") as f:
            f.write("[Something]\nA=1\n")
        try:
            with self.assertRaises(ValueError):
                parse_outjob(path)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
