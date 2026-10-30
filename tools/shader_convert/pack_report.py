"""Reads the structure of an Iris/OptiFine shader pack and says, element by element, how it meets Horizon's
loader: the programs, the vertex attributes each declares, the uniforms, and the features the pack asks for.

It reads interfaces only (which files exist, what a program declares and whether it uses it, the keys of
shaders.properties); it does not read or translate what the shaders compute.
"""
from __future__ import annotations

import re

from preprocess import PreprocessError
from shaderpack_convert import Pack, find_programs, role_of, run_preprocess, static_view

# Programs Horizon's loader runs. "log" = seen loading in the game's log, "code" = in crates/horizon-shaders.
GBUFFERS_LOADED = {
    "gbuffers_terrain", "gbuffers_water", "gbuffers_skybasic", "gbuffers_skytextured", "gbuffers_clouds",
    "gbuffers_hand", "gbuffers_weather", "gbuffers_entities", "gbuffers_block", "gbuffers_entities_translucent",
    "gbuffers_textured", "gbuffers_textured_lit", "gbuffers_particles",
}
COMPOSITE_LOADED = re.compile(r"^(prepare|deferred|composite|shadowcomp)\d*$|^final$")
SHADOW_LOADED = {"shadow", "shadow_water"}

# Vertex attributes Horizon supplies to terrain programs (client-render gfx/pack_atlas.rs); the value of one
# not listed is zero. at_tangent is the same for every face.
ATTRIBUTES_PROVIDED = {"mc_Entity", "mc_midTexCoord", "vaPosition", "vaUV0", "vaUV2", "vaColor", "vaNormal"}
ATTRIBUTES_CONSTANT = {"at_tangent": "the same (1, 0, 0, 1) for every face"}
ATTRIBUTE_DOCS = {
    "at_midBlock": "offset from the vertex to the block's centre (1/64 block) and the block's light level",
    "vaUV1": "entity overlay",
    "mc_chunkFade": "chunk fade-in progress",
}
_ATTRIBUTE = re.compile(r"\b(?:attribute|in)\s+(?:(?:highp|mediump|lowp)\s+)?(\w+)\s+((?:mc_|at_|va)\w+)\s*;")
_FEATURE_KEYS = ("iris.features.required", "iris.features.optional", "image.", "ssbo.", "texture.", "customTexture.",
                 "program.", "blend.", "alphaTest.", "scale.", "size.buffer.", "flip.", "separateAo",
                 "oldLighting", "shadow.", "particles.", "sliders", "screen", "profile.", "uniform.", "variable.",
                 "bufferObject.", "voxelizeLightBlocks", "dhShadow", "dhMat")


def horizon_loads(name: str, has_fragment: bool) -> str:
    """How Horizon treats a program by name: loaded, or why not."""
    if name in GBUFFERS_LOADED or name in SHADOW_LOADED:
        return "loaded"
    if COMPOSITE_LOADED.match(name):
        return "loaded" if has_fragment else "not run (compute only)"
    if name.startswith(("begin", "setup")):
        return "not run (Horizon has no such stage)"
    if name.startswith("gbuffers_") or name.startswith("shadow_"):
        return "unknown (not seen in the loader)"
    return "not a known program"


def program_rows(pack: Pack, dimension: str) -> list[dict]:
    rows = []
    for name, prog in sorted(find_programs(pack, dimension).items()):
        row = {"name": name, "role": role_of(name), "stages": [s for s in ("vsh", "fsh", "gsh", "csh") if getattr(prog, s)],
               "horizon": horizon_loads(name, prog.fsh is not None), "attributes": {}, "notes": []}
        if prog.vsh:
            try:
                text = run_preprocess(pack, prog.vsh).text
            except (PreprocessError, KeyError, RecursionError) as e:
                row["notes"].append(f"vertex shader not read: {e}")
            else:
                for _ty, attr in _ATTRIBUTE.findall(text):
                    used = len(re.findall(rf"\b{re.escape(attr)}\b", text)) > 1
                    row["attributes"][attr] = "used" if used else "declared only"
        if prog.fsh:
            try:
                row["uniform_count"] = len(static_view(run_preprocess(pack, prog.fsh).text)["uniforms"])
            except (PreprocessError, KeyError, RecursionError):
                pass
        rows.append(row)
    return rows


def attribute_status(attr: str) -> str:
    if attr in ATTRIBUTES_PROVIDED:
        return "provided"
    if attr in ATTRIBUTES_CONSTANT:
        return f"constant: {ATTRIBUTES_CONSTANT[attr]}"
    return "NOT provided (reads zero)"


def feature_keys(pack: Pack) -> dict[str, list[str]]:
    """The keys of shaders.properties that ask for a feature, grouped by their prefix (values not read)."""
    path = "shaders/shaders.properties"
    if not pack.has(path):
        return {}
    found: dict[str, list[str]] = {}
    for line in pack.read(path).splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        for prefix in _FEATURE_KEYS:
            if key.startswith(prefix):
                found.setdefault(prefix, []).append(key)
                break
    return found


