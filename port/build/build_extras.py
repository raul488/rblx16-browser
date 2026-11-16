#!/usr/bin/env python3
"""Compile objects that are not owned by the main project files.

These are the Lua VM server implementation, the RakNet persistent store, and
the Boost.System/Thread/Filesystem libraries.  They must be rebuilt with the
same flags as the rest of the engine (notably -fwasm-exceptions).
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
SHIM = ROOT / "port" / "shim" / "caseinclude"
PATCH = ROOT / "port" / "src_patch"
OBJ = ROOT / "port" / "out" / "obj"
BOOST = Path("/home/raul/boost_1_56_0")
EMXX = Path("/home/raul/emsdk/upstream/emscripten/em++")

INCLUDES = [
    SHIM,
    ENGINE / "App" / "include",
    ENGINE / "App",
    ENGINE / "Base" / "include",
    ENGINE / "Rendering" / "g3d" / "include",
    ENGINE / "Rendering" / "RbxG3D" / "include",
    ENGINE / "Rendering" / "GfxBase" / "include",
    ENGINE / "Rendering" / "GfxCore" / "include",
    ENGINE / "Rendering" / "GfxRender",
    ENGINE / "Rendering" / "AppDraw" / "include",
    ENGINE / "Network" / "include",
    ENGINE / "Network" / "include" / "network",
    ENGINE / "Network",
    ENGINE / "Network" / "raknet" / "Source",
    ENGINE / "ClientShared",
    ENGINE / "ClientBase",
    ENGINE / "Log" / "include",
    ENGINE / "App.BulletPhysics",
    ENGINE / "fmod" / "include",
    ENGINE / "App" / "Lua-5.1.4" / "src",
]

CXXFLAGS = [
    "-std=c++11", "-O0", "-pthread", "-fpermissive", "-fdeclspec", "-w",
    "-fwasm-exceptions", "-DROBLOX_BOOST_CONFIGS", "-DRBX_SIMD_USE_SSE",
    "-msimd128", "-msse2", "-sUSE_SDL=2",
]


def sources() -> list[tuple[Path, Path, list[str]]]:
    out = [
        (ENGINE / "App" / "script" / "LuaVMDummy.cpp",
         OBJ / "script" / "LuaVMDummy.cpp.o", []),
        (ENGINE / "Network" / "PersistentDataStore.cpp",
         OBJ / "Network" / "PersistentDataStore.cpp.o", []),
    ]
    for f in ("path", "path_traits", "portability", "operations", "unique_path", "codecvt_error_category"):
        out.append((BOOST / "libs" / "filesystem" / "src" / f"{f}.cpp",
                    OBJ / "boost" / f"filesystem_{f}.cpp.o", ["-DBOOST_THREAD_BUILD_LIB"]))
    for f in ("error_code",):
        out.append((BOOST / "libs" / "system" / "src" / f"{f}.cpp",
                    OBJ / "boost" / f"{f}.cpp.o", ["-DBOOST_THREAD_BUILD_LIB"]))
    for f in ("once", "thread"):
        out.append((BOOST / "libs" / "thread" / "src" / "pthread" / f"{f}.cpp",
                    OBJ / "boost" / f"{f}.cpp.o", ["-DBOOST_THREAD_BUILD_LIB"]))
    for f in ("future", "tss_null"):
        out.append((BOOST / "libs" / "thread" / "src" / f"{f}.cpp",
                    OBJ / "boost" / f"{f}.cpp.o", ["-DBOOST_THREAD_BUILD_LIB"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    failures = []
    for src, obj, extra in sources():
        if not src.is_file():
            print("missing", src, flush=True)
            failures.append(src.as_posix())
            continue
        if obj.is_file() and not a.force:
            continue
        obj.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(EMXX)] + CXXFLAGS + extra
        for inc in INCLUDES:
            cmd += ["-I", str(inc)]
        cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
        env = os.environ.copy()
        env["PATH"] = ("/home/raul/emsdk:/home/raul/emsdk/upstream/emscripten:"
                       "/home/raul/python312/bin:" + env.get("PATH", ""))
        r = subprocess.run(cmd, env=env)
        print(("ok  " if r.returncode == 0 else "FAIL") + " " + src.name, flush=True)
        if r.returncode:
            failures.append(src.as_posix())
    print(f"{len(failures)} failures", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
