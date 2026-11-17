# RBLX2016 Wasm port — status

## Milestone: bundled places are playable in the browser

- Bundled `.rbxl`/`.rbxlx` places from `web/places/` are listed automatically
  in the login menu. The original Doomspire smoke test had 3,367 workspace
  parts.
- The 2016 GL renderer starts and renders frames through WebGL2.
- The real 2016 `ControlScript` and `CameraScript` run: WASD moves the avatar,
  right-drag rotates the camera, and physics keeps the character on the map.
- Place scripts run. The stock 2016 core GUI (`StarterScript`, `Topbar`,
  `SettingsHub`, `BackpackScript`, chat, health) loads from `web/content/scripts`
  and renders inside the engine: topbar, Roblox menu/settings pages, Leave /
  Resume / Reset, backpack hotbar and inventory.
- Tools equip through `BackpackScript` (click a hotbar slot or press 1-9);
  HopperBins from 2008-era places appear in the same hotbar and toggle
  `Active`/mouse command through `HopperBin:ToggleSelect`.
- Joint physics works: a runtime-created `Motor` rotates its Part1 to the
  desired angle. Doomspire itself uses welds/snaps, not hinges or motors.
- Default R6 animations are bundled locally in `web/content/animations` and
  play (idle, walk, run, jump, fall, climb, sit, tools).
- Player sounds play through a WebAudio-backed FMOD shim.
- The canvas backing buffer follows the viewport; the engine draws the classic
  cursor and the CSS cursor is hidden while playing.
- The client's own `LoadingScript.lua` runs and draws the loading screen
  (tiled `darkLoadingTexture.png`, rotating `loadingCircle.png`, SourceSans
  text). `rblx_start_place` only brings up the engine and the loading GUI; JS
  keeps the login shell visible until `rblx_loading_gui_ready`, then hides it
  and calls `rblx_load_place`. The stock screen is the only loading UI while
  the place deserializes and stays until the local character exists
  (`game.Loaded`). The browser shell has no loading screen of its own.
- Chat uses the stock `Chat.lua` and `Topbar`. The local Play Solo player is a
  non-guest (positive UserId and the login username), so `ChatMode` is
  `TextAndMenu`, the chat icon is in the topbar, and sending a message renders
  the local chat line. Client-side Lua chat filtering is left off.
- Large places no longer block the browser tab while loading. `rblx_frame`
  pauses rendering while the place Write task holds the DataModel lock; the
  load continues on scheduler threads. Load-task exceptions are caught and
  always set `g_placeLoaded`, so a bad place cannot wedge `LegacyLock`.
  `Attachment` now accepts value objects as children, so newer place files with
  `Vector3Value` attachments deserialize. `main.cpp` logs a `load phases`
  breakdown.
- Pokemon Brick Bronze (258,416 instances / 163,694 Parts) now deserializes and
  reaches `game.Loaded` without the `Attachments can't have children` error;
  the tab stays responsive. The place itself is still broken under the 2016
  scripts (its map stays in storage, no character spawns), which is expected
  for newer content.
- On phones and tablets the shell enables the stock 2016 touch UI: a
  `Thumbstick`/`DPad`/`Thumbpad`, `TouchJump`, touch camera, touch topbar,
  Backpack/chat touch buttons and `ContextActionTouch` actions. Touch devices
  set `UserInputService.TouchEnabled=true` with mouse/keyboard disabled;
  `TouchInputService` receives browser `touchstart/move/end/cancel` through
  `rblx_touch_event`. The canvas has `touch-action:none`, and held touches are
  released on blur/visibility loss. Touchscreen laptops with a fine pointer
  stay on keyboard/mouse.
- Mobile text entry has no virtual-keyboard bridge yet; chat/TextBox input
  needs a physical keyboard. Movement, camera, jump, tools and menus are
  touch-enabled.
- Right-drag camera no longer snaps when the button is pressed: coalesced
  pre-click movement is flushed before MouseButton2, pointer-lock cursor-warp
  deltas are ignored, and the first locked move plus a short post-lock window
  are dropped.
