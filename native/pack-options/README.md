# pack_options

A screen to **apply a shader pack in the game and change its settings by group**, for Iris/OptiFine-format packs. Horizon's
Shader Packs screen has one on/off button per pack and no settings screen, so this adds a "Shader Options" entry to
Options > Mods.

## How to use

1. Put `pack_options` and `pack_connect` (0.3.1 or later) into the native build. Both must be present.
2. Open Options > Mods, then Settings next to **Shader Options**.
3. The first page, from the top:
   - the pack in use and how many settings it has
   - **the pack list**: every pack in `shaderpacks/` as `name: ON/OFF`. A click applies it (a click on the one in use turns it
     off). The choice is saved the same way the Shader Packs screen saves it.
   - **the groups**: first `Water (24) >` (every setting whose name contains WATER), then the groups the pack puts on its own
     settings screen (`screen`): for BSL Lighting, Material, Environment, Camera, Color...; for Complementary Performance,
     Camera, Lighting, Atmosphere, Materials... A button ending in `>` opens that group.
   - `Reset all options` at the bottom clears every choice for that pack.
4. Inside a group you get `< Back`, where you are (`Lighting > Shadows`), and the group's settings. A group inside a group
   opens the same way.
5. Each click on a setting cycles it to its next value (and back to the first after the last) and reloads the pack at once.
   Choices are kept in `shader-options/<pack file name>.txt` (`NAME=value` lines); the pack files stay as they were.

For example BSL has 386 settings, 24 of them about water (`WATER_A` alpha, `WATER_F` fog strength, the colour
`WATER_R/G/B`, `WATER_I`, normals, parallax, caustics...), and Complementary Reimagined has 435, 24 about water
(`WATER_ALPHA_MULT` opacity, `WATER_FOG_MULT`, foam, refraction, colour multipliers...).

## What water settings need

A pack such as BSL recognises water blocks through `mc_Entity` (the block id). Horizon only passes it to the pack with **flat
terrain draws**, which are on by default only on Metal, so on Vulkan every vertex had `mc_Entity` = -1 (and water, waving plants
and emissive blocks were off in the pack). `native/pack-flat/` (`pack_flat`) fixes that. Without that mod, set the environment
variable `HORIZON_FLAT_DRAWS=1` for the same effect.

## How it works

- **Finding the settings**: it reads `#define NAME` (a switch), `#define NAME value //[a b c]` (a value option) and
  `const int NAME = value; //[a b c]` (a constant option) from the pack's source files. `screen` (the first page) and
  `screen.<name>` (the groups) of `shaders.properties` are shown as the hierarchy they describe: `[name]` is a group, `*` stands
  for the settings no screen lists, `<profile>` and `<empty>` are skipped. A pack with no `screen` shows every setting it found
  on the first page. Labels come from the pack's `lang/en_US.lang` (`option.NAME`, `value.NAME.value`, `screen.NAME`).
- **Applying**: when the game reads a pack file (`Pack::read_text`), `pack_connect` writes the stored choices into the
  `#define`/`const` lines that declare them. Only the value changes; the rest of the line (the list of choices, the comment) stays.
  A switch gets a `//` put in front or taken off.
- **Reloading**: a changed value drops the pack with `renderer.drop_shader_packs()`, so the game reads it again on its next
  frame. Picking another pack only saves the choice, as the Shader Packs screen does, and the game reads it on its next frame.

## Why it is not under the Shader Packs screen

The rows of that screen are built inside a function that uses closures (`list_rows`), and the launcher's horizon-mixins cannot
hook a function that has closures. A button cannot be added to that screen, so the pack list is part of this page instead
(applying and settings on one screen).

## Provenance

The option syntax (`#define`, `//[choices]`, `screen`, `screen.<name>`, lang files) comes from the "shader settings" page of
the public shaders.properties documentation. The game side was written from Horizon's own code only (`native_mods`,
`native_settings` and `native_settings_click` in `native_hooks.rs`; `shader_packs.rs`; `shader_pack_list.rs`). Iris source was
not read, and no description written from reading it was used. The hooks are the functions that Horizon's `native-hooks.json`
points to.

## Limits

- Buttons only, no sliders. A value option walks its list of choices; a value that is not in the list cannot be picked.
- `profile.*` (presets) is not supported. A setting that no `screen` lists is not shown (a pack that collects the rest with `*`
  is shown).
- A pack that declares the same name in several files gets the same value written into every declaration (the documentation
  also asks for identical definitions in all files).
- Each change reloads the pack, so shader compile time is paid every time (about 0.4 s for BSL and 4.6 s for Complementary,
  from earlier logs).
- Unlike `tools/variant_pack.py`, which makes a patched copy of a pack, this never changes the original.

## Verification

- The logic (`logic.rs`) is tested on real BSL and Complementary files: finding settings, the `screen` hierarchy (number of
  groups, the water group, how many settings can be reached), labels, and writes that change only one line.
- The settings page (`runtime.rs`) was compiled against a stand-in with the shape of the real `Game` and a `Pack` stand-in that
  reads folder packs, and run end to end (opening and leaving groups, clicks, the saved file, reset, the reload call, applying
  and turning off packs, packs of another format). 11 tests pass.
- `logic.rs` must be **the same file** as `pack_connect`'s `options_logic.rs` (`tools/check_options_logic.py`).
- Run in the real game: 0.1.0 (one flat list of buttons) and 0.2.0 (groups and the pack list). The page opens, clicks change the
  saved file and reload the pack, and the values reach the shaders (exposure -2.00 darkened the screen). The 0.2.0 layout
  still needs polish.
