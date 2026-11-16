#!/usr/bin/env python3
"""Compile the 2016 Network module objects used by the local Players service."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
NET = ENGINE / "Network"
SHIM = ROOT / "port" / "shim" / "caseinclude"
OUT = ROOT / "port" / "out" / "obj" / "Network"
LOGS = ROOT / "port" / "out" / "logs"
PATCH = ROOT / "port" / "src_patch"
BOOST = Path("/home/raul/boost_1_56_0")
EMXX = Path("/home/raul/emsdk/upstream/emscripten/em++")
EMCC = Path("/home/raul/emsdk/upstream/emscripten/emcc")

INCLUDES = [
    NET,
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
    "-std=c++11",
    "-fwasm-exceptions", "-O0", "-pthread", "-fpermissive", "-fdeclspec", "-w",
    "-Wno-c++11-narrowing", "-DROBLOX_BOOST_CONFIGS", "-DRBX_SIMD_USE_SSE",
    "-msimd128", "-msse2", "-sUSE_SDL=2",
]

EXCLUDE = [
    "CrashReporter.cpp",         # Windows crash uploader
    "NetPmc.cpp",                # anti-cheat, not used offline
    "NetworkClusterPacketCache.cpp",  # terrain streaming, not used offline
    "API.cpp",                   # network version handshake, not used offline
    "GameConfigurer.cpp",        # Windows launcher configuration
    "raknet/",
]


def sources() -> list[str]:
    text = (NET / "Network.vcxproj").read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'<ClCompile Include="([^"]+)"', text):
        rel = m.group(1).replace("\\", "/")
        if any(x in rel for x in EXCLUDE):
            continue
        if (NET / rel).is_file() and "/" not in rel:
            out.append(rel)
    return out


def compile_one(rel: str, force: bool = False) -> tuple[str, int, str]:
    src = NET / rel
    patched = PATCH / "Network" / rel
    if patched.is_file():
        src = patched
    obj = OUT / (rel + ".o")
    if obj.is_file() and not force:
        return rel, 0, ""
    obj.parent.mkdir(parents=True, exist_ok=True)
    is_c = src.suffix == ".c"
    cmd = [str(EMCC if is_c else EMXX)]
    if is_c:
        cmd += ["-std=c99", "-O0", "-pthread", "-w"]
    else:
        cmd += CXXFLAGS
    if rel.lower() == "player.cpp":
        cmd += ["-O2", "-fno-strict-aliasing"]
    for inc in INCLUDES:
        cmd += ["-I", str(inc)]
    cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
    log = LOGS / ("net_" + rel.replace("/", "_") + ".log")
    env = os.environ.copy()
    env["PATH"] = ("/home/raul/emsdk:/home/raul/emsdk/upstream/emscripten:"
                   "/home/raul/python312/bin:" + env.get("PATH", ""))
    with log.open("w") as lf:
        try:
            r = subprocess.run(cmd, stdout=lf, stderr=lf, timeout=900, env=env)
            return rel, r.returncode, str(log)
        except subprocess.TimeoutExpired:
            return rel, 124, str(log)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    rels = sources()
    if a.only:
        rels = [r for r in rels if a.only.lower() in r.lower()]
    print(f"network sources: {len(rels)}", flush=True)
    failures = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(compile_one, r, a.force) for r in rels]):
            rel, rc, log = fut.result()
            done += 1
            if rc:
                failures.append((rel, rc, log))
                print("FAIL", rel, log, flush=True)
            if done % 10 == 0 or done == len(rels):
                print(f"  {done}/{len(rels)}", flush=True)
    print(f"\n{len(rels)-len(failures)}/{len(rels)} OK, {len(failures)} failed", flush=True)
    (ROOT / "port" / "out" / "net-result.json").write_text(json.dumps(failures, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
