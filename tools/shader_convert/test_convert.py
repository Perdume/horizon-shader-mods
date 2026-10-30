"""Tests for the shader pack converter. The packs here are written for the tests (nothing from a real pack).

    python -I test_convert.py
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shaderpack_convert as sc  # noqa: E402
from glsl import Env, Unsupported, translate_fragment  # noqa: E402
from preprocess import Macros, preprocess  # noqa: E402

VSH = """#version 120
varying vec2 texcoord;
void main() {
    gl_Position = ftransform();
    texcoord = gl_MultiTexCoord0.xy;
}
"""

COMPOSITE = """#version 120
/* DRAWBUFFERS:0 */
#include "/lib/util.glsl"
varying vec2 texcoord;
uniform sampler2D colortex0;
uniform sampler2D depthtex0;
uniform float viewWidth;
uniform float viewHeight;
uniform float frameTimeCounter;
uniform float rainStrength;
uniform int isEyeInWater;
uniform vec3 skyColor;

#define VIGNETTE 0.4
#define BLUR_SAMPLES 3
//#define GRAYSCALE
const float GAMMA = 2.2;

vec3 tonemap(vec3 c) {
    c = c / (1.0 + c);
    return pow(c, vec3(1.0 / GAMMA));
}

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

void main() {
    vec2 res = vec2(viewWidth, viewHeight);
    vec3 color = texture2D(colortex0, texcoord).rgb;
    float depth = texture2D(depthtex0, texcoord).r;
    for (int i = 0; i < BLUR_SAMPLES; i++) {
        color += texture2D(colortex0, texcoord + vec2(float(i) + 1.0, 0.0) / res).rgb * 0.05;
    }
    vec2 p = texcoord - 0.5;
    color *= 1.0 - dot(p, p) * VIGNETTE;
    color.rgb = mix(vec3(luma(color)), color, 1.1);
    color.g *= 0.98;
    if (depth >= 1.0) {
        color = mix(color, skyColor, 0.1);
    } else if (isEyeInWater == 1) {
        color *= vec3(0.8, 1.0, 1.05);
    }
    float n = hash(texcoord * res + frameTimeCounter);
    color += (n - 0.5) / 255.0;
    color = clamp(color, 0.0, 1.0);
    float m = mod(frameTimeCounter, 2);
    color *= 1.0 - rainStrength * 0.1 * (m > 1.0 ? 1.0 : 0.5);
#ifdef GRAYSCALE
    color = vec3(luma(color));
#endif
    gl_FragData[0] = vec4(tonemap(color), 1.0);
}
"""

NEEDS_GBUFFER = """#version 120
varying vec2 texcoord;
uniform sampler2D colortex0;
uniform sampler2D colortex1;
uniform mat4 gbufferProjectionInverse;
void main() {
    vec3 n = texture2D(colortex1, texcoord).rgb;
    vec4 view = gbufferProjectionInverse * vec4(texcoord, 0.5, 1.0);
    gl_FragData[0] = vec4(texture2D(colortex0, texcoord).rgb * n * view.xyz, 1.0);
}
"""

FINAL = """#version 120
varying vec2 texcoord;
uniform sampler2D colortex0;
void main() {
    gl_FragColor = texture2D(colortex0, texcoord);
}
"""

TERRAIN = """#version 120
varying vec2 texcoord;
uniform sampler2D texture;
void main() { gl_FragData[0] = texture2D(texture, texcoord); }
"""


def make_pack(root: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = os.path.join(root, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return root


PACK = {
    "shaders/lib/util.glsl": "float luma(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }\n",
    "shaders/composite.fsh": COMPOSITE, "shaders/composite.vsh": VSH,
    "shaders/composite1.fsh": NEEDS_GBUFFER, "shaders/composite1.vsh": VSH,
    "shaders/final.fsh": FINAL, "shaders/final.vsh": VSH,
    "shaders/gbuffers_terrain.fsh": TERRAIN, "shaders/gbuffers_terrain.vsh": VSH,
}


def env() -> Env:
    return Env(
        uniforms=sc.UNIFORMS,
        samplers={**{s: "color" for s in sc.COLOR_SAMPLERS}, **{s: "depth" for s in sc.DEPTH_SAMPLERS}},
        varyings={"texcoord": ("input.uv", "vec2")},
    )


def translate(body: str) -> str:
    return translate_fragment(
        "varying vec2 texcoord;\nuniform sampler2D colortex0;\nuniform float viewWidth;\n" + body, env()
    )["wgsl"]


class PreprocessTests(unittest.TestCase):
    def run_pp(self, files, entry="a.glsl"):
        return preprocess(entry, lambda p: files[p], Macros())

    def test_conditionals_and_macros(self):
        r = self.run_pp({"a.glsl": "#define A 2\n#define SQ(x) ((x)*(x))\n#if A > 1 && defined(A)\nint v = SQ(A + 1);\n"
                                   "#elif 1\nint w;\n#else\nint z;\n#endif\n#ifdef NOPE\nint q;\n#endif\n"})
        self.assertIn("int v = ((2 + 1)*(2 + 1));", r.text)
        self.assertNotIn("int w", r.text)
        self.assertNotIn("int q", r.text)

    def test_include_and_markers(self):
        r = self.run_pp({"a.glsl": "/* DRAWBUFFERS:01 */\n#include \"/lib/x.glsl\"\nfloat b = X;\n",
                         "shaders/lib/x.glsl": "#define X 3\n"})
        self.assertIn("float b = 3;", r.text)
        self.assertEqual(r.draw_buffers, [0, 1])

    def test_comments_are_removed(self):
        r = self.run_pp({"a.glsl": "float a; // #define A 1\n/* #define B 1 */ float b;\n"})
        self.assertNotIn("#define", r.text)


class TranslateTests(unittest.TestCase):
    def test_a_composite_becomes_a_post_stage(self):
        pack_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pack_dir, True)
        pack = sc.Pack(make_pack(pack_dir, PACK))
        reports = {r.name: r for r in sc.analyse(pack, "world0", convert_ok=True)}
        wgsl = reports["composite"].wgsl
        self.assertEqual(reports["composite"].status, "converted", reports["composite"].reasons)
        self.assertIn("fn post(input: PostInput) -> vec4<f32>", wgsl)
        self.assertIn("textureSampleLevel(previous, linear_sampler, input.uv, 0.0)", wgsl)
        self.assertIn("hs_depth_tex(input.uv)", wgsl)
        self.assertIn("return hs_out;", wgsl)
        self.assertNotIn("GRAYSCALE", wgsl)  # the #ifdef was off
        self.assertIn("0.4", wgsl)  # VIGNETTE expanded

    def test_what_horizon_cannot_run_is_reported_with_reasons(self):
        pack_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pack_dir, True)
        reports = {r.name: r for r in sc.analyse(sc.Pack(make_pack(pack_dir, PACK)), "world0", True)}
        self.assertEqual(reports["composite1"].status, "unsupported")
        self.assertTrue(any("colortex1" in r for r in reports["composite1"].reasons))
        self.assertTrue(any("gbufferProjectionInverse" in r for r in reports["composite1"].reasons))
        self.assertEqual(reports["gbuffers_terrain"].role, "geometry")
        self.assertEqual(reports["gbuffers_terrain"].status, "unsupported")
        self.assertEqual(reports["final"].status, "converted", reports["final"].reasons)
        self.assertIn("hs_out = textureSampleLevel", reports["final"].wgsl)

    def test_types_and_swizzles(self):
        w = translate("""
