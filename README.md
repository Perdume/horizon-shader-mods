# Native shader-pack mods for Horizon (unofficial)

Five native mods that make **Iris/OptiFine-format shader packs** (BSL, Complementary Reimagined, ...) work better in
Horizon, the Rust reimplementation of Minecraft Java 26.3. Built and tested with BSL v10.1.8 and Complementary Reimagined
r5.9.3 on Windows with Vulkan (RTX 3050 Ti laptop).

## What is in the box

| Mod | What it does | Checked in game |
|---|---|---|
| `pack_flat` 0.1.0 | **Makes shader packs receive block ids (`mc_Entity`) on Vulkan.** Horizon only passed them on Metal, so on Vulkan BSL's water code, waving plants and leaves, emissive blocks and glass were all switched off. | Yes: water is drawn by BSL's own water code and its water settings take effect |
| `pack_options` 0.2.0 | Options > Mods > Shader Options: a list of packs to apply, then the pack's settings in the groups the pack defines (a water group first). Clicking a setting cycles it to its next value and reloads the pack. | Yes: opens, groups, clicks, saves, reloads. The layout still needs polish |
| `pack_connect` 0.3.1 | Writes the chosen settings into the pack's files as they are read (the pack itself stays untouched). Adds the 12 Iris uniforms that Horizon lacks, and fixes shadows and fog that sway while walking (a depth-unprojection shortcut). | Settings: yes. The sway fix was checked on a patched copy of the pack, not yet through the mod |
| `terrain_alpha` 0.1.0 | Cuts out the alpha of grass and flowers, which were drawn as opaque quads. | Yes |
| `pack_noise` 0.1.0 | Uses the noise image a pack names with `texture.noise` as `noisetex` (Horizon builds white noise instead). | Not yet |

`pack_options` needs `pack_connect` to apply the values. Without `pack_flat`, settings that depend on block ids (water and
the like) have no effect on Vulkan. Use all five together.

## Install and build

- Target: Horizon **nightly-2026-10-30 (ba6344a)** with Minecraft 26.3. Every mod pins this commit; other builds may reject
  them or fail to compile.
- These are **native mods (an experimental feature)**. They are not sandboxed and run with your user's rights. Each `.hnmod`
  is a zip with the source inside (`.rs` files and `horizon-mixin.toml`): read it, and build only if you trust it.
- Install: put the five `.hnmod` files into the `horizon-native-mods` folder of the instance you play, for example
  `%APPDATA%\horizon\instances\<instance>\horizon-native-mods`.
- Build (takes a few minutes):

```powershell
horizon-launcher.exe native-build --instance <instance> --trust-native-code
```

- Check: start that instance after the build. "Shader Options" should appear in Options > Mods. The chosen values are kept in
  `shader-options/<pack name>.txt` in the game folder as `NAME=value` lines.
- To see the water fix without building, start the game with the environment variable `HORIZON_FLAT_DRAWS=1`. It has the same
  effect as `pack_flat`.

## Known limits

- Nothing can be added to the Shader Packs screen. Its rows are built inside a function that uses closures, and the launcher's
  mod tool cannot hook such functions. The pack list is therefore part of the Shader Options page too.
- Settings are buttons only. There are no sliders and no `profile` presets.
- Every change reloads the pack (about 0.4 s for BSL, about 4.6 s for Complementary).
- With Complementary, a few uniforms such as `endFlashIntensity` and one cloud program are logged as unknown or skipped.
- Only Vulkan has been tried. Other backends are untested.

## Provenance

The option syntax and the uniform definitions come from the public shaders.properties documentation. The game-side hooks come
from Horizon's own source, and the behaviour from running the packs and watching what they do.
**The source of Iris, OptiFine and Sodium was not read**, and no description written from reading it was used. The shader packs
themselves (BSL and the rest) are not part of this repository and keep their own licences.

## Repository layout

- `native/<mod>/`: the source of each mod (`horizon-mixin.toml` and `.rs` files). An `.hnmod` is such a folder packed with
  `python tools/package_hnmod.py native/pack-flat native/pack-options ... -o dist`.
- `tools/`: `.hnmod` packing (`package_hnmod.py`), a check that two copies of the options logic are equal
  (`check_options_logic.py`), making a patched copy of a pack (`variant_pack.py`), and Python tools that adapt a pack to
  Horizon (`shader_convert/`).
- Release files are on the Releases page: the five `.hnmod` files, `SHA256SUMS.txt`, and a zip of all of them.

Licence: not decided yet.
