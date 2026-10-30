# Two tools

- `hz_connect.py`: **connects an Iris/OptiFine pack to Horizon's own loader** (see "Connector" below). This is the one to use to
  actually run packs such as BSL.
- `shaderpack_convert.py`: **converts** a pack to Horizon's own shader format (WGSL). Only possible for simple packs that use
  nothing but post-processing.

## Connector (`hz_connect.py`)

```sh
python -I hz_connect.py BSL_v10.1.8.zip --list                                   # preview what would be added
python -I hz_connect.py BSL_v10.1.8.zip -o "%APPDATA%\horizon\shaderpacks\BSL_v10.1.8_horizon.zip"
python -I test_connect.py
```

Every instance has its own `shaderpacks` folder (`%APPDATA%\horizon\` and `%APPDATA%\horizon\instances\<id>\`). The pack list is
read when the Shader Packs screen opens.
Horizon's Shader Packs screen has no settings screen. To apply a pack and change its settings by group inside the game, use the
native mod `pack_options` (Options > Mods > Shader Options: the pack list and the groups, water settings first;
`native/pack-options/README.md`). If you need a **file copy** with changed options, use `tools/variant_pack.py`.
On Vulkan a pack does not receive block ids (`mc_Entity`), so settings such as water have no effect there; you need
`native/pack-flat/` (`pack_flat`) or the environment variable `HORIZON_FLAT_DRAWS=1`.

Horizon's loader reads Iris-format packs directly, but it only fills a fixed list of uniforms (60) and leaves the rest at 0 (the
log says `zero for now`). This tool writes a **copy** of the pack in which 12 uniforms that the public Iris uniform documentation
defines and Horizon lacks (`bedrockLevel`, `cloudHeight`, `cameraPositionFract`, `cameraPositionInt`,
`previousCameraPositionFract`, `previousCameraPositionInt`, `eyePosition`, `playerLookVector`, `isRightHanded`,
`relativeEyePosition`, `endFlashIntensity`, `endFlashPosition`) are added to the pack's `shaders.properties` as custom uniforms.
Horizon's loader binds custom uniforms to every program, so the client does not need to be rebuilt.

**View-bobbing fix**: Horizon multiplies the walking camera sway (a rotation of about 3 and 5 degrees, and a translation) into
`gbufferProjection` (Iris/OptiFine put it in `gbufferModelView`). A **shortcut that uses only the diagonal of
`gbufferProjectionInverse`** when it restores a position from depth (BSL's `ToNDC`) ignores that rotation, so shadows, fog and AO
swing up and down with each step, in proportion to the distance (seen in BSL: several times the screen sway). The connector
rewrites functions of the shape "take a vec3, turn it into NDC with `* 2.0 - 1.0`, return `.xyz / .w`" that use only the
diagonal so that they **multiply by the whole matrix**. For a pure perspective projection the result is the same, so nothing
changes when there is no sway. A function of another shape is left alone, and running the tool twice changes nothing more.
What remains: other shortcuts that use only some elements of the projection matrix, such as `GetLinearDepth`, are not fixed (no
effect on shadow position; a small effect on fog and depth of field).
The real fix is for Horizon to put the sway into the model-view side, so that no pack needs patching (a native mod could not
hook `FrameUniforms::update` because of a closure inside it).

**The same fix as a native mod**: `native/pack-connect/` (`pack_connect`) applies the two things above (the added uniforms and
the position-restoring fix) when the game reads a pack file (a HEAD hook on `Pack::read_text`). With that mod in the build, the
**original pack** can stay in `shaderpacks/` (no copy needed). The Rust code is `native/pack-connect/patch.rs`; it was tested
to give the same output as the Python on real BSL files. Without that mod in the build, or on another Horizon build, use the copy
made by `hz_connect.py` above.

- The original is never changed. A copy is a derivative of the original pack, so the original's licence applies (personal use).
- Limits of the values: `bedrockLevel` is the overworld value (-64), `relativeEyePosition` does not follow third person (it is 0),
  and `isRightHanded` is always true.
- The tool's list of "uniforms Horizon provides" is `NAMES` in `gfx/pack_uniforms.rs`. If a game update adds uniforms, this list
  must be updated too.
- Iris source was not read. Only the documentation (https://shaders.properties/current/reference/uniforms/overview/ and its
  sub-pages) and Horizon's own code were used. This sentence applies to `hz_connect.py`, `variant_pack.py`, and the native mods
  `pack_connect`, `pack_noise`, `terrain_alpha`, `pack_flat` and `pack_options`.

# shaderpack_convert

Analyses an OptiFine/Iris shader pack (zip or folder) and converts only the parts that can be moved to Horizon's own shader
format (API 0.1).

```sh
python -I shaderpack_convert.py analyze pack.zip             # report: which programs move and which do not
python -I shaderpack_convert.py convert pack.zip -o out_dir  # save the post passes that move as a Horizon pack
python -I test_convert.py                                    # tests (also run the official check if horizon-shader is present)
```

- The zip is never unpacked; it is read in memory. It is an external file, so always run with `python -I`.
- Each converted pass is checked with `horizon-shader check` (a warning that the check was skipped if it is missing), and only
  passes that pass go into the pack.
- `--dimension world0` (the default) is the dimension folder the programs are read from. A dimension folder wins over programs in
  the root.

## What works and what does not

Horizon's format has only `sky`, `material` and `post` (full-screen passes, at most 8), and no shadow pass or G-buffer.
So what can be converted is **a `composite*` or `final` program that reads only the picture colour (`colortex0`) and depth
(`depthtex*`)**.

| Converted | Not converted (the report says why) |
|---|---|
| reads of `colortex0`/`gcolor`, `depthtex0~2` | `colortex1~`, `gaux*`, `noisetex`, shadow maps |
| `viewWidth`, `frameTimeCounter`, `rainStrength`, `sunPosition`, `skyColor`, `isEyeInWater`, ... | `gbufferProjection*` (depth runs the other way), previous-frame matrices, `shadow*` |
| functions, `if`/`for`/`while`, vector and matrix maths, `#define`/`#include`/`#if` | structs, arrays, `out`/`inout` parameters, `discard`, `inverse()` |
| a single varying, `texcoord` | varyings computed in the vertex shader (`sunVec` and the like) |

A pack's options (`#define`) are fixed at their default values in the conversion (they do not appear in Horizon's options
screen). `gbuffers_*`, `shadow` and `deferred` have no counterpart stage in Horizon and are not converted.

## Licence

The result of a conversion is a derivative of the original pack. The original pack's licence applies, so keep it to personal
use. This tool was written from public shader-pack format documentation and did not use the source of Iris or OptiFine.
