/* Browser shell for the 2016 Roblox engine port.
 * The menu is intentionally small; the engine owns the game itself. */
(function () {
  "use strict";

  const MAX_PLACE_BYTES = 32 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);

  // Phones/tablets use the stock 2016 touch controls; a touchscreen laptop
  // with a fine pointer stays on keyboard + mouse.
  const touchDevice =
    /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "") ||
    (navigator.maxTouchPoints > 0 &&
      ((window.matchMedia && window.matchMedia("(pointer: coarse)").matches) ||
       /Macintosh/i.test(navigator.userAgent || "")));

  let places = [];
  let selected = null;
  let engine = null;
  let playing = false;
  let modulePromise = null;
  let rafId = 0;
  let screenshotPending = false;
  let mediaRecorder = null;
  let recordChunks = [];
  let recordStream = null;
  const canvas = $("gameCanvas");
  function resizeCanvas() {
    if (!engine || !playing) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w > 0 && h > 0) {
      try { engine.ccall("rblx_resize", null, ["number", "number"], [w, h]); } catch (_) {}
    }
  }

  const login = $("login");
  const placeList = $("placeList");  const status = $("status");
  const playBtn = $("playBtn");
  const openFile = $("openFile");
  const userName = $("userName");
  const unlockFps = $("unlockFps");
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

  // The menu tiles show the year the place title names, if it names one.
  function placeYear(name) {
    const m = /(?:^|\D)((?:19|20)\d{2})(?!\d)/.exec(name || "");
    return m ? m[1] : "";
  }

  function renderPlaces() {
    placeList.innerHTML = "";
    for (const p of places) {
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "place";
      tile.setAttribute("aria-pressed", p === selected ? "true" : "false");
      tile.title = p.name;
      const name = document.createElement("span");
      name.className = "nm";
      name.textContent = p.name;
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = [p.kind === "local" ? "local file" : "", placeYear(p.name), formatSize(p.size)]
        .filter(Boolean).join(" \u00b7 ");
      tile.append(name, sub);
      tile.dataset.id = p.id;
      tile.addEventListener("click", () => {
        selected = p;
        renderPlaces();
        setStatus("");
        // Rebuilding the list drops focus; keep keyboard users on the tile.
        const current = [...placeList.children].find((el) => el.dataset.id === p.id);
        if (current) current.focus();
      });
      tile.addEventListener("dblclick", () => play());
      placeList.appendChild(tile);
    }
    $("placeCount").textContent = places.length
      ? places.length + (places.length === 1 ? " place" : " places") : "no places";
    // The button names the target so a mis-click cannot start the wrong game.
    playBtn.textContent = selected ? "Play " + selected.name : "Play";
    playBtn.title = selected ? "Play " + selected.name : "";
    playBtn.disabled = !selected || !!$("webglWarn").offsetParent;
  }

  function formatSize(bytes) {
    return bytes >= 1048576
      ? (bytes / 1048576).toFixed(1) + " MB"
      : Math.max(1, Math.round(bytes / 1024)) + " KB";
  }

  async function loadPlaces() {
    try {
      const list = await fetchPlaceList();
      const bundled = (Array.isArray(list) ? list : []).map((p) => ({
        id: "place:" + p.file,
        name: p.name,
        kind: "bundled",
        size: p.bytes,
        src: "places/" + encodeURIComponent(p.file),
        bytes: null,
      }));
      const locals = places.filter((p) => p.local);
      const previous = selected && selected.id;
      places.splice(0, places.length, ...locals, ...bundled);
      selected = places.find((p) => p.id === previous) || places[0] || null;
      renderPlaces();
      if (!places.length) {
        setStatus("No places in web/places/. Drop in a .rbxl file or open one locally.", true);
      } else {
        setStatus("");
      }
    } catch (e) {
      setStatus("Could not list web/places/. Open a local place file instead.", true);
    }
  }

  // web/serve.py answers /places by scanning web/places; static hosts (GitHub
  // Pages) use places/manifest.json, which link.py regenerates on every build.
  async function fetchPlaceList() {
    for (const url of ["places", "places/manifest.json"]) {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) continue;
        const list = await response.json();
        if (Array.isArray(list)) return list;
      } catch (_) {}
    }
    throw new Error("no place list");
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
        kind: "local",
        size: bytes.length,
        bytes,
        local: true,
      };
      const old = places.findIndex((x) => x.id === p.id);
      if (old >= 0) places.splice(old, 1);
      places.unshift(p);
      selected = p;
      renderPlaces();
      setStatus("Loaded " + file.name + ". Press Play.");
    };
    reader.readAsArrayBuffer(file);
  }

  function afterPaint() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  function captureCanvas() {
    canvas.toBlob((blob) => {
      if (blob) downloadBlob(blob, "roblox-2016.png");
    }, "image/png");
  }

  function startRecording() {
    if (mediaRecorder) return true;
    if (typeof MediaRecorder === "undefined" || typeof canvas.captureStream !== "function") {
      console.warn("Video recording is not supported in this browser.");
      return false;
    }
    try {
      recordStream = canvas.captureStream(30);
      let mime = "video/webm";
      if (MediaRecorder.isTypeSupported("video/webm;codecs=vp9")) mime = "video/webm;codecs=vp9";
      else if (MediaRecorder.isTypeSupported("video/webm;codecs=vp8")) mime = "video/webm;codecs=vp8";
      mediaRecorder = new MediaRecorder(recordStream, { mimeType: mime });
      recordChunks = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size) recordChunks.push(e.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(recordChunks, { type: "video/webm" });
        recordChunks = [];
        if (recordStream) {
          recordStream.getTracks().forEach((track) => track.stop());
          recordStream = null;
        }
        mediaRecorder = null;
        if (blob.size) downloadBlob(blob, "roblox-2016.webm");
      };
      mediaRecorder.start();
      return true;
    } catch (e) {
      console.warn("Could not start video recording", e);
      if (recordStream) {
        recordStream.getTracks().forEach((track) => track.stop());
        recordStream = null;
      }
      mediaRecorder = null;
      return false;
    }
  }

  function stopRecording() {
    if (!mediaRecorder) return false;
    if (mediaRecorder.state !== "inactive") mediaRecorder.stop();
    return true;
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch((e) => {
        console.warn("Fullscreen request failed", e);
      });
    }
  }

  function installFullscreenSync(mod) {
    if (window.__rblxFullscreenSync) return;
    window.__rblxFullscreenSync = true;
    document.addEventListener("fullscreenchange", () => {
      try {
        mod.ccall("rblx_set_fullscreen", null, ["number"], [document.fullscreenElement ? 1 : 0]);
      } catch (_) {}
    });
  }

  window.rblxScreenshot = () => { screenshotPending = true; };
  window.rblxRecordStart = startRecording;
  window.rblxRecordStop = stopRecording;
  window.rblxToggleFullscreen = toggleFullscreen;

  // link.py splits the 110 MB preloaded content pack into parts under 100 MB,
  // the largest file a static host like GitHub Pages accepts. Reassemble it
  // into a Blob the Emscripten loader fetches as if it were rblx2016.data.
  async function preloadDataPackage() {
    try {
      const listing = await fetch("js/rblx2016.data.parts.json", { cache: "no-store" });
      if (!listing.ok) return null;
      const parts = (await listing.json()).parts;
      if (!Array.isArray(parts) || !parts.length) return null;
      const buffers = await Promise.all(parts.map(async (part) => {
        const response = await fetch(part);
        if (!response.ok) throw new Error("missing " + part);
        return response.arrayBuffer();
      }));
      return URL.createObjectURL(new Blob(buffers));
    } catch (e) {
      console.warn("[rbx2016] using the single data package:", e.message);
      return null;
    }
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
        if (typeof SharedArrayBuffer === "undefined") {
          throw new Error(
            "This page is not cross-origin isolated, so the threaded engine cannot start. " +
            "Serve it with web/serve.py, or over HTTPS where coi-serviceworker.js can " +
            "register (GitHub Pages).");
        }
        const dataUrl = await preloadDataPackage();
        const mod = await window.createRBLX2016Module({
          canvas: $("gameCanvas"),
          locateFile: (path, prefix) => {
            if (dataUrl && path.endsWith(".data")) return dataUrl;
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
        // Log the renderer, so a software rasterizer is recognisable in bug
        // reports.
        try {
          const canvas = $("gameCanvas");
          const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
          const info = gl && gl.getExtension("WEBGL_debug_renderer_info");
          console.log("[rbx2016] webgl:", !gl ? "none" : (info
            ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
            : gl.getParameter(gl.RENDERER)));
        } catch (e) {
          console.log("[rbx2016] webgl: unavailable", e && e.message);
        }
        return mod;
      })().catch((e) => { modulePromise = null; throw e; });
    }
    engine = await modulePromise;
    installFullscreenSync(engine);
    return engine;
  }

  function showFatal(err) {
    playing = false;
    playBtn.disabled = !hasWebGL2();
    login.style.pointerEvents = "";
    login.style.display = "block";
    setStatus("The engine could not start.", true);
    // A black screen usually means a renderer switch: show it without hunting.
    const compat = $("compat");
    if (compat) compat.open = true;
    fatalText.textContent = err && err.stack ? err.stack : String(err || "Unknown error");
    fatal.style.display = "flex";
  }

  async function play() {
    if (playing) return;
    if (!selected) { setStatus("Choose a place first.", true); return; }
    const name = saveName();
    playing = true;
    playBtn.disabled = true;
    closeCatalog();
    login.style.pointerEvents = "none";
    fatal.style.display = "none";
    setStatus("Starting the 2016 engine…");
    await afterPaint();

    try {
      const bytes = await readFileBytes(selected);
      setStatus("Starting the 2016 engine… (" + (bytes.length / 1024).toFixed(0) + " KB place)");
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
        ["number", "number", "string", "string", "number"],
        // Must include the subpath: a project page lives under /<repo>/ and
        // the engine resolves catalog/item/<id> and /asset against this.
        [ptr, bytes.length, name, new URL(".", location.href).href, touchDevice ? 1 : 0]);
      mod._free(ptr);
      if (rc !== 0) throw new Error("rblx_start_place returned " + rc);
      try {
        mod.ccall("rblx_set_unlock_fps", null, ["number"], [unlockFps.checked ? 1 : 0]);
      } catch (_) {}
      applyRenderOptions(mod);
      // Hosted builds have no /asset endpoint; a small Worker does it instead.
      if (!window.__rblxLocalServer) {
        try {
          const response = await fetch("asset-proxy.json", { cache: "no-store" });
          if (response.ok) {
            const proxy = (await response.json()).base;
            if (proxy) mod.ccall("rblx_set_asset_proxy", null, ["string"], [proxy]);
          }
        } catch (_) {}
      }
      // Hand the worn catalog items to the engine; it applies them when the
      // local character spawns.
      try {
        mod.ccall("rblx_set_avatar", null, ["string"], [avatarSpec()]);
      } catch (_) {}
      if (typeof mod._rblx_has_view === "function" && mod.ccall("rblx_has_view", "number", [], [])) {
        const frame = () => {
          if (!playing) return;
          if (canvas.__flushMouse) canvas.__flushMouse();
          try { mod.ccall("rblx_frame", null, [], []); } catch (e) { console.warn(e); }
          if (screenshotPending) {
            screenshotPending = false;
            captureCanvas();
          }
          rafId = requestAnimationFrame(frame);
        };
        installInput(mod);
        resizeCanvas();
        if (!canvas.__resizeObserved && typeof ResizeObserver !== "undefined") {
          canvas.__resizeObserved = true;
          new ResizeObserver(resizeCanvas).observe(canvas);
        }
        rafId = requestAnimationFrame(frame);
        // Keep the login shell up until the engine's own LoadingScript has
        // built its GUI. The stock loading screen is the only loading UI.
        const startLoad = (tries) => {
          let ready = 0;
          try { ready = mod.ccall("rblx_loading_gui_ready", "number", [], []); } catch (_) {}
          if (!ready && tries > 0) {
            setTimeout(() => startLoad(tries - 1), 100);
            return;
          }
          const begin = async () => {
            if (ready) {
              // Let the LoadingScript paint its background and spinner.
              await new Promise((resolve) => setTimeout(resolve, 900));
              await afterPaint();
            }
            login.style.display = "none";
            setStatus("");
            try { mod.ccall("rblx_load_place", "number", [], []); } catch (e) { console.warn(e); }
            // Fallback only; the engine normally spawns the avatar itself and
            // the LoadingScript stays up until then.
            setTimeout(() => { try { mod.ccall("rblx_force_character", null, [], []); } catch (_) {} }, 6000);
          };
          begin();
        };
        startLoad(25);
      } else {
        try { mod.ccall("rblx_load_place", "number", [], []); } catch (_) {}
        login.style.display = "none";
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
      case "F11": return 292;
      case "F12": return 293;
      case "PrintScreen": return 316;
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
    let lockRequested = false;
    let lockTime = 0;
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

    const activeTouches = new Map();
    const releaseAll = () => {
      for (const [, h] of heldKeys) ccall("rblx_key_event", [h.code, 0, h.ch, 0]);
      heldKeys.clear();
      keysDown.clear();
      for (const b of heldButtons) ccall("rblx_mouse_button", [b, lockedPos.x, lockedPos.y, 0]);
      heldButtons.clear();
      for (const [id, p] of activeTouches) {
        try {
          mod.ccall("rblx_touch_event", null, ["number", "number", "number", "number"], [id, p[0], p[1], 3]);
        } catch (_) {}
      }
      activeTouches.clear();
      rightDown = false;
      lockRequested = false;
      if (document.pointerLockElement === canvas) document.exitPointerLock();
    };
    canvas.__releaseInput = releaseAll;

    const touchToEngine = (t, state) => {
      const p = toCanvas(t);
      if (state === 0 || state === 1) activeTouches.set(t.identifier, [p[0], p[1]]);
      try {
        mod.ccall("rblx_touch_event", null, ["number", "number", "number", "number"],
          [t.identifier, p[0], p[1], state]);
      } catch (_) {}
    };
    canvas.addEventListener("touchstart", (e) => {
      if (!playing) return;
      e.preventDefault();
      for (const t of e.changedTouches) {
        if (!activeTouches.has(t.identifier)) touchToEngine(t, 0);
      }
    }, { passive: false });
    canvas.addEventListener("touchmove", (e) => {
      if (!playing) return;
      e.preventDefault();
      for (const t of e.changedTouches) {
        if (!activeTouches.has(t.identifier)) touchToEngine(t, 0);
        else touchToEngine(t, 1);
      }
    }, { passive: false });
    canvas.addEventListener("touchend", (e) => {
      if (!playing) return;
      e.preventDefault();
      for (const t of e.changedTouches) {
        if (activeTouches.delete(t.identifier)) {
          const p = toCanvas(t);
          try {
            mod.ccall("rblx_touch_event", null, ["number", "number", "number", "number"],
              [t.identifier, p[0], p[1], 2]);
          } catch (_) {}
        }
      }
    }, { passive: false });
    canvas.addEventListener("touchcancel", (e) => {
      if (!playing) return;
      e.preventDefault();
      for (const t of e.changedTouches) {
        if (activeTouches.delete(t.identifier)) {
          const p = toCanvas(t);
          try {
            mod.ccall("rblx_touch_event", null, ["number", "number", "number", "number"],
              [t.identifier, p[0], p[1], 3]);
          } catch (_) {}
        }
      }
    }, { passive: false });

    document.addEventListener("pointerlockchange", () => {
      locked = document.pointerLockElement === canvas;
      lockRequested = false;
      // The first event after capture can carry the delta from the click
      // position to the capture origin; ignore it once, plus the short warp
      // window. Drop any movement queued before the lock took effect.
      skipLockedMove = locked;
      lockTime = performance.now();
      pending.has = false;
      pending.dx = 0;
      pending.dy = 0;
      updateCursor();
      if (!locked && rightDown) {
        rightDown = false;
        ccall("rblx_mouse_button", [2, lockedPos.x, lockedPos.y, 0]);
      }
    });
    document.addEventListener("pointerlockerror", () => {
      lockRequested = false;
    });
    updateCursor();
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    document.addEventListener("mousemove", (e) => {
      if (!playing) return;
      // Browser pointer lock warps the cursor to the screen center. Those
      // pre-lock movement deltas must not reach the camera: the right-button
      // drag has already started, and applying them snaps the view.
      if (rightDown && lockRequested) return;
      const p = toCanvas(e);
      let dx = Math.round(e.movementX * p[2]);
      let dy = Math.round(e.movementY * p[3]);
      if (locked && (skipLockedMove || performance.now() - lockTime < 50)) {
        skipLockedMove = false;
        dx = 0;
        dy = 0;
      }
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
      // Deliver any coalesced motion from before the click before starting a
      // drag; otherwise it is flushed after MouseButton2 down and the camera
      // consumes that whole delta as a snap.
      flushMouse();
      if (e.button === 2) {
        rightDown = true;
        lockedPos.x = p[0]; lockedPos.y = p[1];
        lockRequested = document.pointerLockElement !== canvas;
        if (lockRequested) {
          setTimeout(() => { lockRequested = false; }, 1000);
          try {
            const req = canvas.requestPointerLock();
            if (req && req.catch) req.catch(() => { lockRequested = false; });
          } catch (_) {
            lockRequested = false;
          }
        }
      }
      ccall("rblx_mouse_button", [e.button, p[0], p[1], 1]);
    });
    window.addEventListener("mouseup", (e) => {
      if (!playing) return;
      const p = locked ? [lockedPos.x, lockedPos.y] : toCanvas(e);
      heldButtons.delete(e.button);
      if (e.button === 2) {
        rightDown = false;
        lockRequested = false;
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
      if ([" ","arrowup","arrowdown","arrowleft","arrowright","tab","f11","f12","printscreen"].includes(k)) e.preventDefault();
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
    screenshotPending = false;
    if (mediaRecorder) { try { stopRecording(); } catch (_) {} }
    if (canvas.__releaseInput) canvas.__releaseInput();
    try {
      if (engine && typeof engine._rblx_stop === "function") {
        engine.ccall("rblx_stop", null, [], []);
      }
    } catch (e) { console.warn(e); }
    playing = false;
    playBtn.disabled = !hasWebGL2();
    login.style.pointerEvents = "";
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
    fetch("cookie", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("no server"))))
      .then((status) => { window.__rblxLocalServer = true; return status; })
      .then(showCookieStatus)
      .catch(() => {
        // No local server: the field stays visible for reference, but there is
        // nothing that could use the cookie and it must not sit in this page.
        $("cookieState").textContent = "needs the local server (python3 web/serve.py)";
        input.disabled = true;
        input.placeholder = "set it in the local server instead";
        save.disabled = true;
        clear.disabled = true;
        const note = $("cookieNote");
        if (note) note.textContent =
          "This hosted copy has no asset proxy, so the cookie cannot be used here. " +
          "Run the site with web/serve.py to download rbxassetid content.";
      });
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

  // ------------------------------------------------------- render options
  // Renderer workarounds for devices where the world renders black. They are
  // read by the engine before it creates the view, so they apply on the next
  // Play (or after a reload).
  function renderOptions() {
    return {
      lightgrid: Number(localStorage.getItem("rblx2016.lightgrid") || "0") || 0,
      gles2: localStorage.getItem("rblx2016.gles2") === "1",
      diag: localStorage.getItem("rblx2016.diag") === "1",
    };
  }

  // A non-default renderer switch must not hide inside a closed section.
  function markRenderOptions() {
    const state = $("compatState");
    if (!state) return;
    const opts = renderOptions();
    state.textContent = opts.lightgrid || opts.gles2 || opts.diag ? "(custom)" : "";
  }

  function installRenderOptions() {
    const grid = $("lightGrid");
    const gles2 = $("gles2Shaders");
    const diag = $("showDiag");
    if (!grid || !gles2 || !diag) return;
    const opts = renderOptions();
    grid.value = String(opts.lightgrid);
    gles2.checked = opts.gles2;
    diag.checked = opts.diag;
    markRenderOptions();
    grid.addEventListener("change", () => {
      localStorage.setItem("rblx2016.lightgrid", grid.value);
      setStatus("Lighting option saved; it applies on the next Play.");
      markRenderOptions();
    });
    gles2.addEventListener("change", () => {
      localStorage.setItem("rblx2016.gles2", gles2.checked ? "1" : "0");
      setStatus("Shader option saved; it applies on the next Play.");
      markRenderOptions();
    });
    diag.addEventListener("change", () => {
      localStorage.setItem("rblx2016.diag", diag.checked ? "1" : "0");
      updateDiag();
      markRenderOptions();
    });
  }

  function applyRenderOptions(mod) {
    const opts = renderOptions();
    try {
      mod.ccall("rblx_set_render_option", null, ["string", "number"], ["lightgrid", opts.lightgrid]);
      mod.ccall("rblx_set_render_option", null, ["string", "number"], ["shaders", opts.gles2 ? 1 : 0]);
    } catch (e) {
      console.warn("[rbx2016] render options not available", e);
    }
  }

  let diagTimer = 0;
  let diagFrames = 0;
  function updateDiag() {
    const box = $("diagBox");
    if (!box) return;
    const opts = renderOptions();
    box.classList.toggle("on", opts.diag);
    if (!opts.diag) return;
    if (!diagTimer) {
      diagTimer = setInterval(updateDiag, 1000);
      const tick = () => { diagFrames++; requestAnimationFrame(tick); };
      requestAnimationFrame(tick);
    }
    const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
    const info = gl && gl.getExtension("WEBGL_debug_renderer_info");
    const renderer = gl && info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : "?";
    const version = gl ? gl.getParameter(gl.VERSION) : "?";
    const maxTex = gl ? gl.getParameter(gl.MAX_TEXTURE_SIZE) : 0;
    const max3D = gl ? gl.getParameter(gl.MAX_3D_TEXTURE_SIZE) : 0;
    let frames = 0;
    try { frames = engine ? engine.ccall("rblx_frame_count", "number", [], []) : 0; } catch (_) {}
    box.textContent =
      "renderer  " + renderer + "\n" +
      "GL        " + version + "\n" +
      "texture   " + maxTex + " / 3D " + max3D + "\n" +
      "lighting  " + (opts.lightgrid === 0 ? "auto" : opts.lightgrid === 1 ? "2D grid" : "no grid") +
      " | shaders " + (opts.gles2 ? "gles2" : "default") + "\n" +
      "fps       " + diagFrames + " page / " + frames + " engine" + "\n" +
      "isolated  " + (window.crossOriginIsolated ? "yes" : "no");
    diagFrames = 0;
  }

  // ---------------------------------------------------------------- catalog
  // The 2016 avatar files are scraped once into web/catalog by
  // port/tools/fetch_catalog.py; the page only reads the manifest.
  const AVATAR_KEY = "rblx2016.avatar";
  const CATEGORIES = [
    { id: "", label: "All Categories" },
    { id: "hat", label: "Hats" },
    { id: "hair", label: "Hair" },
    { id: "shoulder", label: "Shoulder" },
    { id: "back", label: "Back" },
    { id: "face", label: "Faces" },
    { id: "shirt", label: "Shirts" },
    { id: "pants", label: "Pants" },
    { id: "tshirt", label: "T-Shirts" },
  ];
  // Hats, shoulder and back items are all wearable at once.
  const ACCESSORY_TYPES = ["hat", "hair", "shoulder", "back"];
  const BODY_SLOTS = ["Head", "Left arm", "Right arm", "Torso", "Left leg", "Right leg"];
  const SLOT_SHORT = ["Head", "L arm", "R arm", "Torso", "L leg", "R leg"];
  let bodyPart = 0;
  const BRICK_COLORS = [
    { n: 24, hex: "#f5cd30", name: "Bright yellow" },
    { n: 1, hex: "#f2f3f3", name: "White" },
    { n: 194, hex: "#a3a2a5", name: "Medium stone grey" },
    { n: 199, hex: "#635f62", name: "Dark stone grey" },
    { n: 21, hex: "#c4281c", name: "Bright red" },
    { n: 23, hex: "#0d69ac", name: "Bright blue" },
    { n: 28, hex: "#287f47", name: "Dark green" },
    { n: 37, hex: "#4b974b", name: "Bright green" },
    { n: 106, hex: "#da8541", name: "Bright orange" },
    { n: 5, hex: "#d7c59a", name: "Brick yellow" },
    { n: 192, hex: "#694028", name: "Reddish brown" },
    { n: 217, hex: "#7c5c46", name: "Brown" },
    { n: 18, hex: "#cc8e69", name: "Nougat" },
    { n: 102, hex: "#6e99ca", name: "Medium blue" },
    { n: 119, hex: "#a4bd47", name: "Br. yellowish green" },
    { n: 101, hex: "#da867a", name: "Medium red" },
    { n: 9, hex: "#e8bac8", name: "Light reddish violet" },
    { n: 107, hex: "#008f9c", name: "Bright bluish green" },
    { n: 2, hex: "#a1a5a2", name: "Grey" },
    { n: 226, hex: "#fdea8d", name: "Cool yellow" },
  ];
  const catalogPage = $("catalog");
  const catGrid = $("catGrid");
  const catNote = $("catNote");
  let catalogItems = null;
  let catalogCategory = "";
  let catalogQuery = "";
  let catalogCreator = "";
  let detailItem = null;
  let avatar = loadAvatar();

  function loadAvatar() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(AVATAR_KEY) || "{}") || {}; } catch (_) {}
    // Classic noob: yellow head and arms, bright blue torso, bright green legs.
    const noob = [24, 24, 24, 23, 37, 37];
    const body = Array.isArray(saved.body) && saved.body.length === 6
      ? saved.body.map((n) => Number(n) || 24) : noob.slice();
    // The old all-yellow default was never a deliberate choice; upgrade it.
    if (body.join(",") === "24,24,24,24,24,24") body.splice(0, 6, ...noob);
    const accessories = Array.isArray(saved.accessories) ? saved.accessories
      : (Array.isArray(saved.hats) ? saved.hats : []);
    return {
      shirt: Number(saved.shirt) || 0,
      pants: Number(saved.pants) || 0,
      tshirt: Number(saved.tshirt) || 0,
      face: Number(saved.face) || 0,
      accessories: accessories.map(Number).filter(Boolean),
      body,
    };
  }

  function saveAvatar() {
    try { localStorage.setItem(AVATAR_KEY, JSON.stringify(avatar)); } catch (_) {}
    renderWorn();
  }

  function isWorn(item) {
    if (ACCESSORY_TYPES.includes(item.type)) return avatar.accessories.includes(item.id);
    return avatar[item.type] === item.id;
  }

  function wear(item) {
    if (ACCESSORY_TYPES.includes(item.type)) {
      avatar.accessories = isWorn(item)
        ? avatar.accessories.filter((id) => id !== item.id)
        : avatar.accessories.concat(item.id);
    } else {
      avatar[item.type] = isWorn(item) ? 0 : item.id;
    }
    saveAvatar();
    renderCatalog();
    if (detailItem) renderDetail();
  }

  // The engine applies this list to the local character: one item per clothing
  // slot, any number of hats, plus the six BodyColors values.
  function avatarSpec() {
    const parts = [];
    for (const kind of ["shirt", "pants", "tshirt", "face"]) {
      if (avatar[kind]) parts.push(kind + ":" + avatar[kind]);
    }
    for (const id of avatar.accessories) parts.push("acc:" + id);
    parts.push("body:" + avatar.body.join(","));
    return parts.join(";");
  }

  function itemById(id) {
    return (catalogItems || []).find((it) => it.id === id) || null;
  }

  function itemImage(item) {
    if (item.thumb) return "catalog/" + item.thumb;
    if (item.file) return "catalog/" + item.file;
    return "";  // packages mined from places have no catalog thumbnail
  }

  function renderWorn() {
    const worn = [];
    for (const kind of ["shirt", "pants", "tshirt", "face"]) {
      const item = itemById(avatar[kind]);
      if (item) worn.push(item);
    }
    for (const id of avatar.accessories) {
      const item = itemById(id);
      if (item) worn.push(item);
    }
    const names = worn.map((it) => it.name);
    const line = $("wornLine");
    line.textContent = names.length ? "Wearing: " + names.join(", ") : "Not wearing anything yet.";
    $("catWornLine").textContent = names.length ? names.join(", ") : "nothing worn";
    const bar = $("catWornBar");
    bar.innerHTML = "";
    if (!worn.length) {
      bar.textContent = "You are not wearing anything yet. Click an item to wear it.";
      return;
    }
    const label = document.createElement("span");
    label.innerHTML = "<b>Wearing:</b>";
    bar.appendChild(label);
    for (const item of worn) {
      const chip = document.createElement("span");
      chip.textContent = item.name + " ";
      const x = document.createElement("button");
      x.type = "button";
      x.className = "btn";
      x.textContent = "x";
      x.title = "Remove " + item.name;
      x.addEventListener("click", () => wear(item));
      chip.appendChild(x);
      bar.appendChild(chip);
    }
  }

  function renderCatalog() {
    if (!catalogPage.classList.contains("open")) return;
    const cats = $("catCats");
    if (!cats.childElementCount) {
      for (const cat of CATEGORIES) {
        const link = document.createElement("a");
        link.textContent = cat.label;
        link.dataset.id = cat.id;
        link.tabIndex = 0;
        link.addEventListener("click", () => {
          catalogCategory = cat.id;
          renderCatalog();
        });
        link.addEventListener("keydown", (e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          catalogCategory = cat.id;
          renderCatalog();
        });
        cats.appendChild(link);
      }
    }
    for (const link of cats.children) link.classList.toggle("sel", link.dataset.id === catalogCategory);

    catGrid.innerHTML = "";
    if (!catalogItems) return;
    if (!catalogItems.length) {
      catNote.style.display = "block";
      catNote.innerHTML = "No catalog was scraped yet. Run <code>python3 port/tools/fetch_catalog.py</code>" +
        " to build <code>web/catalog/</code> from the Roblox website.";
      return;
    }
    catNote.style.display = "none";
    const query = catalogQuery.toLowerCase();
    const shown = catalogItems.filter((it) =>
      (!catalogCategory || it.type === catalogCategory) &&
      (!catalogCreator || it.creator === catalogCreator) &&
      (!query || it.name.toLowerCase().includes(query)));
    if (!shown.length) {
      catNote.style.display = "block";
      catNote.textContent = "No items match that filter.";
      return;
    }
    for (const item of shown) {
      const tile = document.createElement("div");
      tile.className = "tile" + (isWorn(item) ? " worn" : "");
      const src = itemImage(item);
      const img = document.createElement(src ? "img" : "div");
      if (src) {
        img.loading = "lazy";
        img.src = src;
      } else {
        img.textContent = (item.name || "?").slice(0, 1);
        img.style.cssText = "display:flex;align-items:center;justify-content:center;background:#e6ebf1;color:#7d8fa3;font-size:44px";
      }
      img.alt = "";
      if (src && item.file) {
        img.addEventListener("error", () => {
          // Some items have no rendered thumbnail; the template/model still shows.
          if (!img.dataset.fallback) {
            img.dataset.fallback = "1";
            img.src = "catalog/" + item.file;
          }
        });
      }
      const nm = document.createElement("div");
      nm.className = "nm";
      nm.textContent = item.name;
      const cr = document.createElement("div");
      cr.className = "cr";
      cr.textContent = "by " + item.creator;
      const pr = document.createElement("div");
      pr.className = "pr";
      pr.textContent = "Your soul";
      tile.append(img, nm, cr, pr);
      tile.tabIndex = 0;
      tile.addEventListener("click", () => openDetail(item));
      tile.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openDetail(item);
        }
      });
      catGrid.appendChild(tile);
    }
  }

  function renderDetail() {
    $("catDetailImg").src = itemImage(detailItem);
    $("catDetailName").textContent = detailItem.name;
    const kind = detailItem.type.replace("tshirt", "t-shirt");
    $("catDetailMeta").textContent = "by " + detailItem.creator +
      (detailItem.created ? " · " + detailItem.created : "") + " · " + kind;
    $("catWear").textContent = isWorn(detailItem) ? "Take off" : "Wear";
    $("catWear").classList.toggle("wearing", isWorn(detailItem));
  }

  function openDetail(item) {
    detailItem = item;
    renderDetail();
    $("catDetail").classList.add("open");
  }

  function closeDetail() {
    detailItem = null;
    $("catDetail").classList.remove("open");
  }

  async function loadCatalog() {
    if (catalogItems) return;
    try {
      const response = await fetch("catalog/manifest.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const manifest = await response.json();
      // Packages carry body parts in the manifest instead of a single item file.
      catalogItems = (manifest.items || []).filter((it) => it && it.id && it.file);
    } catch (_) {
      catalogItems = [];
    }
    const creators = [...new Set((catalogItems || []).map((it) => it.creator))].sort();
    const select = $("catCreator");
    for (const creator of creators) {
      const option = document.createElement("option");
      option.value = creator;
      option.textContent = creator;
      select.appendChild(option);
    }
    renderWorn();
    renderCatalog();
  }

  // 2016 avatar editor: pick a body part, then pick from the BrickColor palette.
  function buildColourPickers() {
    const box = $("catColours");
    const parts = document.createElement("div");
    parts.className = "parts";
    BODY_SLOTS.forEach((slot, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "partBtn";
      const chip = document.createElement("span");
      chip.className = "chip";
      const label = document.createElement("span");
      label.className = "part";
      label.textContent = SLOT_SHORT[i];
      btn.append(chip, label);
      btn.addEventListener("click", () => { bodyPart = i; renderColours(); });
      parts.appendChild(btn);
    });
    const swatches = document.createElement("div");
    swatches.className = "swatches";
    for (const color of BRICK_COLORS) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "swatch";
      swatch.style.background = color.hex;
      swatch.dataset.n = String(color.n);
      swatch.title = color.name;
      swatch.setAttribute("aria-label", color.name);
      swatch.addEventListener("click", () => {
        avatar.body[bodyPart] = color.n;
        saveAvatar();
        renderColours();
      });
      swatches.appendChild(swatch);
    }
    box.append(parts, swatches);
    renderColours();
  }

  function renderColours() {
    const box = $("catColours");
    if (!box) return;
    const byNumber = new Map(BRICK_COLORS.map((c) => [c.n, c]));
    const partButtons = box.querySelectorAll(".partBtn");
    partButtons.forEach((btn, i) => {
      const color = byNumber.get(avatar.body[i]) || BRICK_COLORS[0];
      btn.querySelector(".chip").style.background = color.hex;
      btn.setAttribute("aria-pressed", i === bodyPart ? "true" : "false");
      btn.title = BODY_SLOTS[i] + ": " + color.name;
    });
    const current = String(avatar.body[bodyPart]);
    box.querySelectorAll(".swatch").forEach((swatch) => {
      swatch.setAttribute("aria-pressed", swatch.dataset.n === current ? "true" : "false");
    });
  }

  function openCatalog() {
    catalogPage.classList.add("open");
    renderWorn();
    renderCatalog();
    loadCatalog();
  }

  function closeCatalog() {
    catalogPage.classList.remove("open");
    closeDetail();
  }

  async function boot() {
    try { userName.value = localStorage.getItem("rblx2016.name") || "Player1"; } catch (_) {}
    try { unlockFps.checked = localStorage.getItem("rblx2016.unlockFps") === "1"; } catch (_) {}
    unlockFps.addEventListener("change", () => {
      try { localStorage.setItem("rblx2016.unlockFps", unlockFps.checked ? "1" : "0"); } catch (_) {}
    });
    userName.addEventListener("input", saveName);
    userName.addEventListener("blur", saveName);
    openFile.addEventListener("change", () => openLocalFile(openFile.files[0]));
    $("catalogBtn").addEventListener("click", openCatalog);
    $("catClose").addEventListener("click", closeCatalog);
    $("catSearch").addEventListener("input", () => {
      catalogQuery = $("catSearch").value.trim();
      renderCatalog();
    });
    $("catCreator").addEventListener("change", () => {
      catalogCreator = $("catCreator").value;
      renderCatalog();
    });
    $("catWear").addEventListener("click", () => { if (detailItem) wear(detailItem); });
    $("catDetailClose").addEventListener("click", closeDetail);
    $("catDetail").addEventListener("click", (e) => {
      if (e.target === $("catDetail")) closeDetail();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if ($("catDetail").classList.contains("open")) closeDetail();
      else if (catalogPage.classList.contains("open")) closeCatalog();
    });
    buildColourPickers();
    installRenderOptions();
    updateDiag();
    renderWorn();
    loadCatalog();  // keeps the "Wearing:" line on the menu in sync
    $("openFileBtn").addEventListener("click", () => openFile.click());
    $("refreshPlaces").addEventListener("click", () => loadPlaces());
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
    await loadPlaces();
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
