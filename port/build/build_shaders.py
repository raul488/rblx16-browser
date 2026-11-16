#!/usr/bin/env python3
"""Generate the runtime shader packs from the 2016 shader sources.

ShaderCompiler is the Windows tool built from Rendering/ShaderCompiler; the
Linux build used here is produced by the wasm spike.  Packs and shaders.json
are copied to web/shaders, which link.py preloads at /shaders.
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
OUT = ROOT / "web" / "shaders"
COMPILER = Path(os.environ.get("SHADER_COMPILER", "/home/raul/rblx2016-wasm-spike/ShaderCompiler"))
TARGETS = ("glsles3", "glsles")


HELPER_FIXES = [
    (
        "vec2 xll_matrixindex_mf2x2_i (mat2 m, int i) { vec2 v; v.x=m[0][i]; v.y=m[1][i]; return v; }",
        "vec2 xll_matrixindex_mf2x2_i (mat2 m, int i) { if (i==0) return vec2(m[0][0],m[1][0]); if (i==1) return vec2(m[0][1],m[1][1]); return vec2(0.0); }",
    ),
    (
        "vec3 xll_matrixindex_mf3x3_i (mat3 m, int i) { vec3 v; v.x=m[0][i]; v.y=m[1][i]; v.z=m[2][i]; return v; }",
        "vec3 xll_matrixindex_mf3x3_i (mat3 m, int i) { if (i==0) return vec3(m[0][0],m[1][0],m[2][0]); if (i==1) return vec3(m[0][1],m[1][1],m[2][1]); if (i==2) return vec3(m[0][2],m[1][2],m[2][2]); return vec3(0.0); }",
    ),
    (
        "vec4 xll_matrixindex_mf4x4_i (mat4 m, int i) { vec4 v; v.x=m[0][i]; v.y=m[1][i]; v.z=m[2][i]; v.w=m[3][i]; return v; }",
        "vec4 xll_matrixindex_mf4x4_i (mat4 m, int i) { if (i==0) return vec4(m[0][0],m[1][0],m[2][0],m[3][0]); if (i==1) return vec4(m[0][1],m[1][1],m[2][1],m[3][1]); if (i==2) return vec4(m[0][2],m[1][2],m[2][2],m[3][2]); if (i==3) return vec4(m[0][3],m[1][3],m[2][3],m[3][3]); return vec4(0.0); }",
    ),
]


def rewrite_pack(path: Path) -> None:
    """Make GL ES 3 accept HLSL2GLSL's dynamic matrix-index helpers."""
    data = path.read_bytes()
    if data[:4] != b"RBXS":
        raise RuntimeError(f"{path} is not a shader pack")
    count = struct.unpack_from("<I", data, 4)[0]
    entries = []
    for i in range(count):
        off = 8 + i * 96
        name = data[off:off + 64].split(b"\0")[0].decode()
        offset, size = struct.unpack_from("<II", data, off + 80)
        body = data[offset:offset + size].decode("utf-8", "replace")
        for old, new in HELPER_FIXES:
            body = body.replace(old, new)
        if name == "SkyVS":
            body = body.replace("out highp vec4 xlv_COLOR0;", "out highp vec4 xlv_COLOR0;\nout highp float xlv_PSIZE;")
            body = body.replace("gl_PointSize = float(xl_retval.PSize);", "gl_PointSize = float(xl_retval.PSize);\n    xlv_PSIZE = float(xl_retval.PSize);")
        if "precision " not in body[:600]:
            if body.startswith("#version 300 es"):
                body = body.replace("#version 300 es", "#version 300 es\nprecision highp float;\nprecision highp int;", 1)
            else:
                body = "precision highp float;\nprecision highp int;\n" + body
        entries.append((name, body.encode("utf-8")))
    entries.sort(key=lambda item: item[0])
    base = 8 + count * 96
    header = bytearray(b"RBXS" + struct.pack("<I", count))
    payload = bytearray()
    for name, body in entries:
        entry = bytearray(96)
        entry[:len(name)] = name.encode()
        struct.pack_into("<II", entry, 80, base + len(payload), len(body))
        header += entry
        payload += body
    path.write_bytes(bytes(header + payload))


def main() -> int:
    if not COMPILER.is_file():
        print(f"ShaderCompiler not found at {COMPILER}; set SHADER_COMPILER", file=sys.stderr)
        return 1
    if not (ENGINE / "shaders" / "shaders.json").is_file():
        print("engine shaders folder is missing", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rblx-shaders-") as tmp:
        work = Path(tmp) / "shaders"
        shutil.copytree(ENGINE / "shaders", work)
        r = subprocess.run([str(COMPILER), "/P", str(work)] + list(TARGETS))
        if r.returncode:
            print("shader compilation failed", file=sys.stderr)
            return r.returncode
        shutil.copy2(work / "shaders.json", OUT / "shaders.json")
        for target in TARGETS:
            pack = work / f"shaders_{target}.pack"
            if not pack.is_file():
                print(f"missing pack {pack}", file=sys.stderr)
                return 1
            rewrite_pack(pack)
            shutil.copy2(pack, OUT / pack.name)
            print("wrote", OUT / pack.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
