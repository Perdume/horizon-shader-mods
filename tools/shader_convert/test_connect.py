"""Tests for hz_connect. The packs are written for the tests.

    python -I test_connect.py
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hz_connect as hc  # noqa: E402
from shaderpack_convert import Pack  # noqa: E402

PROGRAM = """#version 330 compatibility
uniform sampler2D colortex0;
uniform float cloudHeight;
uniform int bedrockLevel;
uniform vec3 cameraPositionFract;
uniform vec3 cameraPosition;
uniform float rainStrength;
uniform float madeUpByThePack;
uniform bool isRightHanded;
// uniform float commentedOut;
void main() { gl_FragData[0] = vec4(cloudHeight + float(bedrockLevel) + madeUpByThePack); }
"""


# The shape of a common space-conversion file: unprojection with the diagonal of the inverse projection.
SPACE_CONVERSION = """#define diagonal3(m) vec3((m)[0].x, (m)[1].y, m[2].z)
#define projMAD(m, v) (diagonal3(m) * (v) + (m)[3].xyz)

vec3 ToNDC(vec3 pos) {
	vec4 iProjDiag = vec4(gbufferProjectionInverse[0].x,
						  gbufferProjectionInverse[1].y,
						  gbufferProjectionInverse[2].zw);
    vec3 p3 = pos * 2.0 - 1.0;
    vec4 viewPos = iProjDiag * p3.xyzz + gbufferProjectionInverse[3];
    return viewPos.xyz / viewPos.w;
}

vec3 ToWorld(vec3 pos) {
	return mat3(gbufferModelViewInverse) * pos + gbufferModelViewInverse[3].xyz;
}

