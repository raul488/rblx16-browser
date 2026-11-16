# RBLX2016 browser port — C++ → WebAssembly

This directory is the browser platform layer for the 2016 Roblox client.
The 2016 source tree (`../roblox-2016-source-code-master`) is treated as a
read-only reference; patches and shims live here.

## Layout

- `tools/gen_ci_includes.py` — builds a case-insensitive include overlay
  (Windows include paths are case-insensitive; Linux/Emscripten are not).
- `shim/include/` — generated symlink overlay, put first on the include path.
- `shim/` — platform shims (GLES3, Windows, audio/network stubs).
- `src/` — the new `WebClient` platform layer.
- `build/` — Emscripten build scripts.
- `patches/` — source patches applied to a copied build tree, if needed.
- `out/` — build output (not tracked).

## Toolchain

- Emscripten: `/home/raul/emsdk` (requires the Python 3.12 in
  `/home/raul/python312`).
- Boost 1.56 headers: `/home/raul/boost_1_56_0` (the in-tree Contribs copy
  is incomplete).
- The new WebClient targets the 2016 engine's `VISIT_SOLO` mode: no auth,
  no networking, local RBXL files only.

## Status

- Lua 5.1.4 stock builds to Wasm and runs.
- GfxCore GL backend builds to Wasm and renders in WebGL2.
- ShaderCompiler builds on Linux and emits GLSL ES 3.00 from the 2016
  `plastic.hlsl`.
- Full `App` engine build is the active phase.
