#!/usr/bin/env python3
"""Packs native mod bundles (folders with horizon-mixin.toml and .rs helpers) as `.hnmod` files, the form
the launcher installs from `horizon-native-mods/` (docs/mods.md, crates/horizon-mods/src/index/native.rs).

    python tools/package_hnmod.py native/korean-ime native/skin-reload-fix -o dist

A `.hnmod` is a zip: `horizon-native.json` at the root and the bundle under `mixins/<id>/`. The manifest
repeats the bundle's id, version and tested Horizon revision, which must match horizon-mixin.toml. The files
are written in path order with fixed dates, so the same bundle always gives the same bytes.
"""
import argparse
import json
import os
import sys
import tomllib
import zipfile

# The horizon-mixins revision the launcher builds with (launcher/src/native_mods.rs, MIXINS_REV).
MIXINS = "3f99a5c60dfa311cc365524c44ae2fd799bfa1de"
GAME = "26.3"


def package(bundle: str, out_dir: str) -> str:
    with open(os.path.join(bundle, "horizon-mixin.toml"), "rb") as f:
        source = tomllib.load(f)
    mod_id, version = source["mod"]["id"], source["mod"]["version"]
    revision = source["compatibility"]["horizon_revision"]
    manifest = {"schema": 1, "id": mod_id, "version": version, "game": GAME, "horizon": revision, "mixins": MIXINS}
    files = sorted(n for n in os.listdir(bundle) if n.endswith((".rs", ".toml")))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{mod_id}-{version}.hnmod")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)

        add("horizon-native.json", (json.dumps(manifest, indent=2) + "\n").encode())
        for name in files:
            with open(os.path.join(bundle, name), "rb") as f:
                add(f"mixins/{mod_id}/{name}", f.read())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundles", nargs="+", help="bundle folders (each with horizon-mixin.toml)")
    ap.add_argument("-o", "--out", default="dist", help="where to write the .hnmod files")
    args = ap.parse_args()
    for bundle in args.bundles:
        print(package(bundle, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