- The stock Settings menu has all four tabs (Settings, Report, Help, Record).
  Record works: **Take Screenshot** downloads a PNG, **Record Video** downloads
  a WebM, the topbar Stop Recording icon follows the recording state, and
  `Print Screen` / `F12` are wired to capture/record. **Fullscreen** (row or
  `F11`) uses the browser Fullscreen API and syncs `GameBasicSettings`. Graphics
  Mode/Quality are the stock controls; they update
  `GameSettings.SavedQualityLevel` and `CRenderSettingsItem.QualityLevel`.
- Offline limits: recorded video is canvas-only (no audio), the Record page's
  YouTube upload option saves locally, and Report Abuse cannot submit to
  Roblox without a backend.
- Menu, drag/drop place loader and WebGL2 check work. The Places list is
  populated from `GET /places`, which reads every `.rbxl`/`.rbxlx` in
  `web/places/`; Refresh re-reads the folder, so games can be swapped without
  editing code. The login page's **Unlock FPS** checkbox clears the engine's
  30 FPS throttle (`DataModel::throttleAt30Fps`) and is remembered in
  localStorage. The in-game screen is the engine canvas only — no HTML hint
  bar, no HTML loading screen and no HTML Leave button; controls are listed on
  the login page. Leaving goes through the engine's Settings → Leave Game
  page, which uses the `Exit` verb.
- `main.cpp` registers a `BoundVerb("Exit")` (the native client provides it in
  `WindowsClient/GameVerbs.cpp`) that asks the browser shell to leave. The shell
  reloads the page instead of re-initializing the engine in place, which avoids
  stale Lua/RunService state on the next join.
- `rbxassetid://` textures, meshes, sounds, animations and raw
  `roblox.com/asset/` URLs referenced by place scripts are fetched through the
  local `/asset/?id=N` proxy, which calls Roblox assetdelivery and caches the
  bytes in `web/asset-cache`. Classic public assets load anonymously; assets
  that now require a session use `ROBLOSECURITY` / `ASSET_PLACE_ID`.
- `HttpService` works: `GetAsync`, `PostAsync`, `JSONEncode`, `JSONDecode`,
  `UrlEncode`, `GenerateGUID`. The place's `HttpEnabled` is forced on for the
  local session; the browser still enforces CORS for cross-origin URLs.
- No networking or multiplayer. `Players`/`Player` are local only.

## Build state

- Emscripten 7.0.0: `/home/raul/emsdk`; Python 3.12: `/home/raul/python312`.
- Boost 1.56 headers + System/Thread/Filesystem objects: `/home/raul/boost_1_56_0`.
- App/Base/Log: 549/550 objects (Server.cpp excluded, not linked).
- Load-path translation units (v8xml, v8tree/Instance, v8world, v8kernel, script,
  Lua 5.1, DataModel/Workspace/PartInstance, Humanoid, Network/Player) and the
  GUI/render stack (GfxRender, GfxCore GL, AppDraw, RbxG3D, Gui*, text layout)
  are compiled with `-O2 -fno-strict-aliasing` via `OPTIMIZED_SOURCES` in
  `port/build/build.py` and unconditionally in `build_gfx.py`; the rest of the
  engine stays `-O0`. A Doomspire load takes ~2.3-3.0 s in-engine (serializer
  ~0.9 s, `joinAllHack` ~0.25 s) instead of ~5 s, and the wasm is ~56 MB.
- Gfx stack: 117/117 (GfxCore GL, GfxRender, GfxBase, AppDraw, freetype, libpng 1.2).
- G3D: 112/112. Bullet: 147/147.
- Network offline subset: Players, Player, NetworkSettings, GuidRegistryService,
  PersistentDataStore, RakNet BitStream/SystemAddress.
- Shader packs: generated by `port/build/build_shaders.py`; the pack post-pass
  fixes GLSL ES 3 dynamic matrix indexing, fragment precision, and SkyVS PSIZE.
- Forward-compatible RBXL parser skips unknown/newer property chunks; Int64
  values are accepted for integer properties.
