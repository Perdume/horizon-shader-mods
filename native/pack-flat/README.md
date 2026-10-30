# pack_flat

A small mod that makes shader packs **receive block ids (`mc_Entity`) on Vulkan**. It fixes everything a pack tells apart by block
id and that was switched off on Vulkan: BSL's water, waving plants and leaves, emissive blocks, glass.

## Cause

A pack's gbuffers programs read `mc_Entity` (the block id given by `block.properties`) as a vertex attribute. Horizon fills it
from a per-sprite-layer table only for **flat terrain draws** (`PackAtlas::terrain_inputs(flat = true)`); otherwise every vertex
gets `vec4(-1.0, -1.0, 0.0, 0.0)`. But `wanted()` in `gfx/flat.rs` turns flat draws on by default **for Metal only** (they are
meant for backends that emulate multi-draw). On Vulkan (the author's RTX 3050 Ti laptop) they were off, so packs never received
a block id.

How it was found, by watching the running game and not by reading any pack's code: in a copy of BSL, the terrain vertex shader
painted a vertex red when `mc_Entity.x` was -1, green when 0 and blue when positive. Terrain and water both came out red. With
`HORIZON_FLAT_DRAWS=1` the water was drawn by BSL's own water code (a water colour set to red took effect) at about the same frame
rate (about 99 fps).

## What it does

It intercepts `flat::wanted` (a HEAD hook). If the player has not set `HORIZON_FLAT_DRAWS`, the answer is simply whether the
device supports flat draws (`supported`). If the variable is set (`0` included), the original function decides as before.
Even with flat draws on, a frame without a pack uses them only when many sections are drawn (`MIN_SECTIONS`), so the normal
picture does not change.

## Status and limits

- Confirmed in game after building the mod: water is drawn by BSL and its water settings take effect.
- With a very large render distance and no pack, the flat path is also used on Vulkan (it is the same code Horizon uses on
  Metal). If anything looks wrong, `HORIZON_FLAT_DRAWS=0` turns the mod's effect off.

## Provenance

Written from Horizon's own code (`gfx/flat.rs`, `gfx/pack_atlas.rs`, `game/shader_packs.rs`) and from observation in the game
only. Iris source was not read, and no description written from reading it was used.
