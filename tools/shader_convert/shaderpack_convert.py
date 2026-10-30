#!/usr/bin/env python3
"""Analyzes an OptiFine/Iris shader pack (a zip or a folder) for Horizon and converts what Horizon's own
shader format (API 0.1) can run.

    python -I shaderpack_convert.py analyze BSL_v10.1.8.zip [--json]
    python -I shaderpack_convert.py convert BSL_v10.1.8.zip -o out_dir

Horizon's format has three stages: a sky function, a material function and up to eight full-screen post
passes that see the picture and the depth. It has no shadow pass and no G-buffers, so only the composite and
final programs that read the picture and the depth can be converted; the report says what the rest needs.
The pack is read in place (a zip is never extracted) and treated as data.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from glsl import Env, GlslSyntaxError, Unsupported, translate_fragment  # noqa: E402
from preprocess import Macros, PreprocessError, Result, option_defines, preprocess  # noqa: E402

# ----------------------------------------------------------------------------------------- Horizon side

# Uniforms with a Horizon equivalent: (WGSL expression, GLSL type).
UNIFORMS: dict[str, tuple[str, str]] = {
    "viewWidth": ("frame.resolution.x", "float"),
    "viewHeight": ("frame.resolution.y", "float"),
    "aspectRatio": ("(frame.resolution.x / frame.resolution.y)", "float"),
    "frameTimeCounter": ("frame.time", "float"),
    "frameCounter": ("i32(frame.frame_index)", "int"),
    "rainStrength": ("frame.rain", "float"),
    "thunderStrength": ("frame.thunder", "float"),
    "cameraPosition": ("frame.camera_position", "vec3"),
    "near": ("0.05", "float"),
    "far": ("frame.far", "float"),
    "sunPosition": ("(frame.view * vec4<f32>(frame.sun_direction * 100.0, 0.0)).xyz", "vec3"),
    "moonPosition": ("(frame.view * vec4<f32>(frame.moon_direction * 100.0, 0.0)).xyz", "vec3"),
    "upPosition": ("(frame.view * vec4<f32>(0.0, 100.0, 0.0, 0.0)).xyz", "vec3"),
    "skyColor": ("frame.sky_color", "vec3"),
    "fogColor": ("frame.fog_color", "vec3"),
    "isEyeInWater": ("i32(frame.eye_in)", "int"),
    "nightVision": ("frame.night_vision", "float"),
    "blindness": ("frame.blindness", "float"),
    "darknessFactor": ("frame.darkness", "float"),
    "eyeBrightness": ("vec2<i32>(frame.eye_light * 240.0)", "ivec2"),
    "eyeBrightnessSmooth": ("vec2<i32>(frame.eye_light * 240.0)", "ivec2"),
    "worldTime": ("i32(frame.game_time * 24000.0)", "int"),
    "gbufferModelView": ("frame.view", "mat4"),
    "gbufferModelViewInverse": ("frame.view_inverse", "mat4"),
}
# Why a well-known uniform has none (shown in the report instead of a bare "unsupported").
NO_EQUIVALENT = {
    "gbufferProjection": "projection matrix: Horizon's depth is reversed, so positions rebuilt from it differ",
    "gbufferProjectionInverse": "projection matrix: Horizon's depth is reversed, so positions rebuilt from it differ",
    "gbufferPreviousProjection": "previous-frame matrices (temporal effects)",
    "gbufferPreviousModelView": "previous-frame matrices (temporal effects)",
    "previousCameraPosition": "previous-frame data (temporal effects)",
    "shadowModelView": "the shadow pass",
    "shadowProjection": "the shadow pass",
    "shadowModelViewInverse": "the shadow pass",
    "shadowProjectionInverse": "the shadow pass",
    "shadowLightPosition": "the shadow pass",
    "frameTime": "frame delta time",
    "wetness": "wetness",
    "moonPhase": "moon phase",
    "worldDay": "world day",
    "entityId": "per-entity data",
    "blockEntityId": "per-block data",
    "heldItemId": "held item",
    "screenBrightness": "the Brightness option",
}
# Samplers a post pass can read: the picture (the previous pass's output) and the depth.
COLOR_SAMPLERS = {"colortex0", "gcolor"}
DEPTH_SAMPLERS = {"depthtex0", "depthtex1", "depthtex2", "gdepthtex"}
BUFFER_NAMES = {
    "gdepth": "colortex1", "gnormal": "colortex2", "composite": "colortex3",
    "gaux1": "colortex4", "gaux2": "colortex5", "gaux3": "colortex6", "gaux4": "colortex7",
}
POST_ORDER = re.compile(r"^(composite(\d*)|final)$")
MAX_POST = 8


@dataclass
class Program:
    name: str
    fsh: str | None = None
    vsh: str | None = None
    gsh: str | None = None
    csh: str | None = None


@dataclass
class Report:
    name: str
    role: str  # post, sky, deferred, geometry, shadow, prepass, compute, other
    status: str = "unsupported"  # converted, convertible, unsupported
    reasons: list[str] = field(default_factory=list)
    samplers: list[str] = field(default_factory=list)
    uniforms: list[str] = field(default_factory=list)
    varyings: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    outputs: list[int] = field(default_factory=list)
    wgsl: str | None = None
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------------------- reading a pack

class Pack:
    """A pack as files under `shaders/`, from a folder or a zip (never extracted)."""

    def __init__(self, path: str):
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._disk: dict[str, str] = {}
        if os.path.isdir(path):
            raw = {}
            for base, _dirs, files in os.walk(path):
                for f in files:
                    full = os.path.join(base, f)
                    raw[os.path.relpath(full, path).replace(os.sep, "/")] = full
            self._index(raw)
        else:
            self._zip = zipfile.ZipFile(path)
            self._index({i.filename: i.filename for i in self._zip.infolist() if not i.is_dir()})

    def _index(self, raw: dict[str, str]) -> None:
        prefix = ""
        for name in raw:
            if name.startswith("shaders/"):
                break
            at = name.find("/shaders/")
            if at >= 0:
                prefix = name[: at + 1]
                break
        self.files: dict[str, str] = {}
        has_shaders = any(n[len(prefix):].startswith("shaders/") for n in raw)
        for name, ref in raw.items():
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            rel = rel if has_shaders else "shaders/" + rel
            self.files[rel] = ref

    def names(self) -> list[str]:
        return sorted(self.files)

    def read(self, rel: str) -> str:
        ref = self.files[rel]
        if self._zip:
            data = self._zip.read(ref)
        else:
            with open(ref, "rb") as f:
                data = f.read()
        return data.decode("utf-8", errors="replace")

    def has(self, rel: str) -> bool:
        return rel in self.files


def find_programs(pack: Pack, dimension: str) -> dict[str, Program]:
    """The programs the pack has for a dimension folder (`world0`), over its root ones."""
    found: dict[str, Program] = {}
    for scope in ("", dimension):
        for name in pack.names():
            m = re.fullmatch(r"shaders/(?:([\w-]+)/)?(\w+)\.(fsh|vsh|gsh|csh)", name)
            if not m or (m.group(1) or "") != scope:
                continue
            prog = found.setdefault(m.group(2), Program(m.group(2)))
            setattr(prog, m.group(3), name)
    return found


def role_of(name: str) -> str:
    if POST_ORDER.match(name):
        return "post"
    if name == "gbuffers_skybasic":
        return "sky"
    if name.startswith("deferred"):
        return "deferred"
    if name.startswith("gbuffers_"):
        return "geometry"
    if name.startswith("shadow"):
        return "shadow"
    if name.startswith(("prepare", "begin", "setup")):
        return "prepass"
    return "other"


ROLE_REASON = {
    "geometry": "draws the world's surfaces into G-buffers: Horizon has no G-buffer stage (its material stage "
                "only returns a lit colour per surface)",
    "shadow": "the shadow pass: Horizon's API 0.1 has no shadow stage yet",
    "deferred": "deferred lighting reads the G-buffers, which Horizon does not have",
    "sky": "the sky is drawn as geometry here; Horizon's sky stage is a function of the view ray (not converted yet)",
    "prepass": "runs before the world is drawn: Horizon has no such stage",
    "other": "not a program Horizon's stages map to",
}


# ----------------------------------------------------------------------------------------- analysing

def _declared(text: str, keyword: str) -> dict[str, str]:
    found: dict[str, str] = {}
    pattern = rf"\b{keyword}\s+(?:(?:flat|smooth|noperspective|centroid|highp|mediump|lowp)\s+)*(\w+)\s+([^;(){{}}]+);"
    for ty, names in re.findall(pattern, text):
        for n in names.split(","):
            found[re.sub(r"\[.*", "", n.split("=")[0]).strip()] = ty
    return found


def _used(text: str, name: str) -> bool:
    return len(re.findall(rf"\b{re.escape(name)}\b", text)) > 1


def static_view(text: str) -> dict:
    uniforms = {n: t for n, t in _declared(text, "uniform").items() if _used(text, n)}
    varyings = {n: t for n, t in {**_declared(text, "varying"), **_declared_in(text)}.items() if _used(text, n)}
    features = []
    if re.search(r"\bstruct\b", text):
        features.append("structs")
    if re.search(r"\b\w+\s+\w+\s*\[\s*\w*\s*\]", text):
        features.append("arrays")
    if re.search(r"\([^)]*\b(?:out|inout)\b[^)]*\)\s*\{", text):
        features.append("out/inout parameters")
    if re.search(r"\bdiscard\b", text):
        features.append("discard")
    return {"uniforms": uniforms, "varyings": varyings, "features": features}


def _declared_in(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for ty, name in re.findall(r"(?m)^\s*(?:flat\s+|smooth\s+)?in\s+(?:highp\s+)?(\w+)\s+(\w+)\s*;", text):
        found[name] = ty
    return found


def uv_varyings(vertex_text: str) -> set[str]:
    """Varyings the vertex shader sets straight from the first texture coordinate."""
    names = set(re.findall(r"\b(\w+)\s*=\s*(?:\(\s*gl_TextureMatrix\[0\]\s*\*\s*)?gl_MultiTexCoord0\b", vertex_text))
    names |= set(re.findall(r"\b(\w+)\s*=\s*gl_TexCoord\[0\]\.(?:xy|st)\b", vertex_text))
    return names


def run_preprocess(pack: Pack, path: str) -> Result:
    return preprocess(path, pack.read, Macros())


def analyse_program(pack: Pack, prog: Program, convert_ok: bool) -> Report:
    role = role_of(prog.name)
    rep = Report(prog.name, role)
    if prog.fsh is None:
        rep.reasons.append("no fragment shader" if not prog.csh else "a compute program")
        rep.role = "compute" if prog.csh else rep.role
        return rep
    try:
        frag = run_preprocess(pack, prog.fsh)
    except (PreprocessError, KeyError, RecursionError) as e:
        rep.reasons.append(f"preprocessing failed: {e}")
        return rep
    view = static_view(frag.text)
    rep.samplers = sorted(n for n, t in view["uniforms"].items() if t.startswith("sampler"))
    rep.uniforms = sorted(n for n, t in view["uniforms"].items() if not t.startswith("sampler"))
    rep.features = view["features"]
    rep.outputs = sorted(set(frag.draw_buffers)) or [0]
    vertex_uv: set[str] = set()
    if prog.vsh:
        try:
            vert = run_preprocess(pack, prog.vsh)
            vertex_uv = uv_varyings(vert.text)
        except (PreprocessError, KeyError, RecursionError) as e:
            rep.notes.append(f"vertex shader not read: {e}")
    rep.varyings = sorted(view["varyings"])

    if rep.role != "post":
        rep.reasons.append(ROLE_REASON.get(rep.role, ROLE_REASON["other"]))
        return rep

    # What a post pass can read and write.
    for s in rep.samplers:
        if s in COLOR_SAMPLERS or s in DEPTH_SAMPLERS:
            continue
        alias = BUFFER_NAMES.get(s, s)
        what = "a buffer an earlier pass wrote" if alias.startswith("colortex") or s in BUFFER_NAMES else "a texture Horizon has no equivalent of"
        if s.startswith("shadow"):
            what = "the shadow map"
        rep.reasons.append(f"reads {s} ({what})")
    for u in rep.uniforms:
        if u not in UNIFORMS:
            rep.reasons.append(f"uniform {u}: {NO_EQUIVALENT.get(u, 'no Horizon equivalent')}")
    for v in rep.varyings:
        if v not in vertex_uv:
            rep.reasons.append(f"varying {v} is computed by the vertex shader")
    if 0 not in rep.outputs:
        rep.reasons.append(f"writes only colortex{','.join(map(str, rep.outputs))}, not the picture")
    for f in rep.features:
        rep.reasons.append(f"uses {f} (not translated yet)")
    if rep.reasons:
        return rep
    if not convert_ok:
        rep.status = "convertible"
        return rep

    env = Env(
        uniforms=UNIFORMS,
        samplers={**{s: "color" for s in COLOR_SAMPLERS}, **{s: "depth" for s in DEPTH_SAMPLERS}},
        varyings={v: ("input.uv", "vec2") for v in vertex_uv},
    )
    try:
        out = translate_fragment(frag.text, env)
    except Unsupported as e:
        rep.reasons.append(f"translation: {e}")
        return rep
    except GlslSyntaxError as e:
        rep.reasons.append(f"translation: syntax: {e}")
        return rep
    rep.status = "converted"
    rep.wgsl = out["wgsl"]
    rep.notes += out["notes"]
    if any(s in DEPTH_SAMPLERS and s != "depthtex0" for s in rep.samplers):
        rep.notes.append("depthtex1/2 read as the one depth Horizon has (with translucents)")
    return rep


def post_sort_key(name: str):
    m = POST_ORDER.match(name)
    return (1, 0) if name == "final" else (0, int(m.group(2) or 0))


def analyse(pack: Pack, dimension: str, convert_ok: bool) -> list[Report]:
    progs = find_programs(pack, dimension)
    reports = [analyse_program(pack, p, convert_ok) for p in progs.values()]
    order = {"post": 0, "sky": 1, "deferred": 2, "geometry": 3, "shadow": 4, "prepass": 5}
    reports.sort(key=lambda r: (order.get(r.role, 9), post_sort_key(r.name) if r.role == "post" else (0, 0), r.name))
    return reports


# ----------------------------------------------------------------------------------------- Horizon pack

def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "pack"
    return s[:50]


def find_checker() -> str | None:
    found = shutil.which("horizon-shader")
    if found:
        return found
    cand = os.path.join(os.path.expanduser("~"), ".cargo", "bin", "horizon-shader.exe" if os.name == "nt" else "horizon-shader")
    return cand if os.path.exists(cand) else None


def manifest(pack_id: str, name: str, passes: list[tuple[str, str]], source: str, total: int) -> str:
    lines = [
        "schema = 1", f'id = "{pack_id}"', f'name = "{name}"', 'version = "0.1.0"', 'api = "0.1"',
        f'description = "{len(passes)} of {total} programs of {source}, converted for Horizon (see README.md)."',
        "",
    ]
    for fname, label in passes:
        lines += ["[[post]]", f'file = "post/{fname}.wgsl"', f'name = "{label}"', ""]
    lines += ["[budget]", "gpu_ms = 4.0", ""]
    return "\n".join(lines)


def check_pass(checker: str, pack_id: str, fname: str, wgsl: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "post"))
        with open(os.path.join(tmp, "post", f"{fname}.wgsl"), "w", encoding="utf-8") as f:
            f.write(wgsl)
        with open(os.path.join(tmp, "horizon-shaders.toml"), "w", encoding="utf-8") as f:
            f.write(manifest(pack_id, "check", [(fname, fname)], "check", 1))
        proc = subprocess.run([checker, "check", tmp, "--json"], capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)[0]  # one result per path checked
    except (json.JSONDecodeError, IndexError):
        return False, (proc.stdout + proc.stderr).strip()[:400]
    if data.get("ok"):
        return True, ""
    problems = [
        f"{p.get('file')}:{p.get('line')}:{p.get('column')}: {p.get('message')}" if isinstance(p, dict) else str(p)
        for p in data.get("problems", [])
    ]
    return False, "; ".join(problems)[:600]


def write_pack(reports: list[Report], out: str, source: str, checker: str | None) -> dict:
    posts = [r for r in reports if r.status == "converted"]
    pack_id = slug(os.path.splitext(os.path.basename(source.rstrip("/\\")))[0]) + "_converted"
    notes = []
    if checker:
        for r in list(posts):
            ok, why = check_pass(checker, pack_id, slug(r.name), r.wgsl or "")
            if not ok:
                r.status, r.wgsl = "unsupported", None
                r.reasons.append(f"Horizon's check refused the converted WGSL: {why}")
                posts.remove(r)
    else:
        notes.append("horizon-shader was not found: the converted WGSL is NOT checked")
    if len(posts) > MAX_POST:
        for r in posts[MAX_POST:]:
            r.status = "unsupported"
            r.reasons.append(f"more than {MAX_POST} post passes: Horizon runs at most {MAX_POST}")
        posts = posts[:MAX_POST]
    if not posts:
        return {"written": False, "passes": 0, "notes": notes + ["nothing could be converted: no pack written"]}
    os.makedirs(os.path.join(out, "post"), exist_ok=True)
    passes = []
    for r in posts:
        fname = slug(r.name)
        with open(os.path.join(out, "post", f"{fname}.wgsl"), "w", encoding="utf-8") as f:
            f.write(r.wgsl or "")
        passes.append((fname, r.name))
    title = os.path.splitext(os.path.basename(source.rstrip("/\\")))[0]
    with open(os.path.join(out, "horizon-shaders.toml"), "w", encoding="utf-8") as f:
        f.write(manifest(pack_id, f"{title} (converted)", passes, title, len(reports)))
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme(title, reports, passes))
    return {"written": True, "passes": len(passes), "id": pack_id, "notes": notes}


def readme(title: str, reports: list[Report], passes) -> str:
    kept = {p for p, _ in passes}
    lines = [
        f"# {title} (converted)", "",
        f"Converted for Horizon's own shader format by shaderpack_convert.py: {len(passes)} post pass(es) of "
        f"{len(reports)} programs. It is derived from {title}: the original pack's licence applies to this "
        "conversion, which is for personal use unless that licence says otherwise.", "",
        "It is not the original look: the lighting, shadows and G-buffer passes have no Horizon equivalent, so "
        "only the passes below run, over Horizon's own picture.", "", "## Converted", "",
    ]
    lines += [f"- {r.name}" for r in reports if slug(r.name) in kept and r.status == "converted"] or ["- none"]
    lines += ["", "## Left out", ""]
    for r in reports:
        if r.status != "converted":
            lines.append(f"- {r.name} ({r.role}): {r.reasons[0] if r.reasons else 'not converted'}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------- output

def print_report(reports: list[Report], pack: Pack, dimension: str, source: str, options: int) -> None:
    print(f"Pack: {os.path.basename(source)}   dimension folder: {dimension}")
    print(f"Programs with a fragment shader: {sum(1 for r in reports if r.role != 'compute')}"
          f"   pack options found: {options}")
    print()
    width = max((len(r.name) for r in reports), default=8)
    for r in reports:
        mark = {"converted": "CONVERTED ", "convertible": "convertible", "unsupported": "unsupported"}[r.status]
        print(f"  {r.name:<{width}}  {r.role:<9} {mark}")
        for reason in r.reasons[:6]:
            print(f"  {'':<{width}}    - {reason}")
        if len(r.reasons) > 6:
            print(f"  {'':<{width}}    - ... and {len(r.reasons) - 6} more")
        for note in r.notes:
            print(f"  {'':<{width}}    * {note}")
    ok = sum(1 for r in reports if r.status in ("converted", "convertible"))
    print()
    print(f"Horizon can run {ok} of {len(reports)} programs of this pack (post passes over the picture and depth).")
    if ok == 0:
        print("None map onto Horizon's stages (no shadow, no G-buffers): the pack cannot be converted as is.")


def to_json(reports: list[Report]) -> str:
    return json.dumps([{k: v for k, v in r.__dict__.items() if k != "wgsl"} for r in reports], indent=2)


def count_options(pack: Pack) -> int:
    total = 0
    for name in pack.names():
        if name.endswith((".glsl", ".fsh", ".vsh", ".inc")):
            try:
                total += len(option_defines(pack.read(name)))
            except KeyError:
                pass
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "convert"):
        p = sub.add_parser(name)
        p.add_argument("pack", help="a shader pack zip or folder")
        p.add_argument("--dimension", default="world0", help="dimension folder to read (default world0)")
        p.add_argument("--json", action="store_true", help="print the report as JSON")
        if name == "convert":
            p.add_argument("-o", "--out", required=True, help="folder to write the Horizon pack to")
            p.add_argument("--no-check", action="store_true", help="do not run horizon-shader check")
    args = ap.parse_args(argv)

    pack = Pack(args.pack)
    reports = analyse(pack, args.dimension, convert_ok=args.cmd == "convert")
    result = None
    if args.cmd == "convert":
        checker = None if args.no_check else find_checker()
        result = write_pack(reports, args.out, args.pack, checker)
    if args.json:
        print(to_json(reports))
    else:
        print_report(reports, pack, args.dimension, args.pack, count_options(pack))
        if result:
            print()
            for n in result["notes"]:
                print(f"note: {n}")
            if result["written"]:
                print(f"Wrote {result['passes']} post pass(es) to {args.out} (pack id {result['id']}).")
            else:
                print("No pack written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