def custom_textures(pack: Pack) -> list[dict]:
    """`texture.noise = path` and `texture.<stage>.<sampler> = path` of shaders.properties, and how Horizon
    treats each (paths only; the images are not opened)."""
    path = "shaders/shaders.properties"
    if not pack.has(path):
        return []
    found = []
    for line in pack.read(path).splitlines():
        m = re.match(r"\s*texture\.([\w.]+)\s*=\s*(\S+)", line)
        if not m:
            continue
        key, file = m.group(1), m.group(2)
        if key == "noise":
            handling = "IGNORED: Horizon gives noisetex its own 256x256 white noise (client-render pack_post.rs noise())"
        else:
            handling = "NOT LOADED: the sampler reads a 1x1 stand-in texture (pack_post.rs Source::Dummy)"
        found.append({"key": f"texture.{key}", "file": file, "in_pack": pack.has("shaders/" + file.lstrip("/")),
                      "handling": handling})
    return found


def layout(pack: Pack) -> dict:
    names = pack.names()
    dims = sorted({m.group(1) for n in names if (m := re.match(r"shaders/(world-?\d+|[\w-]+)/\w+\.(?:fsh|vsh|csh|gsh)$", n))})
    return {
        "files": len(names),
        "dimension_folders": dims,
        "dimension.properties": pack.has("shaders/dimension.properties"),
        "block.properties": pack.has("shaders/block.properties"),
        "entity.properties": pack.has("shaders/entity.properties"),
        "item.properties": pack.has("shaders/item.properties"),
        "lang_files": sum(1 for n in names if n.startswith("shaders/lang/")),
        "include_folders": sorted({n.split("/")[1] for n in names if n.count("/") >= 2 and n.split("/")[1] in ("lib", "program", "include", "util", "common")}),
    }


def build(pack: Pack, dimension: str) -> dict:
    rows = program_rows(pack, dimension)
    gaps: dict[str, list[str]] = {}
    for r in rows:
        if r["horizon"] != "loaded" and r["horizon"] != "not a known program":
            gaps.setdefault(f"program: {r['horizon']}", []).append(r["name"])
        for attr, use in r["attributes"].items():
            if attribute_status(attr).startswith("NOT") and use == "used":
                gaps.setdefault(f"attribute {attr} reads zero", []).append(r["name"])
            elif attr in ATTRIBUTES_CONSTANT and use == "used":
                gaps.setdefault(f"attribute {attr} is {ATTRIBUTES_CONSTANT[attr]}", []).append(r["name"])
    features = feature_keys(pack)
    textures = custom_textures(pack)
    for t in textures:
        gaps.setdefault("custom texture " + t["handling"].split(":")[0], []).append(f"{t['key']} = {t['file']}")
    for key in ("iris.features.required", "image.", "ssbo.", "bufferObject.", "customTexture."):
        if key in features:
            gaps.setdefault(f"feature `{key}` (no support found in the loader)", []).extend(features[key][:4])
    return {"layout": layout(pack), "programs": rows, "features": {k: len(v) for k, v in features.items()},
            "textures": textures, "gaps": gaps}


def render(report: dict, dimension: str) -> str:
    L = report["layout"]
    out = [f"Structure of the pack (dimension folder: {dimension})", ""]
    out.append(f"  files: {L['files']}   dimension folders: {', '.join(L['dimension_folders']) or '-'}   "
               f"include folders: {', '.join(L['include_folders']) or '-'}")
    out.append("  properties: " + ", ".join(f"{k}={'yes' if L[k] else 'no'}" for k in
                                            ("dimension.properties", "block.properties", "entity.properties", "item.properties"))
               + f"   lang files: {L['lang_files']}")
    out += ["", "Programs (execution order: begin, shadow, shadowcomp, prepare, gbuffers, deferred, gbuffers, composite, final)", ""]
    width = max(len(r["name"]) for r in report["programs"]) if report["programs"] else 8
    for r in report["programs"]:
        out.append(f"  {r['name']:<{width}}  {'+'.join(r['stages']):<12} {r['horizon']}")
        for attr, use in r["attributes"].items():
            out.append(f"  {'':<{width}}    attribute {attr:<15} {use:<13} -> {attribute_status(attr)}")
        for n in r["notes"]:
            out.append(f"  {'':<{width}}    * {n}")
    if report["features"]:
        out += ["", "shaders.properties asks for (key groups, counts):"]
        out.append("  " + ", ".join(f"{k.rstrip('.')}: {n}" for k, n in sorted(report["features"].items())))
    if report.get("textures"):
        out += ["", "Custom textures the pack names:"]
        for t in report["textures"]:
            out.append(f"  {t['key']} = {t['file']}  ({'in the pack' if t['in_pack'] else 'file not found'})")
            out.append(f"      {t['handling']}")
    out += ["", "Where this pack meets a gap in Horizon's loader:", ""]
    if not report["gaps"]:
        out.append("  none found at the structure level")
    for what, who in report["gaps"].items():
        shown = ", ".join(dict.fromkeys(who))
        out.append(f"  - {what}")
        out.append(f"      in: {shown}")
    return "\n".join(out)
