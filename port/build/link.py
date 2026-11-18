#!/usr/bin/env python3
"""Link the compiled 2016 engine objects into a browser Wasm module."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
OUT = ROOT / "port" / "out"
OBJ = OUT / "obj"
WEB = ROOT / "web" / "js"
BOOST = Path("/home/raul/boost_1_56_0")

INCLUDES = [
    ROOT / "port" / "shim" / "caseinclude",
    ENGINE / "App" / "include",
    ENGINE / "App",
    ENGINE / "Base" / "include",
    ENGINE / "Rendering" / "g3d" / "include",
    ENGINE / "Rendering" / "RbxG3D" / "include",
    ENGINE / "Rendering" / "GfxBase" / "include",
    ENGINE / "Rendering" / "GfxCore" / "include",
    ENGINE / "Rendering" / "GfxRender",
    ENGINE / "Rendering" / "GfxCore",
    ENGINE / "Rendering" / "GfxCore" / "GL",
    ENGINE / "Rendering" / "AppDraw" / "include",
    ENGINE / "Network" / "include",
    ENGINE / "Network" / "include" / "network",
    ENGINE / "Network",
    ENGINE / "ClientShared",
    ENGINE / "ClientBase",
    ENGINE / "Log" / "include",
    ENGINE / "App.BulletPhysics",
    ENGINE / "fmod" / "include",
    ENGINE / "App" / "Lua-5.1.4" / "src",
]

CXXFLAGS = [
    "-std=c++11",
    "-fwasm-exceptions",
    "-O0",
    "-pthread",
    "-fpermissive",
    "-fdeclspec",
    "-w",
    "-DROBLOX_BOOST_CONFIGS",
    "-DRBX_SIMD_USE_SSE",
    "-msimd128",
    "-msse2",
    "-sUSE_SDL=2",
]


def env() -> dict[str, str]:
    e = os.environ.copy()
    e["PATH"] = ("/home/raul/emsdk:/home/raul/emsdk/upstream/emscripten:"
                 "/home/raul/python312/bin:" + e.get("PATH", ""))
    e["EMSDK"] = "/home/raul/emsdk"
    e["EMSDK_NODE"] = "/home/raul/emsdk/node/24.19.0_64bit/bin/node"
    return e


def main() -> int:
    WEB.mkdir(parents=True, exist_ok=True)
    for src_name in ("main.cpp", "WebView.cpp", "WebCoreScripts.cpp", "platform_stubs.cpp", "text_stubs.cpp", "network_globals.cpp", "offline_stubs.cpp", "network_stubs.cpp", "util_stubs.cpp", "fmod_stubs.cpp", "legacy_stubs.cpp"):
        src = ROOT / "port" / "src" / "WebClient" / src_name
        obj = OBJ / "WebClient" / (src_name + ".o")
        obj.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["em++"] + CXXFLAGS
        for inc in INCLUDES:
            cmd += ["-I", str(inc)]
        cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
        print("compiling WebClient", src_name, "...", flush=True)
        r = subprocess.run(cmd, env=env())
        if r.returncode:
            return r.returncode

    skip = {"lcode.c.o", "lparser.c.o"}  # provided by LuaVMServer.cpp
    objects = sorted([o for o in OBJ.rglob("*.o")
                      if "network_excluded" not in o.parts and o.name not in skip]
                     + list((OUT / "gfxobj").rglob("*.o")))
    print(f"linking {len(objects)} objects...")
    link = ["em++", "-O0", "-pthread"] + [str(o) for o in objects]
    link += [
        "-fwasm-exceptions",
        "-o", str(WEB / "rblx2016.js"),
        "-sUSE_SDL=2",
        "-sUSE_GLFW=3",
        "-lGL",
        "-sUSE_ZLIB=1",
        "-sUSE_PTHREADS=1",
        "-sPTHREAD_POOL_SIZE=8",
        "-sPTHREAD_POOL_SIZE_STRICT=0",
        "-sMODULARIZE=1",
        "-sEXPORT_NAME=createRBLX2016Module",
        "-sENVIRONMENT=web",
        "-sINVOKE_RUN=0",
        "-sALLOW_MEMORY_GROWTH=1",
        "-sINITIAL_MEMORY=268435456",
        "-sMAXIMUM_MEMORY=2147483648",
        "-sMIN_WEBGL_VERSION=2",
        "-sMAX_WEBGL_VERSION=2",
        "-sFULL_ES3=1",
        "-sGL_ENABLE_GET_PROC_ADDRESS=1",
        "-sASSERTIONS=2",
        "-sSTACK_SIZE=8388608",
        "-sDEFAULT_PTHREAD_STACK_SIZE=8388608",
        "--profiling-funcs",
        "-sSTACK_OVERFLOW_CHECK=2",
        "-sNO_EXIT_RUNTIME=1",
        "-sFETCH=1",
        "--preload-file", str(ROOT / "web" / "content") + "@/content",
        "--preload-file", str(ROOT / "web" / "shaders") + "@/shaders",
        "-sEXPORTED_FUNCTIONS=_rblx_start_place,_rblx_load_place,_rblx_loading_gui_ready,_rblx_stop,_rblx_frame,_rblx_resize,_rblx_has_view,_rblx_last_error,_rblx_test_thread,_rblx_frame_count,_rblx_set_camera,_rblx_set_fullscreen,_rblx_set_unlock_fps,_rblx_set_avatar,_rblx_set_render_option,_rblx_touch_event,_rblx_debug_player,_rblx_key_event,_rblx_mouse_button,_rblx_mouse_move,_rblx_mouse_wheel,_rblx_force_character,_rblx_run_script,_rblx_debug_joints,_rblx_debug_anim,_rblx_debug_asset,_rblx_debug_gui,_rblx_audio_ended,_malloc,_free",
        "-sEXPORTED_RUNTIME_METHODS=ccall,cwrap,HEAPU8,HEAPU32",
    ]
    r = subprocess.run(link, env=env())
    if r.returncode:
        print("link failed", r.returncode, file=sys.stderr)
        return r.returncode
    print("linked", WEB / "rblx2016.js", WEB / "rblx2016.wasm")
    split_data_package()
    write_place_manifest()
    return 0


def split_data_package(chunk=48 * 1024 * 1024):
    """Static hosts cap a single file at 100 MB; the page concatenates parts."""
    data = WEB / "rblx2016.data"
    if not data.is_file():
        return
    for old in WEB.glob("rblx2016.data.part*"):
        old.unlink()
    parts = []
    with data.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            part = WEB / f"rblx2016.data.part{len(parts) + 1}"
            part.write_bytes(block)
            parts.append("js/" + part.name)
    (WEB / "rblx2016.data.parts.json").write_text(json.dumps({"parts": parts}))
    data.unlink()
    print("preloaded package:", len(parts), "parts")


def write_place_manifest():
    """places/manifest.json lets a static host list the bundled places."""
    places = ROOT / "web" / "places"
    if not places.is_dir():
        return
    entries = [{"name": p.stem, "file": p.name, "bytes": p.stat().st_size}
               for p in sorted(places.iterdir())
               if p.suffix.lower() in (".rbxl", ".rbxlx") and p.is_file()]
    (places / "manifest.json").write_text(json.dumps(entries, indent=1))
    print("places:", len(entries), "in places/manifest.json")
    return


if __name__ == "__main__":
    sys.exit(main())
