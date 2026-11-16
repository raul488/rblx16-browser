/* Browser shell for the 2016 Roblox engine port.
 * The menu is intentionally small; the engine owns the game itself. */
(function () {
  "use strict";

  const MAX_PLACE_BYTES = 32 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);

  const builtIn = [
    {
      id: "doomspire",
      name: "Doomspire Brickbattle",
      meta: "2016 classic · 4 teams · brick battle",
      src: "places/Doomspire Brickbattle.rbxl",
      bytes: null,
    },
  ];

  let places = builtIn.slice();
  let selected = places[0];
  let engine = null;
  let playing = false;
  let modulePromise = null;
  let rafId = 0;
  const canvas = $("gameCanvas");
  function resizeCanvas() {
    if (!engine || !playing) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w > 0 && h > 0) {
      try { engine.ccall("rblx_resize", null, ["number", "number"], [w, h]); } catch (_) {}
    }
  }

  const login = $("login");
  const placeList = $("placeList");
  const status = $("status");
  const playBtn = $("playBtn");
  const openFile = $("openFile");
  const userName = $("userName");
  const loading = $("loadingBar");
  const loadingText = $("loadingText");
  const loadingTitle = $("loadingTitle");
  const fatal = $("fatal");
  const fatalText = $("fatalText");

  function setStatus(msg, isError) {
    status.textContent = msg || "";
    status.classList.toggle("err", !!isError);
  }

  function sanitizeName(raw) {
    let s = String(raw || "").replace(/[^A-Za-z0-9 _]/g, "").replace(/\s+/g, " ").trim();
    if (s.length > 20) s = s.slice(0, 20);
    return s || "Player1";
  }

  function saveName() {
    const name = sanitizeName(userName.value);
    userName.value = name;
    try { localStorage.setItem("rblx2016.name", name); } catch (_) {}
    return name;
  }

  function renderPlaces() {
    placeList.innerHTML = "";
    for (const p of places) {
      const row = document.createElement("div");
      row.className = "pl" + (p === selected ? " sel" : "");
      row.tabIndex = 0;
      row.innerHTML = '<span class="nm"></span><span class="meta"></span>';
      row.querySelector(".nm").textContent = p.name;
      row.querySelector(".meta").textContent = p.meta || "";
      row.addEventListener("click", () => {
        selected = p;
        renderPlaces();
        setStatus("");
      });
      row.addEventListener("dblclick", () => play());
      row.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { selected = p; renderPlaces(); play(); }
      });
      placeList.appendChild(row);
    }
    playBtn.disabled = !selected || !!$("webglWarn").offsetParent;
  }

  async function readFileBytes(p) {
    if (p.bytes) return p.bytes;
    if (!p.src) throw new Error("No place data.");
    const r = await fetch(p.src);
    if (!r.ok) throw new Error("Could not read " + p.src + " (" + r.status + ")");
    p.bytes = new Uint8Array(await r.arrayBuffer());
    return p.bytes;
  }

  function openLocalFile(file) {
    if (!file) return;
    if (file.size > MAX_PLACE_BYTES) {
      setStatus("Place file is too large (limit " + (MAX_PLACE_BYTES / 1024 / 1024) + " MB).", true);
      return;
    }
    const reader = new FileReader();
    reader.onerror = () => setStatus("Could not read the file.", true);
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      const p = {
        id: "local:" + file.name,
        name: file.name.replace(/\.(rbxlx?|rbxm|xml)$/i, ""),
        meta: "local file · " + (bytes.length / 1024).toFixed(0) + " KB",
        bytes,
      };
      const old = places.findIndex((x) => x.id === p.id);
      if (old >= 0) places.splice(old, 1);
      places.unshift(p);
      selected = p;
      renderPlaces();
      setStatus("Loaded " + file.name + " — press Play.");
    };
    reader.readAsArrayBuffer(file);
  }

  function afterPaint() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  async function ensureModule() {
    if (engine) return engine;
    if (!modulePromise) {
      modulePromise = (async () => {
        if (typeof window.createRBLX2016Module !== "function") {
          await new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = "js/rblx2016.js";
            s.onload = resolve;
            s.onerror = () => reject(new Error(
              "The 2016 engine module (js/rblx2016.js) is not present yet. " +
              "Build it with port/build/build-wasm.sh, then reload this page."));
            document.head.appendChild(s);
          });
        }
        if (typeof window.createRBLX2016Module !== "function") {
          throw new Error("js/rblx2016.js loaded, but createRBLX2016Module is missing.");
        }
        const mod = await window.createRBLX2016Module({
          canvas: $("gameCanvas"),
          locateFile: (path, prefix) => {
            if (prefix) return prefix + path;
            return new URL("js/" + path, location.href).href;
          },
          preRun: [() => console.log("[rbx2016] preRun")],
          onRuntimeInitialized: () => console.log("[rbx2016] runtime initialized"),
          print: (t) => console.log("[rbx2016]", t),
          printErr: (t) => console.warn("[rbx2016]", t),
        });
        if (typeof mod._rblx_test_thread === "function") {
          console.log("[rbx2016] thread test:", mod.ccall("rblx_test_thread", "number", [], []));
        }
        return mod;
      })().catch((e) => { modulePromise = null; throw e; });
    }
    engine = await modulePromise;
    return engine;
  }

  function showFatal(err) {
    playing = false;
    if (loading.__ticker) { clearInterval(loading.__ticker); loading.__ticker = null; }
    loading.style.display = "none";
    login.style.display = "block";
    fatalText.textContent = err && err.stack ? err.stack : String(err || "Unknown error");
    fatal.style.display = "flex";
  }

  async function play() {
    if (playing) return;
    if (!selected) { setStatus("Choose a place first.", true); return; }
    const name = saveName();
    playing = true;
    fatal.style.display = "none";
    setStatus("");
    loadingTitle.textContent = selected.name;
    const loadingCreator = $("loadingCreator");
    const loadingMessage = $("loadingMessage");
    if (loadingCreator) loadingCreator.textContent = selected.bytes ? "Local file" : "Roblox";
    if (loadingMessage) loadingMessage.textContent = "Starting the 2016 engine…";
    if (loading.__ticker) { clearInterval(loading.__ticker); loading.__ticker = null; }
    let loadingDots = 0;
    if (loadingText) loadingText.textContent = "Loading";
    const loadingTicker = setInterval(() => {
      loadingDots = (loadingDots + 1) % 4;
      if (loadingText) loadingText.textContent = "Loading" + ".".repeat(loadingDots);
    }, 450);
    loading.__ticker = loadingTicker;
    loading.style.display = "flex";
    login.style.display = "none";
    await afterPaint();

    try {
      const bytes = await readFileBytes(selected);
      if (loadingMessage) loadingMessage.textContent = "Loading place (" + (bytes.length / 1024).toFixed(0) + " KB)…";
      const mod = await ensureModule();
      if (typeof mod._rblx_start_place !== "function") {
        throw new Error(
          "The engine module is built, but rblx_start_place is not exported yet. " +
          "This is the WebClient integration step.");
      }
      const ptr = mod._malloc(bytes.length);
      if (!ptr) throw new Error("Out of Wasm memory while loading the place.");
      mod.HEAPU8.set(bytes, ptr);
      // rbxassetid:// resolves to <origin>/asset/?id=N; serve.py proxies that
      // to assetdelivery and caches the bytes.
      const rc = mod.ccall("rblx_start_place", "number",
        ["number", "number", "string", "string"], [ptr, bytes.length, name, location.origin + "/"]);
      mod._free(ptr);
      if (rc !== 0) throw new Error("rblx_start_place returned " + rc);
      const hideSplash = () => {
        loading.style.display = "none";
        if (loading.__ticker) { clearInterval(loading.__ticker); loading.__ticker = null; }
        loadingText.textContent = "Loading";
        if (loadingMessage) loadingMessage.textContent = "";
      };
      if (typeof mod._rblx_has_view === "function" && mod.ccall("rblx_has_view", "number", [], [])) {
        const frame = () => {
          if (!playing) return;
          if (canvas.__flushMouse) canvas.__flushMouse();
          try { mod.ccall("rblx_frame", null, [], []); } catch (e) { console.warn(e); }
          rafId = requestAnimationFrame(frame);
        };
        installInput(mod);
        resizeCanvas();
        if (!canvas.__resizeObserved && typeof ResizeObserver !== "undefined") {
          canvas.__resizeObserved = true;
          new ResizeObserver(resizeCanvas).observe(canvas);
        }
        rafId = requestAnimationFrame(frame);
        // Let the engine's LoadingScript build and paint its GUI behind the
        // HTML splash, then deserialize the place. The stock screen stays up
        // until the local character exists.
        const startLoad = (tries) => {
          let ready = 0;
          try { ready = mod.ccall("rblx_loading_gui_ready", "number", [], []); } catch (_) {}
          if (!ready && tries > 0) {
            setTimeout(() => startLoad(tries - 1), 100);
            return;
          }
          const begin = () => {
            // Reveal the engine's own loading GUI before the deserialize blocks
            // the main thread. If the stock GUI never appeared, keep the HTML
            // splash until after the blocking parse.
            if (ready) hideSplash();
            try { mod.ccall("rblx_load_place", "number", [], []); } catch (e) { console.warn(e); }
            if (!ready) setTimeout(hideSplash, 4000);
            // Fallback only; the engine normally spawns the avatar itself and
            // the LoadingScript stays up until then.
            setTimeout(() => { try { mod.ccall("rblx_force_character", null, [], []); } catch (_) {} }, 6000);
          };
          // Let the tiled background and spinner textures load/paint first.
          if (ready) setTimeout(begin, 900);
          else begin();
        };
        startLoad(25);
      } else {
        try { mod.ccall("rblx_load_place", "number", [], []); } catch (_) {}
        hideSplash();
        setStatus("Place loaded, but the renderer did not start (headless engine).", true);
      }
      console.log("[rbx2016] place started");
    } catch (e) {
      showFatal(e);
    }
  }

  const keysDown = new Set();
  function modFromKeys() {
    let m = 0;
    if (keysDown.has("shift")) m |= 1;       // KMOD_LSHIFT
    if (keysDown.has("control")) m |= 0x40;  // KMOD_LCTRL
    if (keysDown.has("alt")) m |= 0x100;     // KMOD_LALT
    return m;
  }
  function engineKeyCode(e) {
    switch (e.key) {
      case "ArrowUp": return 273;
      case "ArrowDown": return 274;
      case "ArrowRight": return 275;
      case "ArrowLeft": return 276;
      case "Shift": return e.location === 2 ? 303 : 304;
      case "Control": return e.location === 2 ? 305 : 306;
      case "Alt": return e.location === 2 ? 307 : 308;
      case " ": return 32;
      case "Enter": return 13;
      case "Tab": return 9;
      case "Backspace": return 8;
      case "Escape": return 27;
      default: break;
    }
    if (e.key.length === 1) return e.key.toLowerCase().charCodeAt(0);
    return 0;
  }
  function installInput(mod) {
    if (canvas.__inputInstalled) return;
    canvas.__inputInstalled = true;
    const ccall = (fn, args) => { try { mod.ccall(fn, null, ["number","number","number","number"], args); } catch (_) {} };
    const toCanvas = (e) => {
      const r = canvas.getBoundingClientRect();
      const sx = canvas.width / Math.max(1, r.width);
      const sy = canvas.height / Math.max(1, r.height);
      return [Math.round((e.clientX - r.left) * sx), Math.round((e.clientY - r.top) * sy), sx, sy];
    };
    const updateCursor = () => {
      // The engine draws the 2016 cursor (arrow, hand, tool icons) itself.
      canvas.style.cursor = playing ? "none" : "default";
    };

    // Right-drag captures the pointer like the desktop client: the OS cursor
    // stops at window edges, so without capture camera turning would stick.
    let locked = false;
    let rightDown = false;
    let skipLockedMove = false;
    const lockedPos = { x: 0, y: 0 };
    const heldKeys = new Map();   // key -> { code, ch }
    const heldButtons = new Set();
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    // Mouse motion is coalesced into one engine event per frame, the way the
    // native client processes one mouse message per frame.
    let pending = { x: 0, y: 0, dx: 0, dy: 0, has: false };
    const flushMouse = () => {
      if (!pending.has) return;
      ccall("rblx_mouse_move", [pending.x, pending.y, pending.dx, pending.dy]);
      pending.dx = 0; pending.dy = 0; pending.has = false;
    };
    canvas.__flushMouse = flushMouse;

    const releaseAll = () => {
      for (const [, h] of heldKeys) ccall("rblx_key_event", [h.code, 0, h.ch, 0]);
      heldKeys.clear();
      keysDown.clear();
      for (const b of heldButtons) ccall("rblx_mouse_button", [b, lockedPos.x, lockedPos.y, 0]);
      heldButtons.clear();
      rightDown = false;
      if (document.pointerLockElement === canvas) document.exitPointerLock();
    };
    canvas.__releaseInput = releaseAll;

    document.addEventListener("pointerlockchange", () => {
      locked = document.pointerLockElement === canvas;
      // The first event after capture can carry the delta from the click
      // position to the capture origin; ignore it once.
      skipLockedMove = locked;
      updateCursor();
      if (!locked && rightDown) {
        rightDown = false;
        ccall("rblx_mouse_button", [2, lockedPos.x, lockedPos.y, 0]);
      }
    });
    updateCursor();
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    document.addEventListener("mousemove", (e) => {
      if (!playing) return;
      const p = toCanvas(e);
      let dx = Math.round(e.movementX * p[2]);
      let dy = Math.round(e.movementY * p[3]);
      if (locked && skipLockedMove) { skipLockedMove = false; dx = 0; dy = 0; }
      let x = p[0], y = p[1];
      if (locked) {
        lockedPos.x = clamp(lockedPos.x + dx, 0, canvas.width - 1);
        lockedPos.y = clamp(lockedPos.y + dy, 0, canvas.height - 1);
        x = lockedPos.x; y = lockedPos.y;
      } else {
        lockedPos.x = x; lockedPos.y = y;
      }
      pending.x = x; pending.y = y; pending.dx += dx; pending.dy += dy; pending.has = true;
    });
    canvas.addEventListener("mousedown", (e) => {
      if (!playing) return;
      const p = toCanvas(e);
      heldButtons.add(e.button);
      if (e.button === 2) {
        rightDown = true;
        lockedPos.x = p[0]; lockedPos.y = p[1];
        try {
          const req = canvas.requestPointerLock();
          if (req && req.catch) req.catch(() => {});
        } catch (_) {}
      }
      ccall("rblx_mouse_button", [e.button, p[0], p[1], 1]);
    });
    window.addEventListener("mouseup", (e) => {
      if (!playing) return;
      const p = locked ? [lockedPos.x, lockedPos.y] : toCanvas(e);
      heldButtons.delete(e.button);
      if (e.button === 2) {
        rightDown = false;
        if (document.pointerLockElement === canvas) document.exitPointerLock();
      }
      ccall("rblx_mouse_button", [e.button, p[0], p[1], 0]);
    });
    canvas.addEventListener("wheel", (e) => {
      if (!playing) return;
      e.preventDefault();
      const p = toCanvas(e);
      ccall("rblx_mouse_wheel", [p[0], p[1], e.deltaY < 0 ? 1 : -1]);
    }, { passive: false });
    window.addEventListener("keydown", (e) => {
      if (!playing) return;
      const k = e.key.toLowerCase();
      if ([" ","arrowup","arrowdown","arrowleft","arrowright","tab"].includes(k)) e.preventDefault();
      if (keysDown.has(k)) return;
      keysDown.add(k);
      const code = engineKeyCode(e);
      const ch = e.key.length === 1 ? e.key.charCodeAt(0) : 0;
      if (code) {
        heldKeys.set(k, { code, ch });
        ccall("rblx_key_event", [code, modFromKeys(), ch, 1]);
      }
    });
    window.addEventListener("keyup", (e) => {
      if (!playing) return;
      const k = e.key.toLowerCase();
      keysDown.delete(k);
      const h = heldKeys.get(k);
      heldKeys.delete(k);
      const code = h ? h.code : engineKeyCode(e);
      const ch = h ? h.ch : (e.key.length === 1 ? e.key.charCodeAt(0) : 0);
      if (code) ccall("rblx_key_event", [code, modFromKeys(), ch, 0]);
    });
    window.addEventListener("blur", releaseAll);
    document.addEventListener("visibilitychange", () => { if (document.hidden) releaseAll(); });
  }

  function stop() {
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    if (loading.__ticker) { clearInterval(loading.__ticker); loading.__ticker = null; }
    if (canvas.__releaseInput) canvas.__releaseInput();
    try {
      if (engine && typeof engine._rblx_stop === "function") {
        engine.ccall("rblx_stop", null, [], []);
      }
    } catch (e) { console.warn(e); }
    playing = false;
    loading.style.display = "none";
    login.style.display = "block";
    fatal.style.display = "none";
    setStatus("");
  }

  function hasWebGL2() {
    try {
      const c = document.createElement("canvas");
      return !!(window.WebGL2RenderingContext && c.getContext("webgl2"));
    } catch (_) { return false; }
  }

  function showCookieStatus(status) {
    const state = $("cookieState");
    if (!state) return;
    if (!status) { state.textContent = "status unavailable"; return; }
    if (!status.set) { state.textContent = "not set"; return; }
    state.textContent = "set " + "*".repeat(12) + (status.source ? " · " + status.source : "");
  }

  async function cookieRequest(method, value) {
    const options = { method: method, cache: "no-store", headers: { "X-RBX-Cookie": "1" } };
    if (value !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify({ value: value });
    }
    const response = await fetch("cookie", options);
    if (!response.ok) throw new Error("cookie request failed");
    return await response.json();
  }

  function installCookieUi() {
    const input = $("cookieInput");
    const save = $("cookieSave");
    const clear = $("cookieClear");
    if (!input || !save || !clear) return;
    fetch("cookie", { cache: "no-store" }).then(r => r.json()).then(showCookieStatus).catch(() => showCookieStatus(null));
    save.addEventListener("click", async () => {
      const value = input.value.trim();
      if (!value) { setStatus("Paste the .ROBLOSECURITY value first.", true); return; }
      try {
        const status = await cookieRequest("POST", value);
        input.value = "";
        showCookieStatus(status);
        setStatus("Asset cookie saved. Remote assets load on the next join.");
      } catch (_) {
        setStatus("Could not save the asset cookie.", true);
      }
    });
    clear.addEventListener("click", async () => {
      try {
        const status = await cookieRequest("DELETE");
        input.value = "";
        showCookieStatus(status);
        setStatus("Asset cookie removed.");
      } catch (_) {
        setStatus("Could not remove the asset cookie.", true);
      }
    });
  }

  function boot() {
    try { userName.value = localStorage.getItem("rblx2016.name") || "Player1"; } catch (_) {}
    userName.addEventListener("input", saveName);
    userName.addEventListener("blur", saveName);
    openFile.addEventListener("change", () => openLocalFile(openFile.files[0]));
    $("openFileBtn").addEventListener("click", () => openFile.click());
    playBtn.addEventListener("click", play);
    $("fatalBack").addEventListener("click", () => { location.reload(); });
    login.addEventListener("dragover", (e) => { e.preventDefault(); login.classList.add("drag"); });
    login.addEventListener("dragleave", () => login.classList.remove("drag"));
    login.addEventListener("drop", (e) => {
      e.preventDefault();
      login.classList.remove("drag");
      openLocalFile(e.dataTransfer.files[0]);
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("drop", (e) => e.preventDefault());
    window.addEventListener("resize", resizeCanvas);
    window.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !playing && document.activeElement === userName) play();
    });

    if (!hasWebGL2()) {
      $("webglWarn").style.display = "block";
      playBtn.disabled = true;
    }
    renderPlaces();
    installCookieUi();
    window.RBLX2016_MENU = { play, stop, places };
    // The engine menu's Leave button (Exit verb) calls this. Reload instead of
    // re-initializing the engine in place: the native client also leaves the
    // game client entirely, and a fresh page avoids stale Lua/RunService state.
    window.rblxLeaveGame = () => { location.reload(); };

    // Warm the 70 MB wasm + 107 MB content pack while the user is still on the
    // menu, so pressing Play only pays for the place itself.
    if (!playBtn.disabled) {
      const warm = () => { ensureModule().catch(() => {}); };
      if ("requestIdleCallback" in window) requestIdleCallback(warm, { timeout: 2500 });
      else setTimeout(warm, 500);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
