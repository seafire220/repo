# Local changes to Inox2D

`inox2d/` and `inox2d-opengl/` are copied from
https://github.com/Inochi2D/inox2d at commit
`d4dd9dd7f16b775042cbda44370570abf9a8cf81` (BSD-2-Clause; each folder keeps
its `LICENSE`). They are kept here, rather than fetched, because they need
the changes below and so that builds do not depend on the network.

## inox2d-opengl: run on OpenGL ES 3.0

Low-cost ARM boards provide OpenGL ES, not desktop OpenGL 3.3.

1. `src/shader.rs` — shaders get the GLSL ES 3.00 header whenever the context
   is OpenGL ES, decided at run time. Upstream does this only when compiled
   for WebAssembly.
2. `src/texture.rs` — textures use `CLAMP_TO_EDGE` on OpenGL ES and WebGL,
   which have no `CLAMP_TO_BORDER`. In the browser the unsupported mode was
   being rejected, leaving stray fragments at part edges.
3. `src/texture.rs` — the off-screen emissive buffer is 8-bit on OpenGL ES,
   where rendering to float textures is an optional extension.
4. `src/lib.rs` — helper `is_gles`.
4a. `src/shader.rs` — on desktop OpenGL too, `#version 330 core` goes first, ahead of the licence
    comment the shaders begin with (a Mac's driver is strict about anything before it).

## inox2d: opacity bindings

Upstream parses a parameter binding on `opacity` and then ignores it. Rigs
that blink by swapping open-eye art for closed-eye art, or that reveal a
mouth interior, depend on it.

5. `src/params.rs`, `src/formats/payload.rs` — `BindingValues::Opacity` now
   carries its values and multiplies the target's opacity when applied.
6. `src/node/components.rs`, `src/puppet/transforms.rs` — each drawable's
   authored opacity is remembered (`BaseOpacity`) and restored at the start
   of every frame, as transforms and draw order already are.

## inox2d: tolerant metadata

7. `src/formats/payload.rs` — optional metadata fields (`rigger`, `artist`,
   `copyright`, `licenseURL`, `contact`, `reference`, `name`, `version`,
   `preservePixels`) may be absent. Files written by nijilive-family tools
   omit several of them.

## inox2d: parameters at zero

8. `src/params.rs` — every parameter is applied every frame. Upstream skips
   a parameter whose value is exactly (0, 0), assuming zero is neutral. It is
   not neutral for rigs where zero means "eye closed" or "layer hidden"; a
   blush layer that should have been off was showing.

9. **Premultiplied textures** (`inox2d/src/texture.rs`). The renderer blends
   with premultiplied alpha (`ONE, ONE_MINUS_SRC_ALPHA`), as Inochi2D does,
   and Inochi2D premultiplies texture colour by alpha when it loads a
   puppet; Inox2D did not, so every half-transparent edge (anti-aliased
   outlines, soft blush, a character's cut-out edge) was drawn too bright,
   showing as pale halos. Textures are now premultiplied on load.

# Ayagami (Live2D-format player core)

`ayagami/` is the core crate of https://github.com/AyagamiDev/ayagami at
commit `0d1d7aa3efe57b1b363b72e672e353c52a9c15d6` (2026-09-14), dual licensed
MIT or Apache-2.0 (`LICENSE-MIT`, `LICENSE-APACHE`, `COPYRIGHT` kept beside
it). It reads `.moc3` files, evaluates parameters, deformers and physics, and
gives each art mesh's vertices, opacity, colours and draw order. The stage
draws them itself with OpenGL ES 3 (`src/live2d.rs`), so Ayagami's own wgpu
renderer and demo are not included.

1. `Cargo.toml` — builds only as an ordinary library: the `cdylib` crate type,
   the `load-model` example program (`src/main.rs`) and its `env_logger`
   dependency are removed. No source file is changed.
