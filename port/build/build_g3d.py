#!/usr/bin/env python3
"""Compile the G3D math/geometry library used by GfxCore and GfxRender."""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
G3D = ENGINE / "Rendering" / "g3d"
OUT = ROOT / "port" / "out" / "obj" / "g3d"
LOGS = ROOT / "port" / "out" / "logs"
BOOST = Path("/home/raul/boost_1_56_0")
EMXX = Path("/home/raul/emsdk/upstream/emscripten/em++")
EMCC = Path("/home/raul/emsdk/upstream/emscripten/emcc")

INCLUDES = [
    ROOT / "port" / "shim" / "caseinclude",
    ROOT / "port" / "src_patch",
    G3D / "include",
    G3D,
    G3D / "ijg",
    G3D / "png",
    ENGINE / "App" / "include",
    ENGINE / "App",
    ENGINE / "Base" / "include",
    ENGINE / "Rendering",
    ENGINE / "Rendering" / "RbxG3D" / "include",
    ENGINE / "Rendering" / "GfxBase" / "include",
    ENGINE / "Rendering" / "GfxCore" / "include",
    ENGINE / "Rendering" / "GfxRender",
    ENGINE / "Rendering" / "AppDraw" / "include",
    ENGINE / "Network" / "include",
    ENGINE / "Network",
    ENGINE / "ClientShared",
    ENGINE / "Log" / "include",
]

CXXFLAGS = [
    "-std=c++11",
    "-fwasm-exceptions", "-O0", "-pthread", "-fpermissive", "-fdeclspec", "-w",
    "-Wno-c++11-narrowing", "-DROBLOX_BOOST_CONFIGS", "-DRBX_SIMD_USE_SSE",
    "-msimd128", "-msse2", "-sUSE_SDL=2", "-sUSE_ZLIB=1",
]


def sources() -> list[Path]:
    text = (G3D / "graphics3D.vcxproj").read_text(encoding="utf-8", errors="replace")
    index = {p.relative_to(G3D).as_posix().lower(): p for p in G3D.rglob("*") if p.is_file()}
    out = []
    for m in re.finditer(r'<ClCompile Include="([^"]+)"', text):
        rel = m.group(1).replace("\\", "/")
        if rel.startswith("$(") or "win32" in rel.lower() or rel.startswith("zlib/"):
            continue
        p = index.get(rel.lower())
        if p is not None:
            out.append(p)
    return out


def compile_one(src: Path, force: bool = False) -> tuple[str, int, str]:
    rel = src.relative_to(G3D).as_posix()
    obj = OUT / (rel + ".o")
    if obj.is_file() and not force:
        return rel, 0, ""
    patched = ROOT / "port" / "src_patch" / "Rendering" / "g3d" / rel
    if patched.is_file():
        src = patched
    obj.parent.mkdir(parents=True, exist_ok=True)
    is_c = src.suffix == ".c"
    cmd = [str(EMCC if is_c else EMXX)]
    if is_c:
        cmd += ["-std=c99", "-O0", "-pthread", "-w", "-fwasm-exceptions", "-sUSE_ZLIB=1"]
    else:
        cmd += CXXFLAGS
    for inc in INCLUDES:
        cmd += ["-I", str(inc)]
    cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
    log = LOGS / ("g3d_" + rel.replace("/", "_") + ".log")
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
    srcs = sources()
    if a.only:
        srcs = [s for s in srcs if a.only.lower() in s.as_posix().lower()]
    print(f"g3d sources: {len(srcs)}", flush=True)
    failures = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(compile_one, s, a.force) for s in srcs]):
            rel, rc, log = fut.result()
            done += 1
            if rc:
                failures.append((rel, rc, log))
                print("FAIL", rel, log, flush=True)
            if done % 10 == 0 or done == len(srcs):
                print(f"  {done}/{len(srcs)}", flush=True)
    print(f"\n{len(srcs)-len(failures)}/{len(srcs)} OK, {len(failures)} failed", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