void main() {
    float a = 1;
    vec4 c = texture2D(colortex0, texcoord);
    c.rgb = c.bgr * 2;
    vec3 d = vec3(a, 0, 1);
    d = clamp(d, 0.0, 1.0);
    d = max(d, 0.5);
    float s = step(0.5, d.x) + smoothstep(0.0, 1.0, d.y) + atan(d.z, d.x);
    int k = int(s) % 3;
    gl_FragData[0] = vec4(d * float(k), c.a);
}""")
        self.assertIn("var a: f32 = 1.0;", w)
        self.assertIn("vec3<f32>(a, 0.0, 1.0)", w)
        self.assertIn("clamp(d, vec3<f32>(0.0), vec3<f32>(1.0))", w)
        self.assertIn("max(d, vec3<f32>(0.5))", w)
        self.assertIn("atan2(", w)
        self.assertIn("let hs_t0 =", w)  # c.rgb = ... assigns one component at a time
        self.assertIn("c.x = hs_t0.x;", w)

    def test_a_parameter_that_is_assigned_gets_a_copy(self):
        w = translate("""
float f(float x) { x = x * 2.0; return x; }
void main() { gl_FragData[0] = vec4(f(texcoord.x)); }""")
        self.assertIn("fn f(x_in: f32) -> f32", w)
        self.assertIn("var x: f32 = x_in;", w)

    def test_names_that_clash_with_wgsl_are_renamed(self):
        w = translate("""
void main() {
    vec3 filter = vec3(1.0);
    float step = 2.0;
    gl_FragData[0] = vec4(filter * step, 1.0);
}""")
        self.assertIn("var u_filter", w)
        self.assertIn("var u_step", w)

    def test_unsupported_things_say_why(self):
        for body, why in [
            ("uniform mat4 gbufferProjectionInverse;\nvoid main() { gl_FragData[0] = vec4(gbufferProjectionInverse[0].x); }",
             "gbufferProjectionInverse"),
            ("struct S { float a; };\nvoid main() { gl_FragData[0] = vec4(1.0); }", "struct"),
            ("void main() { float a[2]; gl_FragData[0] = vec4(1.0); }", "arrays"),
            ("void f(out float x) { x = 1.0; }\nvoid main() { gl_FragData[0] = vec4(1.0); }", "out/inout"),
            ("void main() { discard; }", "discard"),
            ("void main() { mat3 m = inverse(mat3(1.0)); gl_FragData[0] = vec4(1.0); }", "inverse"),
        ]:
            with self.subTest(why=why):
                with self.assertRaises(Unsupported) as ctx:
                    translate(body)
                self.assertIn(why, str(ctx.exception))


@unittest.skipUnless(sc.find_checker(), "horizon-shader is not installed")
class HorizonCheckTests(unittest.TestCase):
    def test_converted_pack_passes_horizons_own_check(self):
        pack_dir, out = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pack_dir, True)
        self.addCleanup(shutil.rmtree, out, True)
        pack = sc.Pack(make_pack(pack_dir, PACK))
        reports = sc.analyse(pack, "world0", convert_ok=True)
        result = sc.write_pack(reports, out, pack_dir, sc.find_checker())
        self.assertTrue(result["written"], result)
        self.assertEqual(result["passes"], 2)  # composite and final
        import subprocess
        proc = subprocess.run([sc.find_checker(), "check", out], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
