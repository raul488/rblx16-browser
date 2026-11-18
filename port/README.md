# Building the browser port

`port/` is the browser platform layer around the 2016 engine source in
`../roblox-2016-source-code-master` (which is never modified). Everything here
targets the engine's `VISIT_SOLO` mode: no authentication, no networking, local
place files only.

## Requirements

- Emscripten SDK at `/home/raul/emsdk`, with the Python 3.12 in
  `/home/raul/python312` on `PATH` while building.
- Boost 1.56 headers at `/home/raul/boost_1_56_0` (the in-tree copy is
  incomplete).
- A Linux build of the 2016 `ShaderCompiler` for the shader packs.

## Layout

| path | contents |
|---|---|
| `src/WebClient/` | The browser platform layer: entry point, GL context, WebView, core-script bootstrap, input, audio, HTTP/asset stubs. |
| `src_patch/` | Whole-file replacements for engine sources, same relative path as under `App/`, `Base/`, `Network/`, `Rendering/`, `rbx/`, `script/`, `util/`, `v8datamodel/`. |
| `shim/` | Platform shims and header mirrors. `patched/` is hand-written; `include/` and `caseinclude/` are generated. |
| `build/` | Build and link scripts. |
| `tools/` | Header-mirror generator and the avatar catalog scraper. |
| `out/` | Objects and logs (generated). |

## Build

```sh
# 1. Case-insensitive include mirror (only after header changes)
python3 port/tools/gen_ci_includes.py

# 2. Engine objects (incremental; --only <substring> rebuilds a subset)
python3 port/build/build.py --project all --jobs 2
python3 port/build/build_network.py --jobs 2
python3 port/build/build_gfx.py --jobs 2
python3 port/build/build_g3d.py --jobs 2
python3 port/build/build_bullet.py --jobs 2
python3 port/build/build_extras.py --force

# 3. Shader packs (make SHADER_COMPILER point at the Linux ShaderCompiler)
python3 port/build/build_shaders.py

# 4. Link, preload web/content and web/shaders, write web/places/manifest.json
python3 port/build/link.py
```

`port/build/build-wasm.sh` runs steps 1, 2 and 4 in one go.

Outputs in `web/js/`: `rblx2016.js`, `rblx2016.wasm` (~57 MB) and the
preloaded content package, which `link.py` splits into
`rblx2016.data.partN` (48 MB each) plus `rblx2016.data.parts.json`. Splitting
keeps every file under the 100 MB per-file limit that static hosts impose; the
page reassembles the parts in the browser.

Editing anything under `web/content/` requires a relink, because content is
embedded in the preloaded package.

## Running

```sh
python3 web/serve.py 8080     # COOP/COEP headers, /places, /asset proxy
```

`web/serve.py` also exposes the avatar catalog (`/catalog/item/<id>`) and the
asset-delivery cookie settings used by the menu.

## Optional: avatar catalog

```sh
python3 port/tools/fetch_catalog.py
```

Downloads 2016-and-older clothing, hats, hair, shoulder/back accessories and
classic faces from Roblox, plus their thumbnails, into `web/catalog/`
(gitignored). Roblox only serves user-uploaded assets to a logged-in session,
so pass `--cookie`, set `ROBLOX_COOKIE`, or keep the cookie the menu saves in
`asset-cookie.txt`. The scraper also caches every mesh and texture the
accessory models reference in `web/asset-cache/`, so they render offline.

## Conventions

- Never edit `roblox-2016-source-code-master/`; add a file under `src_patch/`
  and rebuild that source.
- Patched headers live in `shim/patched/`; regenerate the mirrors with
  `gen_ci_includes.py` after changing them.
- Objects are cached by file existence, not by flags. After changing compiler
  flags, rebuild the affected sources with `--force --only <path>`.
- Keep engine behaviour authentic where possible: fix the port, not the stock
  Lua scripts.
