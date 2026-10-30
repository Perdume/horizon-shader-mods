# The common structure of shader packs and what Horizon's loader supports

Evidence tags: **[docs]** the public Iris documentation (https://shaders.properties/current/reference/), **[code]** Horizon source
`e84a47c` (`crates/horizon-shaders`, `horizon-client-render`), **[log]** the author's game log, **[BSL]** the file layout of
BSL v10.1.8. Iris/OptiFine source was not read; only format information at the level of the documentation was used.

These are notes from an early analysis, written against `e84a47c`. One row has since been corrected (`mc_Entity`, section 4):
Horizon only supplies it with flat section draws, so on Vulkan a pack sees -1 for every vertex unless `native/pack-flat` (or
`HORIZON_FLAT_DRAWS=1`) is used.

## 1. Folder structure [docs]

```
(zip or folder)
└─ shaders/
   ├─ shaders.properties          pack settings + the settings screens (screen/sliders/profile) + custom uniforms
   ├─ *.lang                      translations of option names and tooltips
   ├─ block.properties            block -> numeric id (passed as mc_Entity)
   ├─ entity.properties           entity -> id
   ├─ item.properties             item -> id
   ├─ dimension.properties        dimension -> folder name (the worldN rule when absent)
   ├─ <program>.vsh/.fsh/.gsh/.csh/.tcs/.tes     in the root: the default for every dimension
   ├─ world0/  world-1/  world1/  per-dimension programs (they win over the root)
   └─ lib/ program/ tex/ ...      conventional folders. #include "/lib/x.glsl" (a leading / is relative to shaders, otherwise to the current file)
```

BSL v10.1.8 [BSL]: the programs are thin wrappers in `world0/`, `world-1/` and `world1/`, the real code is in `program/` (37 files)
and `lib/` (51 files), and the root holds only `*.properties`. This is the common structure above exactly.

## 2. Execution order [docs]

`setup` (compute only) -> `begin` -> `shadow` -> `shadowcomp` -> `prepare` -> **gbuffers (opaque)** -> `deferred` ->
**gbuffers (translucent)** -> `composite` -> `final`

| Kind | Programs | Notes |
|---|---|---|
| gbuffers style | `gbuffers_*`, `shadow` | draws real geometry. Vertex + fragment (+ geometry/tessellation), no compute |
| composite style | `begin`, `shadowcomp`, `prepare`, `deferred`, `composite`, `final` | one full-screen rectangle. Suffixes 1~99 give up to 100 (`final` only 1). Compute allowed |

## 3. gbuffers programs and their fallbacks [docs]

A program that is missing is drawn by a fallback. For example `gbuffers_terrain` (solid blocks) <- `gbuffers_textured_lit` <-
`gbuffers_textured` <- `gbuffers_basic`. `gbuffers_water` (translucent terrain) falls back to `gbuffers_terrain`.
`shadow` is one file (`shadow.vsh/fsh`) that draws every kind of geometry (Iris 1.8+ adds `shadow_solid` and others).

## 4. Vertex attributes (vertex shader only) [docs] / whether Horizon supplies them [code]

| Attribute | Meaning (docs) | Valid in | Horizon |
|---|---|---|---|
| `mc_Entity` | x = id from `block.properties` (-1 if none), y = 1.0 for fluids, -1.0 otherwise | terrain, water, shadow | Supplied **only with flat section draws** (a per-sprite table; log: 683 of 1291 sprites). Otherwise every vertex gets -1, which is the default on Vulkan (see `native/pack-flat`) |
| `mc_midTexCoord` | centre UV of the current quad's texture | gbuffers_*, shadow | supplied (the sprite centre) |
| `at_tangent` | model-space tangent, sign of w = handedness | gbuffers_*, shadow | **constant** `(1,0,0,1)` |
| **`at_midBlock`** | xyz = offset from the vertex to the block centre (1/64 block), w = block light emission 0~15 | terrain, water, shadow | **not supplied -> 0** (absent from the whole source; an unregistered attribute is 0) |
| `vaUV1` | entity overlay | gbuffers_*, shadow | not supplied |
| `vaPosition/vaUV0/vaUV2/vaColor/vaNormal` | modern-format inputs | | supplied |
| `mc_chunkFade` | chunk fade (1.21.11+, Iris only) | gbuffers_* | not found in the code |

## 5. Buffers [docs]

`colortexN` (RGBA, screen size, configurable), `depthtexN`, `shadowtexN`/`shadowcolorN` (shadow-map size), `noisetex`, three
atlases. Iris adds custom textures, custom images and SSBOs. [log] The shadow map is 2048x2048, with 2 `shadowcolor` buffers and
`shadowtex1` configured.

## 6. What Horizon's loader supports

| Item | Status | Evidence |
|---|---|---|
| dimension folder `world0` | supported | [log] |
| `gbuffers_terrain/water/skybasic/skytextured/clouds/hand/weather/entities/block/textured` | supported | [log] |
| `shadow`, `shadow_water` | supported (`shadow_solid/cutout` are not used) | [code] post.rs |
| `prepare`, `deferred`, `composite`, `final` | supported | [code][log] |
| `shadowcomp` (ordinary vsh/fsh) | supported | [code] |
| **`shadowcomp.csh` (compute)** | **does not seem to run**: BSL's `shadowcomp` is compute only and is not in the log's program list (9 programs) | [BSL][log] |
| `begin`, `setup` | not found in the code | [code] |
| compute shaders, custom images, SSBO, `iris.features.*` | not found in the code (seem unsupported) | [code] |
| uniforms | 60 built in + the pack's custom uniforms. Anything else is 0 | [code] |
| vertex attributes | the table in section 4 | [code] |

## 7. Gaps that actually affect BSL v10.1.8 (output of the structure reader, `hz_connect.py --structure`)

**Correction**: an earlier note listed `at_midBlock` as a candidate cause of the grass problem, but the structure reader shows
that BSL's `gbuffers_terrain` does **not declare** this attribute, and `shadow` declares it without using it. So `at_midBlock`
being 0 has nothing to do with BSL's grass. `mc_Entity` and `mc_midTexCoord`, which the terrain program does use, are supplied
by Horizon (`mc_Entity` with the flat-draw caveat in section 4).

The gaps that really matter:

| Gap | Affected | Evidence |
|---|---|---|
| **`texture.noise` is ignored**: instead of the noise image BSL names, Horizon feeds a 256x256 **white noise** it builds itself as `noisetex` | every program that reads `noisetex` (it looks like clouds, water waves, dithering) | [code] `noise()` in pack_post.rs; the loader has no `texture.noise` handling |
| other custom textures such as `texture.composite.colortex7` are a 1x1 stand-in | composite | [code] `Source::Dummy` |
| the 30 `image.*` entries and the compute `shadowcomp.csh` do not run | BSL's optional features (`iris.features.optional`) | [BSL][log] |
| `at_tangent` is constant | `gbuffers_water` (water normals) | [BSL][code] |
| some gbuffers (`armor_glint`, `beaconbeam`, `spidereyes`, ...) not confirmed in the loader | that geometry | [log] |

- **Shadows**: the shadow camera moves on a grid at `shadowIntervalSize` steps (BSL does not set it) [code]. Edge shimmer can be
  a side effect of that design; if a shadow slides off its object as a whole, it is a loader problem such as a matrix mismatch.
  The cause has not been pinned down.
- **Grass**: the only confirmed gap that applies directly is `noisetex` (white noise). Whether BSL uses noise on grass was not
  checked, so this is a hypothesis.
