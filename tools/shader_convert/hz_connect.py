#!/usr/bin/env python3
"""Connects an Iris/OptiFine shader pack to Horizon's own pack loader.

Horizon's loader reads packs in the `shaders/` format itself, but it provides a fixed list of uniforms; any
other uniform a program declares is zero ("zero for now" in the log). Some of the missing ones are Iris
additions (documented at https://shaders.properties/current/reference/uniforms/). A pack can define uniforms of
its own in shaders.properties, and the loader binds those to every program, so this writes a COPY of the pack
with the missing ones defined there. The original is not changed and the game is not rebuilt.

    python -I hz_connect.py BSL_v10.1.8.zip -o BSL_v10.1.8_horizon.zip
    python -I hz_connect.py BSL_v10.1.8.zip --list          # what would be added, nothing written

It also fixes one thing in the pack's code. Horizon multiplies view bobbing into gbufferProjection (Iris and
OptiFine put it in gbufferModelView). A pack that rebuilds a position from depth with only the diagonal of
gbufferProjectionInverse then ignores the bob's rotation, and everything it lights from that position (shadows,
fog, ambient occlusion) sways with each step, more the farther away it is. A function of the form "screen
position -> view position" that does so is rewritten to multiply the whole matrix, which is the same thing
when the projection is a plain perspective one.

The values come from Iris's published uniform documentation and Horizon's own code (clouds are drawn at 192.33);
no Iris source was read. The copy is the original pack plus a few lines, so the original's licence applies.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shaderpack_convert import Pack  # noqa: E402

# The uniforms Horizon's loader provides (crates/horizon-client-render/src/gfx/pack_uniforms.rs, NAMES).
HORIZON_PROVIDES = """
cameraPosition previousCameraPosition eyeAltitude upPosition sunPosition moonPosition shadowLightPosition sunAngle
shadowAngle worldTime worldDay moonPhase frameCounter frameTime frameTimeCounter viewWidth viewHeight aspectRatio
near far rainStrength wetness thunderStrength fogColor skyColor fogMode fogStart fogEnd fogDensity fogShape
isEyeInWater blindness nightVision darknessFactor darknessLightFactor screenBrightness hideGUI eyeBrightness
eyeBrightnessSmooth heldItemId heldItemId2 heldBlockLightValue heldBlockLightValue2 renderStage biome
biome_category biome_precipitation temperature rainfall gbufferModelView gbufferModelViewInverse gbufferProjection
gbufferProjectionInverse gbufferPreviousModelView gbufferPreviousProjection atlasSize shadowModelView
shadowModelViewInverse shadowProjection shadowProjectionInverse
""".split()

# Uniforms Iris documents that Horizon does not provide yet: (type, expression, where the value comes from).
# The expression language is shaders.properties': frac(), floor(), vec3(), cameraPosition.x ...; a bool is 1 or 0.
DEFINITIONS: dict[str, tuple[str, str, str]] = {
    "bedrockLevel": ("int", "-64",
                     "y of the bedrock floor; -64 is the Overworld's (other dimensions differ)"),
    "cloudHeight": ("float", "192.33",
                    "y of vanilla clouds; Horizon draws them at 192.33 (client-render clouds.rs)"),
    "cameraPositionFract": ("vec3",
                            "vec3(frac(cameraPosition.x), frac(cameraPosition.y), frac(cameraPosition.z))",
                            "fractional part (0 to 1) of the camera position"),
    "cameraPositionInt": ("vec3",
                          "vec3(floor(cameraPosition.x), floor(cameraPosition.y), floor(cameraPosition.z))",
                          "whole part of the camera position (an ivec3 in the programs: a custom vec3 is written as "
                          "the declared type); floor, so Int + Fract is the position"),
    "previousCameraPositionFract": ("vec3",
                                    "vec3(frac(previousCameraPosition.x), frac(previousCameraPosition.y), "
                                    "frac(previousCameraPosition.z))",
                                    "cameraPositionFract of the previous frame"),
    "previousCameraPositionInt": ("vec3",
                                  "vec3(floor(previousCameraPosition.x), floor(previousCameraPosition.y), "
                                  "floor(previousCameraPosition.z))",
                                  "cameraPositionInt of the previous frame"),
    "eyePosition": ("vec3", "vec3(cameraPosition.x, cameraPosition.y, cameraPosition.z)",
                    "the head model's position: the camera's in first person (third person is not tracked)"),
    "playerLookVector": ("vec3",
                         "vec3(-gbufferModelViewInverse.0.2, -gbufferModelViewInverse.1.2, "
                         "-gbufferModelViewInverse.2.2)",
                         "the world direction the view looks: minus the inverse view's column 2 (row.column)"),
    "isRightHanded": ("bool", "1", "true while the main hand is the right one (the default)"),
    "relativeEyePosition": ("vec3", "vec3(0, 0, 0)",
                            "cameraPosition - eyePosition: zero in first person (third person is not tracked)"),
    "endFlashIntensity": ("float", "0", "the End's flash intensity: 0 when none is happening"),
    "endFlashPosition": ("vec3", "vec3(0, 0, 0)", "the End flash's view-space position: unused while there is none"),
}

SOURCE_SUFFIXES = (".fsh", ".vsh", ".gsh", ".csh", ".glsl", ".inc", ".h")
_UNIFORM = re.compile(r"\buniform\s+(?:(?:highp|mediump|lowp)\s+)?(\w+)\s+([^;{}()]+);")
MARK = "# --- added by hz_connect.py"


def declared_uniforms(pack: Pack) -> set[str]:
    """Names of uniforms any program or include of the pack declares (over-inclusive: it ignores #if)."""
    found: set[str] = set()
    for name in pack.names():
        if not name.endswith(SOURCE_SUFFIXES):
            continue
        text = re.sub(r"//[^\n]*|/\*.*?\*/", "", pack.read(name), flags=re.S)
        for _ty, names in _UNIFORM.findall(text):
            for n in names.split(","):
                found.add(re.sub(r"\[.*", "", n.split("=")[0]).strip())
    return found


def pack_defined(properties: str) -> set[str]:
    """Names the pack defines itself as `uniform.<type>.<name>` or `variable.<type>.<name>`."""
    return {m.group(1) for m in re.finditer(r"(?m)^\s*(?:uniform|variable)\.\w+\.(\w+)\s*[=:]", properties)}


_DIAGONAL = re.compile(r"gbufferProjectionInverse\s*\[\s*0\s*\]\s*\.\s*x")
_TO_NDC = re.compile(r"\*\s*2(?:\.0*)?\s*-\s*1(?:\.0*)?")
_DIVIDES_BY_W = re.compile(r"\.xyz\s*/\s*\w+\s*\.\s*w")
_FUNCTION = re.compile(r"\bvec3\s+(\w+)\s*\(\s*vec3\s+(\w+)\s*\)\s*\{")
BOB_NOTE = "// hz_connect.py: the whole inverse projection (Horizon puts view bobbing in the projection)"


def _closing_brace(text: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def fix_unprojection(text: str) -> tuple[str, list[str]]:
    """`text` with each screen-to-view function that uses only the diagonal of gbufferProjectionInverse
    rewritten to multiply the whole matrix, and the names of those functions.

    A function counts when it takes one vec3, uses gbufferProjectionInverse[0].x, turns its argument
    into NDC with `* 2.0 - 1.0` and returns `.xyz / .w`; any other shape is left as it is."""
    out: list[str] = []
    fixed: list[str] = []
    at = 0
    for m in _FUNCTION.finditer(text):
        if m.start() < at:
            continue
        close = _closing_brace(text, m.end() - 1)
        if close < 0:
            continue
        body = re.sub(r"//[^\n]*|/\*.*?\*/", "", text[m.end():close], flags=re.S)
        if not (_DIAGONAL.search(body) and _TO_NDC.search(body) and _DIVIDES_BY_W.search(body)):
            continue
        out.append(text[at:m.end()])
        out.append(f"\n    {BOB_NOTE}\n"
                   f"    vec4 viewPos = gbufferProjectionInverse * vec4({m.group(2)} * 2.0 - 1.0, 1.0);\n"
                   f"    return viewPos.xyz / viewPos.w;\n")
        at = close
        fixed.append(m.group(1))
    out.append(text[at:])
    return "".join(out), fixed


def plan(pack: Pack) -> dict:
    declared = declared_uniforms(pack)
    path = "shaders/shaders.properties"
    properties = pack.read(path) if pack.has(path) else ""
    own = pack_defined(properties)
    provided = set(HORIZON_PROVIDES)
    add = sorted(n for n in DEFINITIONS if n in declared and n not in provided and n not in own)
    left = sorted(n for n in declared if n not in provided and n not in own and n not in DEFINITIONS
                  and not n.startswith("hz_") and not _is_sampler_name(n))
    unprojection: dict[str, tuple[str, list[str]]] = {}
    for name in pack.names():
        if name.endswith(SOURCE_SUFFIXES):
            text = pack.read(name)
            if "gbufferProjectionInverse" in text:
                fixed_text, names = fix_unprojection(text)
                if names:
                    unprojection[name] = (fixed_text, names)
    return {"declared": declared, "add": add, "left": left, "already": MARK in properties,
            "properties": properties, "path": path, "unprojection": unprojection}


def _is_sampler_name(name: str) -> bool:
    return bool(re.fullmatch(r"(colortex|depthtex|shadowtex|shadowcolor|gaux|noisetex|texture|lightmap|normals|"
                             r"specular|watershadow|gcolor|gdepth|gnormal|composite|gdepthtex|tex|dhDepthTex)\d*", name))


def block(names: list[str]) -> str:
    lines = ["", f"{MARK}: uniforms Horizon's loader does not provide yet (Iris's uniform documentation) ---"]
    for n in names:
        ty, expr, why = DEFINITIONS[n]
        lines.append(f"# {n}: {why}")
        lines.append(f"uniform.{ty}.{n}={expr}")
    return "\n".join(lines) + "\n"


def connect(source: str, out: str) -> dict:
    pack = Pack(source)
    p = plan(pack)
    if not p["add"] and not p["unprojection"]:
        return {**p, "written": False}
    changed: dict[str, bytes] = {}
    if p["add"]:
        patched = p["properties"]
        if patched and not patched.endswith("\n"):
            patched += "\n"
        patched += block(p["add"])
        target = entry_name(pack, p["path"]) if pack.has(p["path"]) else prefix_of(pack) + p["path"]
        changed[target] = patched.encode("utf-8")
    for rel, (text, _names) in p["unprojection"].items():
        changed[entry_name(pack, rel)] = text.encode("utf-8")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        seen = set()
        for rel in pack.names():
            name = entry_name(pack, rel)
            seen.add(name)
            dst.writestr(name, changed[name] if name in changed else raw(pack, rel))
        for name, data in changed.items():
            if name not in seen:
                dst.writestr(name, data)
    return {**p, "written": True, "out": out}


def entry_name(pack: Pack, rel: str) -> str:
    """The name a file has in a zip: its original entry name (a zip may nest `shaders/` in a folder)."""
    return pack.files[rel] if pack._zip else rel


def prefix_of(pack: Pack) -> str:
    """The folder a zip nests `shaders/` in, or ''."""
    if pack._zip:
        for rel, ref in pack.files.items():
            if ref.endswith(rel):
                return ref[: len(ref) - len(rel)]
    return ""


def raw(pack: Pack, rel: str) -> bytes:
    """The bytes of one file of the pack, undecoded."""
    ref = pack.files[rel]
    if pack._zip:
        return pack._zip.read(ref)
    with open(ref, "rb") as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="an Iris/OptiFine shader pack zip or folder")
    ap.add_argument("-o", "--out", help="the zip to write (it must not be the original)")
    ap.add_argument("--list", action="store_true", help="only say what would be added")
    ap.add_argument("--structure", action="store_true",
                    help="print the pack's structure and where it meets a gap in Horizon's loader")
    ap.add_argument("--dimension", default="world0", help="dimension folder for --structure (default world0)")
    args = ap.parse_args(argv)

    if args.structure:
        import pack_report
        print(pack_report.render(pack_report.build(Pack(args.pack), args.dimension), args.dimension))
        return 0

    if args.list or not args.out:
        p = plan(Pack(args.pack))
        written = None
    else:
        if os.path.abspath(args.out) == os.path.abspath(args.pack):
            ap.error("the output must not be the original pack")
        p = connect(args.pack, args.out)
        written = p["written"]

    print(f"Pack: {os.path.basename(args.pack)}   uniforms its programs declare: {len(p['declared'])}")
    if p["already"]:
        print("This pack was already connected (it has hz_connect.py's lines).")
    if p["add"]:
        print("\nDefined for Horizon's loader (they were zero):")
        for n in p["add"]:
            ty, expr, why = DEFINITIONS[n]
            print(f"  {ty:<6} {n:<20} = {expr}")
            print(f"  {'':<6} {'':<20}   {why}")
    else:
        print("\nNothing to add: the pack declares none of the uniforms this knows.")
    if p["unprojection"]:
        print("\nRewritten to use the whole inverse projection (Horizon's projection holds the view bobbing):")
        for rel, (_text, names) in p["unprojection"].items():
            print(f"  {rel}: {', '.join(names)}")
    if p["left"]:
        print("\nDeclared by the pack, but neither Horizon nor this provides them (may include uniforms in code that")
        print("is switched off, e.g. Distant Horizons; the game's log says which are really zero):")
        print("  " + ", ".join(p["left"]))
    if written:
        print(f"\nWrote {p['out']}. Pick it in Options > Horizon Settings > Shader Packs.")
    elif written is False:
        print("\nNo copy written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