- WebClient exports: `rblx_start_place`, `rblx_stop`, `rblx_frame`, `rblx_resize`,
  `rblx_has_view`, `rblx_set_camera`, `rblx_frame_count`, `rblx_last_error`,
  `rblx_key_event`, `rblx_mouse_move`, `rblx_mouse_button`, `rblx_mouse_wheel`,
  `rblx_force_character`, plus debug helpers.

## Known gaps

- Lighting/materials are basic; SurfaceType and material textures load, but the
  shading is flat compared with the original client. The default sky is a solid
  image rather than the full skybox.
- Remote content is assetdelivery only. Assets that Roblox marks as requiring
  authentication need a session cookie on the server; main-thread synchronous
  content loads (some `require(assetId)` modules) still cannot fetch.
- No general HTTP/HTTPS networking or multiplayer. Local content still wins for
  `rbxasset://` paths.
- Audio is 2D (no positional 3D panning).
- Some old place scripts reference removed services (`game.Selection`) or newer
  Lua constructors (`ColorSequenceKeypoint`); those scripts log an error and
  the rest of the place keeps running.
- Default player scripts load from `web/content/fonts` and compile locally at
  first join, which takes a few seconds on slow machines.
- Performance is CPU-bound under software WebGL; a hardware GPU is expected for
  smooth play. The renderer has no physics interpolation, so below 60 FPS the
  avatar moves in visible 60 Hz steps.
- `rblx2016.data` is ~112 MB because full engine content + platform textures
  are preloaded.

## Engine notes (port-specific)

- Lua runs the **local-compiler** implementation (`LuaVMDummy.cpp`), not the
  server/client pair. The daemon/VM bytecode keys therefore stay at the
  `LUAVM_*_DUMMY` values and `lua_load` output is finalized with the DAX
  transform before execution. Using `LuaVMServer.cpp` here produces bytecode
  that the interpreter misdecodes (wild jumps and heap corruption).
- `rblx_frame` calls `DataModel::renderStep` under the Write lock before
  rendering; this drives `UserInputService`, `RenderStepped`, the camera and
  the player scripts. Rendering itself runs outside the job arbiter. `dt` is
  clamped to 0.1 s rather than replaced with 1/60 so tweens and animations keep
  real time on slow frames.
- The local player is created with a positive UserId and the login username.
  `Player::setName` requires `WritePlayer`, so the rename runs under
  `Security::Impersonator(Replicator_)`; without it the load Write task throws
  an empty security exception, the scheduler removes the job before the
  `LegacyLock` handshake, and the main thread freezes on the stock loading
  screen with `g_placeLoaded` never set.
- Keyboard, mouse-move and mouse-button events are forwarded to
  `UserInputService::fireInputEvent`; `rblx_force_character` loads the avatar
  through `DataModel::submitTask`. Tools and HopperBins are owned by the stock
  `BackpackScript`; there is no WebClient equip shortcut.
- `WebCoreScripts.cpp` performs the `StudioConfigurer::configure` bootstrap for
  the offline subset: it builds `RobloxGui.Modules` from
  `web/content/scripts/Modules` and starts `ServerStarterScript`/`StarterScript`
  from `DataModel::startCoreScripts`.
- `UseInGameTopBar` is forced to true at startup, matching the desktop client's
  `ClientAppSettings.json`. Without it `Chat.lua` resets the GUI inset to zero
  and the topbar is laid out off-screen, so no topbar click can land.
- `StandardOut` is wired to stderr, so Lua `print`/`warn` and script errors
  appear in the browser console.
- Browser mouse buttons are translated by DOM number: `0` left, `2` right
  (`MouseButton2`), `1` middle (`MouseButton3`). The earlier swapped mapping
  delivered right-clicks as the middle button, so the Lua CameraScript never
  started its right-drag pan.
- `UserAllCamerasInLua` is forced on with `UseInGameTopBar`, matching the 2016
  client flags. Otherwise `Camera::step` keeps running the C++ Follow camera on
  top of the Lua CameraScript.
- Right-drag requests pointer lock so camera turning is not limited by the
  window edge; the first locked move is dropped, Esc/blur releases the button,
  and mouse motion is coalesced to one `rblx_mouse_move` per frame. Blur also
  sends key-up/button-up for everything held, so movement cannot stick.
