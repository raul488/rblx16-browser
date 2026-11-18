# Commands

```sh
# Case-insensitive include mirror (only when headers change)
python3 port/tools/gen_ci_includes.py

# Engine objects
python3 port/build/build.py --project all --jobs 2
python3 port/build/build_network.py --jobs 2
python3 port/build/build_gfx.py --jobs 2
python3 port/build/build_g3d.py --jobs 2
python3 port/build/build_bullet.py --jobs 2
python3 port/build/build_extras.py --force

# Shader packs (needs the Linux ShaderCompiler; set SHADER_COMPILER if moved)
python3 port/build/build_shaders.py

# Link, preload web/content and web/shaders, write web/places/manifest.json
python3 port/build/link.py

# Serve with the COOP/COEP headers the threaded engine needs
python3 web/serve.py 8080

# Optional: avatar catalog (web/catalog, gitignored)
python3 port/tools/fetch_catalog.py

# Optional: assets that need a Roblox session
export ROBLOSECURITY='...'   # or save it on the menu / in asset-cookie.txt
export ASSET_PLACE_ID=1818   # optional, for place-scoped assets
python3 web/serve.py 8080
```

Open `http://127.0.0.1:8080/`, pick a bundled place (or drop a `.rbxl` into
`web/places/` and press Refresh), press Play.
