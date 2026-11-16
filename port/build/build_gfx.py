#!/usr/bin/env python3
"""Compile the 2016 graphics stack (GfxCore GL, GfxRender, GfxBase, AppDraw)."""
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
SHIM = ROOT / "port" / "shim" / "caseinclude"
PATCH = ROOT / "port" / "src_patch"
OUT = ROOT / "port" / "out" / "gfxobj"
LOGS = ROOT / "port" / "out" / "gfxlogs"
BOOST = Path("/home/raul/boost_1_56_0")
EMXX = Path("/home/raul/emsdk/upstream/emscripten/em++")
EMCC = Path("/home/raul/emsdk/upstream/emscripten/emcc")

INCLUDES = [
    SHIM,
    ENGINE / "App" / "include",
    ENGINE / "App",
    ENGINE / "Base" / "include",
    ENGINE / "Rendering" / "g3d" / "include",
    ENGINE / "Rendering" / "RbxG3D" / "include",
    ENGINE / "Rendering" / "GfxBase" / "include",
    ENGINE / "Rendering" / "GfxCore" / "include",
    ENGINE / "Rendering" / "GfxCore",
    ENGINE / "Rendering" / "GfxCore" / "GL",
    ENGINE / "Rendering" / "GfxRender",
    ENGINE / "Rendering",
    ENGINE / "Rendering" / "GfxRender" / "freetype" / "include",
    ENGINE / "Rendering" / "GfxRender" / "freetype" / "include" / "freetype" / "config",
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
    "-fwasm-exceptions",
    "-O0",
    "-pthread",
    "-fpermissive",
    "-fdeclspec",
    "-w",
    "-Wno-c++11-narrowing",
    "-DROBLOX_BOOST_CONFIGS",
    "-DRBX_SIMD_USE_SSE",
    "-msimd128",
    "-msse2",
    "-sUSE_SDL=2",
    "-sUSE_GLFW=3",
]

GL_FLAGS = ["-D__ANDROID__"]


def project_sources(path: Path, root: Path) -> list[Path]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'<ClCompile Include="([^"]+)"', text):
        rel = m.group(1).replace("\\", "/")
        if rel.startswith("$("):
            continue
        p = root / rel
        if p.is_file():
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    groups: list[tuple[Path, list[str]]] = []
    core = [
        ENGINE / "Rendering" / "GfxCore" / "Device.cpp",
        ENGINE / "Rendering" / "GfxCore" / "DeviceCreate.cpp",
        ENGINE / "Rendering" / "GfxCore" / "Framebuffer.cpp",
        ENGINE / "Rendering" / "GfxCore" / "Geometry.cpp",
        ENGINE / "Rendering" / "GfxCore" / "Resource.cpp",
        ENGINE / "Rendering" / "GfxCore" / "Shader.cpp",
        ENGINE / "Rendering" / "GfxCore" / "States.cpp",
        ENGINE / "Rendering" / "GfxCore" / "Texture.cpp",
        ENGINE / "Rendering" / "GfxCore" / "pix.cpp",
    ]
    gl = [
        ENGINE / "Rendering" / "GfxCore" / "GL" / "DeviceGL.cpp",
        ENGINE / "Rendering" / "GfxCore" / "GL" / "DeviceContextGL.cpp",
        ENGINE / "Rendering" / "GfxCore" / "GL" / "FramebufferGL.cpp",
        ENGINE / "Rendering" / "GfxCore" / "GL" / "GeometryGL.cpp",
        ENGINE / "Rendering" / "GfxCore" / "GL" / "ShaderGL.cpp",
        ENGINE / "Rendering" / "GfxCore" / "GL" / "TextureGL.cpp",
        ROOT / "port" / "src" / "WebClient" / "ContextGLEmscripten.cpp",
        ROOT / "port" / "src" / "WebClient" / "gl_stubs.cpp",
    ]
    for p in core:
        groups.append((p, []))
    for p in gl:
        groups.append((p, GL_FLAGS))
    groups.append((ENGINE / "ClientBase" / "RenderSettingsItem.cpp", []))
    for proj, root in [
        (ENGINE / "Rendering" / "GfxRender" / "GfxRender.vcxproj", ENGINE / "Rendering" / "GfxRender"),
        (ENGINE / "Rendering" / "GfxBase" / "GfxBase.vcxproj", ENGINE / "Rendering" / "GfxBase"),
        (ENGINE / "Rendering" / "AppDraw" / "AppDraw.vcxproj", ENGINE / "Rendering" / "AppDraw"),
        (ENGINE / "Rendering" / "RbxG3D" / "RbxG3D.vcxproj", ENGINE / "Rendering" / "RbxG3D"),
    ]:
        for p in project_sources(proj, root):
            groups.append((p, []))

    if a.only:
        groups = [(p, f) for (p, f) in groups if a.only.lower() in p.as_posix().lower()]

    def compile_group(item):
        src, extra = item
        rel = src.relative_to(ENGINE).as_posix() if src.is_relative_to(ENGINE) else src.name
        patched = PATCH / rel
        if patched.is_file():
            src = patched
        obj = OUT / (rel + ".o")
        if obj.is_file() and not a.force:
            return rel, 0, ""
        obj.parent.mkdir(parents=True, exist_ok=True)
        is_c = src.suffix == ".c"
        cmd = [str(EMCC if is_c else EMXX)]
        if is_c:
            cmd += ["-std=c99", "-O0", "-pthread", "-w", "-fwasm-exceptions", "-DFT2_BUILD_LIBRARY"]
        else:
            cmd += CXXFLAGS + extra + ["-O2", "-fno-strict-aliasing"]
        for inc in INCLUDES:
            cmd += ["-I", str(inc)]
        cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
        log = LOGS / (rel.replace("/", "_") + ".log")
        env = os.environ.copy()
        env["PATH"] = ("/home/raul/emsdk:/home/raul/emsdk/upstream/emscripten:"
                       "/home/raul/python312/bin:" + env.get("PATH", ""))
        with log.open("w") as lf:
            try:
                r = subprocess.run(cmd, stdout=lf, stderr=lf, timeout=900, env=env)
                return rel, r.returncode, str(log)
            except subprocess.TimeoutExpired:
                return rel, 124, str(log)

    print(f"gfx sources: {len(groups)}", flush=True)
    failures = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futures = [ex.submit(compile_group, g) for g in groups]
        for fut in concurrent.futures.as_completed(futures):
            rel, rc, log = fut.result()
            done += 1
            if rc:
                failures.append((rel, rc, log))
                print("FAIL", rel, log, flush=True)
            if done % 10 == 0 or done == len(groups):
                print(f"  {done}/{len(groups)}", flush=True)
    print(f"\n{len(groups)-len(failures)}/{len(groups)} OK, {len(failures)} failed", flush=True)
    (ROOT / "port" / "out" / "gfx-result.json").write_text(json.dumps(failures, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
