#!/usr/bin/env python3
"""Generate a case-insensitive header mirror for the 2016 engine tree.

Windows include paths are case-insensitive; Linux/Emscripten are not.  A
mirror of every header/inline file is created under port/shim/caseinclude,
using the file's real relative path.  Mixed-case include spellings found in
the sources get directory/file symlink aliases.  Because the mirror
directories are real directories, headers that use relative includes (for
example "SerializerV2.h" or "ChunkMap.inl") still resolve.

Patched headers live in port/shim/patched and are placed first on the
include path.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
OUT = ROOT / "port" / "shim" / "caseinclude"

INCLUDE_ROOTS = [
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
    ENGINE / "Network",
    ENGINE / "Network" / "raknet" / "Source",
    ENGINE / "ClientShared",
    ENGINE / "Log" / "include",
    ENGINE / "App.BulletPhysics",
    ENGINE / "Win",
]

SCAN_DIRS = [
    ENGINE / "App",
    ENGINE / "Base",
    ENGINE / "Rendering",
    ENGINE / "Log",
    ENGINE / "ClientShared",
    ENGINE / "Network",
    ENGINE / "Network" / "raknet" / "Source",
    ENGINE / "App.BulletPhysics",
]

HEADER_SUFFIXES = {".h", ".hpp", ".inl", ".inc"}
SOURCE_SUFFIXES = HEADER_SUFFIXES | {".c", ".cc", ".cpp"}
INCLUDE_RE = re.compile(rb'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def build_canonical() -> dict[str, tuple[str, Path]]:
    canonical: dict[str, tuple[str, Path]] = {}
    for root in INCLUDE_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in HEADER_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            key = rel.lower()
            if key not in canonical:
                canonical[key] = (rel, path)
    return canonical


def link_dir_alias(path: Path, target: Path) -> None:
    if path.exists() or path.is_symlink():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rel_target = os.path.relpath(target, path.parent)
    os.symlink(rel_target, path)


def main() -> int:
    canonical = build_canonical()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    # 1. Mirror every header at its real path.
    for rel, real in canonical.values():
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(real, dst)
    print(f"canonical headers: {len(canonical)}")

    # 2. Scan source for mixed-case spellings and create aliases.
    seen: set[str] = set()
    aliases = 0
    unresolved: set[str] = set()
    for scan in SCAN_DIRS:
        if not scan.is_dir():
            continue
        for src in scan.rglob("*"):
            if src.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            try:
                data = src.read_bytes()
            except OSError:
                continue
            for match in INCLUDE_RE.finditer(data):
                requested = match.group(1).decode("utf-8", "replace").strip()
                if not requested or requested.startswith("/") or requested.startswith(".."):
                    continue
                requested = requested.replace("\\", "/")
                if requested in seen:
                    continue
                seen.add(requested)
                entry = canonical.get(requested.lower())
                if entry is None and "/" not in requested:
                    # A bare file name can resolve to a header in a subdirectory
                    # (for example LuaConf.h -> Lua-5.1.4/src/luaconf.h).
                    matches = [k for k in canonical if k.rsplit("/", 1)[-1] == requested.lower()]
                    if matches:
                        entry = canonical[matches[0]]
                if entry is None:
                    if "/" in requested or requested.endswith((".h", ".hpp", ".inl")):
                        unresolved.add(requested)
                    continue
                rel, real = entry
                if "/" not in requested and requested.lower() not in {
                    "math.h", "string.h", "stdio.h", "stdlib.h", "time.h",
                    "ctype.h", "assert.h", "errno.h", "float.h", "limits.h",
                    "stddef.h", "stdint.h", "stdarg.h", "unistd.h", "zlib.h",
                }:
                    dst = OUT / requested
                    if not (dst.exists() or dst.is_symlink()):
                        os.symlink(real, dst)
                        aliases += 1
                    continue
                req_parts = requested.split("/")
                can_parts = rel.split("/")
                if len(req_parts) != len(can_parts):
                    # A different root with the same basename; skip.
                    continue
                # Create directory aliases for every differing prefix.
                for i in range(len(req_parts) - 1):
                    rp = "/".join(req_parts[: i + 1])
                    cp = "/".join(can_parts[: i + 1])
                    if rp != cp:
                        link_dir_alias(OUT / rp, OUT / cp)
                # File-name alias if only the basename differs.
                if req_parts[-1] != can_parts[-1]:
                    dst = OUT / "/".join(can_parts[:-1] + [req_parts[-1]])
                    if not (dst.exists() or dst.is_symlink()):
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        os.symlink(real, dst)
                        aliases += 1

    print(f"include spellings seen: {len(seen)}")
    print(f"file aliases created:   {aliases}")
    print(f"not found in engine:    {len(unresolved)}")

    # 3. Apply patched headers into the mirror.  One physical patched file is
    # written per original header; every mixed-case alias becomes a symlink to
    # that file so #pragma once and relative includes behave.
    patched_dir = ROOT / "port" / "shim" / "patched"
    patched_count = 0
    if patched_dir.is_dir():
        patched_by_original: dict[Path, Path] = {}
        canonical_rel: dict[Path, str] = {}
        for pf in patched_dir.rglob("*"):
            if not pf.is_file():
                continue
            rel = pf.relative_to(patched_dir).as_posix()
            entry = canonical.get(rel.lower())
            if not entry:
                continue
            original = entry[1].resolve()
            if original not in patched_by_original:
                patched_by_original[original] = pf
                canonical_rel[original] = rel

        # Write the canonical patched file for each original.
        for original, pf in patched_by_original.items():
            rel = canonical_rel[original]
            dst = OUT / rel
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(pf.read_bytes())
            patched_count += 1

        # Replace every alias symlink that points at a patched original with a
        # symlink to the canonical patched copy.
        for link in OUT.rglob("*"):
            if not link.is_symlink():
                continue
            try:
                target = link.resolve()
            except OSError:
                continue
            if target in patched_by_original:
                canonical_path = OUT / canonical_rel[target]
                link.unlink()
                link.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(os.path.relpath(canonical_path, link.parent), link)
                patched_count += 1

        # Patched files that are entirely new (not in the source tree).
        for pf in patched_dir.rglob("*"):
            if not pf.is_file():
                continue
            rel = pf.relative_to(patched_dir).as_posix()
            if rel.lower() in canonical:
                continue
            dst = OUT / rel
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(pf.read_bytes())
            patched_count += 1

    print(f"patched headers applied: {patched_count}")
    for name in sorted(unresolved)[:60]:
        print("  ?", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