vec3 ToShadow(vec3 pos) {
	vec3 shadowpos = mat3(shadowModelView) * pos + shadowModelView[3].xyz;
	return projMAD(shadowProjection, shadowpos);
}
"""


def make_zip(tmp: str, files: dict[str, str], nested: str = "") -> str:
    path = os.path.join(tmp, "pack.zip")
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(nested + name, text)
    return path


class ConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_missing_uniforms_are_defined_in_a_copy(self):
        src = make_zip(self.tmp, {"shaders/composite.fsh": PROGRAM, "shaders/shaders.properties": "sun=true\n"})
        out = os.path.join(self.tmp, "out.zip")
        result = hc.connect(src, out)
        self.assertTrue(result["written"])
        self.assertEqual(result["add"], ["bedrockLevel", "cameraPositionFract", "cloudHeight", "isRightHanded"])
        props = zipfile.ZipFile(out).read("shaders/shaders.properties").decode()
        self.assertIn("sun=true", props)
        self.assertIn("uniform.int.bedrockLevel=-64", props)
        self.assertIn("uniform.float.cloudHeight=192.33", props)
        self.assertIn("uniform.vec3.cameraPositionFract=vec3(frac(cameraPosition.x)", props)
        self.assertIn("uniform.bool.isRightHanded=1", props)
        self.assertNotIn("endFlash", props)  # not declared by any program
        # The original is untouched and every other file is copied as it was.
        self.assertNotIn("uniform.", zipfile.ZipFile(src).read("shaders/shaders.properties").decode())
        self.assertEqual(zipfile.ZipFile(out).read("shaders/composite.fsh").decode(), PROGRAM)

    def test_camera_and_look_uniforms_are_defined_when_a_program_declares_them(self):
        program = ("uniform ivec3 cameraPositionInt;\nuniform vec3 previousCameraPositionFract;\n"
                   "uniform ivec3 previousCameraPositionInt;\nuniform vec3 eyePosition;\n"
                   "uniform vec3 playerLookVector;\nvoid main() {}\n")
        src = make_zip(self.tmp, {"shaders/composite.fsh": program, "shaders/shaders.properties": "a=b\n"})
        out = os.path.join(self.tmp, "out.zip")
        result = hc.connect(src, out)
        self.assertEqual(result["add"], ["cameraPositionInt", "eyePosition", "playerLookVector",
                                         "previousCameraPositionFract", "previousCameraPositionInt"])
        props = zipfile.ZipFile(out).read("shaders/shaders.properties").decode()
        self.assertIn("uniform.vec3.cameraPositionInt=vec3(floor(cameraPosition.x), floor(cameraPosition.y), "
                      "floor(cameraPosition.z))", props)
        self.assertIn("uniform.vec3.playerLookVector=vec3(-gbufferModelViewInverse.0.2, "
                      "-gbufferModelViewInverse.1.2, -gbufferModelViewInverse.2.2)", props)
        self.assertIn("uniform.vec3.eyePosition=vec3(cameraPosition.x, cameraPosition.y, cameraPosition.z)", props)

    def test_what_horizon_already_provides_or_the_pack_defines_is_left_alone(self):
        src = make_zip(self.tmp, {
            "shaders/composite.fsh": PROGRAM,
            "shaders/shaders.properties": "uniform.float.cloudHeight=100\n",
        })
        plan = hc.plan(Pack(src))
        self.assertNotIn("cloudHeight", plan["add"])  # the pack's own definition wins
        self.assertNotIn("cameraPosition", plan["add"])  # Horizon provides it
        self.assertNotIn("rainStrength", plan["add"])
        self.assertEqual(plan["left"], ["madeUpByThePack"])  # nothing provides it: still zero
        self.assertNotIn("commentedOut", plan["declared"])

    def test_no_properties_file_gets_one(self):
        src = make_zip(self.tmp, {"shaders/composite.fsh": PROGRAM})
        out = os.path.join(self.tmp, "out.zip")
        hc.connect(src, out)
        self.assertIn("uniform.float.cloudHeight=192.33", zipfile.ZipFile(out).read("shaders/shaders.properties").decode())

    def test_a_pack_nested_in_a_folder_keeps_its_layout(self):
        src = make_zip(self.tmp, {"shaders/composite.fsh": PROGRAM, "shaders/shaders.properties": "a=b\n"}, "BSL/")
        out = os.path.join(self.tmp, "out.zip")
        hc.connect(src, out)
        names = zipfile.ZipFile(out).namelist()
        self.assertIn("BSL/shaders/shaders.properties", names)
        self.assertEqual(len(names), 2)

    def test_connecting_twice_adds_nothing_new(self):
        src = make_zip(self.tmp, {"shaders/composite.fsh": PROGRAM, "shaders/shaders.properties": "a=b\n"})
        once = os.path.join(self.tmp, "once.zip")
        hc.connect(src, once)
        again = hc.plan(Pack(once))
        self.assertEqual(again["add"], [])
        self.assertTrue(again["already"])

    def test_a_diagonal_only_unprojection_uses_the_whole_matrix(self):
        src = make_zip(self.tmp, {"shaders/lib/util/spaceConversion.glsl": SPACE_CONVERSION})
        out = os.path.join(self.tmp, "out.zip")
        result = hc.connect(src, out)
        self.assertTrue(result["written"])
        self.assertEqual(result["add"], [])
        self.assertEqual({k: v[1] for k, v in result["unprojection"].items()},
                         {"shaders/lib/util/spaceConversion.glsl": ["ToNDC"]})
        text = zipfile.ZipFile(out).read("shaders/lib/util/spaceConversion.glsl").decode()
        self.assertIn("gbufferProjectionInverse * vec4(pos * 2.0 - 1.0, 1.0)", text)
        self.assertNotIn("iProjDiag", text)
        self.assertNotIn("gbufferProjectionInverse[0]", text)
        # What comes before and after it is as it was, including the diagonal use that is right (ortho).
        self.assertTrue(text.startswith(SPACE_CONVERSION.split("vec3 ToNDC")[0]))
        self.assertIn("vec3 ToWorld(vec3 pos) {\n\treturn mat3(gbufferModelViewInverse) * pos", text)
        self.assertIn("projMAD(shadowProjection, shadowpos)", text)
        # The original pack is untouched.
        self.assertIn("iProjDiag", zipfile.ZipFile(src).read("shaders/lib/util/spaceConversion.glsl").decode())

    def test_only_the_diagonal_shortcut_shape_is_rewritten(self):
        other = ("uniform mat4 gbufferProjectionInverse;\n"
                 "vec3 Full(vec3 pos) { vec4 v = gbufferProjectionInverse * vec4(pos * 2.0 - 1.0, 1.0); return v.xyz / v.w; }\n"
                 "vec3 NotScreen(vec3 p) { vec4 v = vec4(gbufferProjectionInverse[0].x * p.x, 0.0, 0.0, 1.0); return v.xyz / v.w; }\n"
                 "float Fov() { return gbufferProjectionInverse[0].x; }\n")
        text, names = hc.fix_unprojection(other)
        self.assertEqual(names, [])
        self.assertEqual(text, other)
        # A second run over a rewritten pack finds nothing left to do.
        once, _ = hc.fix_unprojection(SPACE_CONVERSION)
        self.assertEqual(hc.fix_unprojection(once)[1], [])

    def test_the_rewrite_keeps_the_argument_name(self):
        text, names = hc.fix_unprojection(
            "vec3 Unproject(vec3 screen) {\n  vec4 d = vec4(gbufferProjectionInverse[0].x, gbufferProjectionInverse[1].y, "
            "gbufferProjectionInverse[2].zw);\n  vec3 q = screen * 2.0 - 1.0;\n  vec4 v = d * q.xyzz + gbufferProjectionInverse[3];\n"
            "  return v.xyz / v.w;\n}\n")
        self.assertEqual(names, ["Unproject"])
        self.assertIn("vec4(screen * 2.0 - 1.0, 1.0)", text)

    def test_a_folder_pack_works_too(self):
        folder = os.path.join(self.tmp, "folder")
        os.makedirs(os.path.join(folder, "shaders"))
        with open(os.path.join(folder, "shaders", "composite.fsh"), "w") as f:
            f.write(PROGRAM)
        out = os.path.join(self.tmp, "out.zip")
        hc.connect(folder, out)
        self.assertIn("shaders/shaders.properties", zipfile.ZipFile(out).namelist())


if __name__ == "__main__":
    unittest.main(verbosity=2)