- `Http::get` performs a synchronous `emscripten_fetch` on the content worker
  pool (never on the browser main thread). `Http` async calls (HttpService) run
  the same sync fetch on a detached boost thread and invoke the yield callback
  from there, matching the original `HttpAsync` threading model.
  `ContentProvider` rewrites
  `rbxassetid://N` and `roblox.com/asset/` to the local origin passed by
  `rblx_start_place`, and skips URL reconstruction so those stay on the proxy.
- `UserInputService::getPlatform()` returns `PLATFORM_WINDOWS` under Emscripten;
  the old `PLATFORM_NONE` result failed Lua enum conversion and killed
  `LoadingScript` before it built its GUI.
- Physics keeps real time despite scheduler jitter: the physics job accumulates
  wall time and runs up to 8 fixed 1/60 steps per wakeup. The engine's
  `EThrottle` auto mode is disabled in `main.cpp` (`ThrottleDisabled`); it was
  reacting to job-timing noise and dropping the simulation rate to as little as
  1/16 even at 60 FPS.
- `HttpService` is patched in `port/src_patch/v8datamodel/HttpService.cpp`:
  the constructor defaults `httpEnabled` to true under Emscripten, the
  `HttpEnabled` property is script-visible, and `checkEverything` skips the
  per-place gate. `main.cpp` also walks the loaded DataModel and sets
  `HttpEnabled` on every serialized HttpService after load.
- The login page has an **Asset delivery cookie** field (password input, so it
  shows as asterisks) with Save/Remove. It posts to `/cookie`; the value is
  written to `asset-cookie.txt` next to `web/` with mode 0600 and never read
  back by the page. `GET /cookie` only reports `{set, source}`. Without the
  header `X-RBX-Cookie: 1` the write endpoints refuse, so other pages cannot
  set it.
- `web/serve.py` proxies `/asset/?id=N` to
  `https://assetdelivery.roblox.com/v1/asset?id=N`, with optional
  `ROBLOSECURITY` / `ASSET_PLACE_ID` and an `ASSET_DELIVERY_BASE` override for
  tests. Downloads are cached in `web/asset-cache/`.
- The WebClient warms the engine module from the login screen
  (`requestIdleCallback` in `web/js/app.js`), so the wasm + content pack load
  while the user picks a place.
- `web/serve.py` sends `Cache-Control: no-cache` (revalidate, not `no-store`) so
  reloads reuse the cached wasm/content and V8's compiled-wasm cache.
- Content files are read fully into memory before XML parsing; seeking the
  VFS stream made the parser fall back to one-byte reads.
- `rbxasset://` content resolves before URL reconstruction, so local models,
  sounds and animations outside the HTTP asset path whitelist load.
- Animation keyframe sequences are accepted from local `rbxasset://` URLs
  (the provider only rewrites HTTP/asset-id URLs).
- `fmod_stubs.cpp` is a WebAudio shim: FMOD calls queue commands on the job
  thread and `rblx_audio_pump()` runs them on the main thread each frame.

## Commands

```sh
# Include mirror (only when headers change; never during a build)
python3 port/tools/gen_ci_includes.py

# Engine objects
python3 port/build/build.py --project all --jobs 2
python3 port/build/build_network.py --jobs 2
python3 port/build/build_gfx.py --jobs 2
python3 port/build/build_g3d.py --jobs 2
python3 port/build/build_bullet.py --jobs 2
python3 port/build/build_extras.py --force

# Shader packs (no-opt ShaderCompiler; set SHADER_COMPILER if moved)
python3 port/build/build_shaders.py

# Link + preload content/shaders
python3 port/build/link.py

# Serve with COOP/COEP for pthreads
python3 web/serve.py 8080

# Optional: download assets that require a Roblox session
export ROBLOSECURITY='...'      # or write the value to asset-cookie.txt
export ASSET_PLACE_ID=1818      # optional, for place-scoped assets
python3 web/serve.py 8080
```

Open `http://127.0.0.1:8080/`, pick a bundled place (or drop a `.rbxl` into
`web/places/` and press Refresh), press Play.
