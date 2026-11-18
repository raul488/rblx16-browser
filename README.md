# RBLX2016 — the 2016 Roblox client in a browser tab

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

## Features

- The 2016 engine and Lua core GUI themselves: topbar, settings, chat,
  backpack, health, loading screen, Leave/Resume.
- Real physics, joints, R6 animations, tools and HopperBins.
- Places from `web/places/` are listed automatically; drop in a file and press
  **Refresh**.
- An avatar catalog with 2016-era shirts, t-shirts, pants, hats, hair,
  shoulder and back accessories and classic faces, plus body colours. Worn
  items persist in the browser and are applied to your character.
- Settings page: graphics mode/quality, fullscreen, camera/movement mode,
  mouse sensitivity, volume, screenshots and recording.
- Optional "Unlock FPS" switch on the menu; the stock client caps at 30 FPS.

## Known limitations

- No networking: Players and Player are local, no multiplayer, no server.
- Remote Roblox assets need the local `/asset` proxy and, for user-uploaded
  items, a `.ROBLOSECURITY` cookie saved on the menu. On a static host that
  proxy does not exist.
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
