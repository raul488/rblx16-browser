# RBLX2016 the 2016 Roblox client in a browser tab

This repository compiles the 2016 Roblox C++ client to WebAssembly and runs it
in the browser. It is a single-player, offline build: you pick a local
`.rbxl`/`.rbxlx` place, the real 2016 engine loads it, and the original core
GUI, physics, animations and tools come with it. There is no account, no
multiplayer and no Roblox connection.

## Play

```sh
python3 web/serve.py 8080
# open http://127.0.0.1:8080/
```

Pick a bundled place (or open any `.rbxl`/`.rbxlx` from your disk), press
**Play**. The first load pulls a ~60 MB WebAssembly module and the ~110 MB
content pack (delivered in parts), so give it a moment; afterwards the browser
caches them.

The local server sets the cross-origin isolation headers the threaded engine
needs; `web/coi-serviceworker.js` does the same from a service worker when the
files are hosted statically, and `web/places/manifest.json` supplies the place
list there.

## Controls

| input | action |
|---|---|
| WASD / arrow keys | move |
| Space | jump |
| Right-drag | rotate the camera |
| Mouse wheel | zoom |
| 1–9 | equip/holster a backpack tool |
| Esc | release the mouse, open the menu |
| F11 | fullscreen |
| F12 | start/stop video recording |
| Print Screen | save a screenshot |

Phones and tablets get the original 2016 touch controls: thumbstick, jump
button, touch camera and touch tool buttons. Text entry (chat, TextBox) still
needs a physical keyboard.

## Hosting

`web/` is also published as a static site from the `gh-pages` branch. Static
hosts cannot send the COOP/COEP headers the threaded engine needs, so
`coi-serviceworker.js` provides cross-origin isolation, and the page cannot
read the asset cookie itself. To load Roblox-hosted avatars and textures there,
deploy [`port/worker/asset-proxy.js`](port/worker/asset-proxy.js) as a
Cloudflare Worker with a `ROBLOSECURITY_COOKIE` secret and point
`web/asset-proxy.json` at it (`{"base": "https://<worker>"}`). Without the
proxy the site still lists places and plays them, just with missing remote
assets.

## Features

- The 2016 engine and Lua core GUI themselves: topbar, settings, chat,
  backpack, health, loading screen, Leave/Resume.
- Real physics, joints, R6 animations, tools and HopperBins.
- Places from `web/places/` are listed automatically; drop in a file and press
  **Refresh**.
- An avatar catalog with 2008–2016 classic clothing (shirts, t-shirts, pants)
  from Roblox, armitroner and the era's designers — VendettaTheGreat, Plad,
  Keletho, GPR3, 1UK, Shaddic, ibloxu20 — plus hats, hair, shoulder and back
  accessories, classic heads and faces, and body colours. Worn items persist in
  the browser and are applied to your character. Item meshes and textures come
  from Roblox: the local server with a saved `.ROBLOSECURITY` cookie is the
  default source, and a static host can use the Worker proxy instead (see
  [Hosting](#hosting)).
- Settings page: graphics mode/quality, fullscreen, camera/movement mode,
  mouse sensitivity, volume, screenshots and recording.
- Optional "Unlock FPS" switch on the menu; the stock client caps at 30 FPS.
- Renderer compatibility switches on the menu (2D light grid, no light grid,
  GLES2 shader pack) plus an on-screen diagnostics panel with the renderer
  name, GL version and texture limits.

## Known limitations

- No networking: Players and Player are local, no multiplayer, no server.
- Remote Roblox assets need a working asset source: the local `/asset` proxy
  with a `.ROBLOSECURITY` cookie, or the Worker proxy on a static host. Without
  one, catalogs still list and places still load, but Roblox-hosted meshes and
  textures are missing.
- Some places ship scripts that were written for newer Roblox or depend on
  Studio-only modules; those games load but their own logic can fail. The tab
  stays responsive.
- Video recording is canvas-only (no audio) and uploads nothing.
- No positional audio, no sub-60 FPS interpolation, no skybox.
- Software WebGL (e.g. SwiftShader in headless browsers) is very slow and is
  not representative of real hardware.

## Repository layout

| path | contents |
|---|---|
| `roblox-2016-source-code-master/` | The 2016 engine source tree, used read-only. |
| `port/` | Browser platform layer, build scripts, patches, scrapers. |
| `web/` | The site: page, engine build output, content pack, places, catalog. |

Building from source is described in [`port/README.md`](port/README.md), and
the build/serve commands are in [`port/COMMANDS.md`](port/COMMANDS.md).

## Credits

- Built with [OpenCode](https://opencode.ai) and DeepSeek v4.1 1017.
- The 2016 Roblox source tree in `roblox-2016-source-code-master/` comes from
  <https://github.com/SANS3R66/roblox-2016-source-code>.

## Legal

Roblox, the Roblox logo and Roblox assets belong to Roblox Corporation. This
is an unofficial, non-commercial preservation and engineering project; it is
not affiliated with or endorsed by Roblox. Only run place files and content you
have the right to use.
