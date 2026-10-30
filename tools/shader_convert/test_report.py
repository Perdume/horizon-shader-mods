"""Tests for pack_report (the structure interpreter). The packs are written for the tests.

    python -I test_report.py
"""
import os
import sys
import unittest
import zipfile
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pack_report as pr  # noqa: E402
from shaderpack_convert import Pack  # noqa: E402

TERRAIN_VSH = """#version 120
attribute vec4 mc_Entity;
attribute vec2 mc_midTexCoord;
attribute vec4 at_midBlock;
attribute vec4 at_tangent;
varying vec2 uv;
void main() { uv = gl_MultiTexCoord0.xy; float w = mc_Entity.x + at_midBlock.y + mc_midTexCoord.x; gl_Position = ftransform() + vec4(w); }
"""
SHADOW_VSH = """#version 120
attribute vec4 at_midBlock;
void main() { gl_Position = ftransform(); }
"""
FSH = "#version 120\nvarying vec2 uv;\nvoid main() { gl_FragData[0] = vec4(uv, 0.0, 1.0); }\n"
PROPERTIES = """# a comment
iris.features.required = COMPUTE_SHADERS
image.thing = img rgba rgba8 unsigned_byte false true 1.0 1.0
texture.noise = tex/noise.png
texture.composite.colortex7 = tex/extra.png
program.shadowcomp.enabled = true
"""


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        path = os.path.join(self.tmp, "p.zip")
        files = {
            "shaders/world0/gbuffers_terrain.vsh": TERRAIN_VSH, "shaders/world0/gbuffers_terrain.fsh": FSH,
            "shaders/world0/shadow.vsh": SHADOW_VSH, "shaders/world0/shadow.fsh": FSH,
            "shaders/world0/composite.vsh": FSH, "shaders/world0/composite.fsh": FSH,
            "shaders/world0/shadowcomp.csh": "#version 430\nlayout(local_size_x = 8) in;\nvoid main() {}\n",
            "shaders/world0/begin.fsh": FSH,
            "shaders/world0/gbuffers_spidereyes.vsh": FSH, "shaders/world0/gbuffers_spidereyes.fsh": FSH,
            "shaders/shaders.properties": PROPERTIES, "shaders/tex/noise.png": "x",
        }
        with zipfile.ZipFile(path, "w") as z:
            for name, text in files.items():
                z.writestr(name, text)
        self.report = pr.build(Pack(path), "world0")
        self.rows = {r["name"]: r for r in self.report["programs"]}

    def test_programs_are_classified_by_what_horizon_does_with_them(self):
        self.assertEqual(self.rows["gbuffers_terrain"]["horizon"], "loaded")
        self.assertEqual(self.rows["shadow"]["horizon"], "loaded")
        self.assertEqual(self.rows["composite"]["horizon"], "loaded")
        self.assertEqual(self.rows["shadowcomp"]["horizon"], "not run (compute only)")
        self.assertTrue(self.rows["begin"]["horizon"].startswith("not run"))
        self.assertTrue(self.rows["gbuffers_spidereyes"]["horizon"].startswith("unknown"))

    def test_attributes_are_split_into_used_and_declared_only(self):
        attrs = self.rows["gbuffers_terrain"]["attributes"]
        self.assertEqual(attrs["mc_Entity"], "used")
        self.assertEqual(attrs["at_midBlock"], "used")
        self.assertEqual(self.rows["shadow"]["attributes"]["at_midBlock"], "declared only")
        self.assertEqual(pr.attribute_status("mc_Entity"), "provided")
        self.assertTrue(pr.attribute_status("at_midBlock").startswith("NOT provided"))
        self.assertTrue(pr.attribute_status("at_tangent").startswith("constant"))

    def test_a_declared_but_unused_attribute_is_not_a_gap(self):
        gaps = self.report["gaps"]
        who = gaps["attribute at_midBlock reads zero"]
        self.assertIn("gbuffers_terrain", who)  # used there
        self.assertNotIn("shadow", who)  # only declared there

    def test_features_and_custom_textures(self):
        self.assertEqual(self.report["features"]["image."], 1)
        by_key = {t["key"]: t for t in self.report["textures"]}
        self.assertTrue(by_key["texture.noise"]["handling"].startswith("IGNORED"))
        self.assertTrue(by_key["texture.noise"]["in_pack"])
        self.assertTrue(by_key["texture.composite.colortex7"]["handling"].startswith("NOT LOADED"))
        self.assertFalse(by_key["texture.composite.colortex7"]["in_pack"])
        text = pr.render(self.report, "world0")
        self.assertIn("Where this pack meets a gap", text)
        self.assertIn("custom texture IGNORED", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
