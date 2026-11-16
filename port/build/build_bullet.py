#!/usr/bin/env python3
"""Compile the bundled Bullet core used by the 2016 physics world."""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "roblox-2016-source-code-master"
BULLET = ENGINE / "App.BulletPhysics"
OUT = ROOT / "port" / "out" / "obj" / "bullet"
LOGS = ROOT / "port" / "out" / "logs"
BOOST = Path("/home/raul/boost_1_56_0")
EMXX = Path("/home/raul/emsdk/upstream/emscripten/em++")

INCLUDES = [
    ROOT / "port" / "shim" / "caseinclude",
    BULLET,
    BULLET / "LinearMath",
    BULLET / "BulletCollision",
    BULLET / "BulletDynamics",
    BULLET / "Extras" / "ConvexDecomposition",
    ENGINE / "App" / "include",
    ENGINE / "Base" / "include",
]

CXXFLAGS = [
    "-std=c++11",
    "-fwasm-exceptions", "-O0", "-pthread", "-fpermissive", "-fdeclspec", "-w",
    "-Wno-c++11-narrowing", "-DROBLOX_BOOST_CONFIGS",
    "-msimd128", "-msse2", "-sUSE_SDL=2",
]


def sources() -> list[Path]:
    text = (BULLET / "App.BulletPhysics.vcxproj").read_text(encoding="utf-8", errors="replace")
    out = []
    for m in re.finditer(r'<ClCompile Include="([^"]+)"', text):
        rel = m.group(1).replace("\\", "/")
        if not (rel.startswith("BulletCollision/") or rel.startswith("BulletDynamics/")
                or rel.startswith("LinearMath/") or rel.startswith("Extras/")):
            continue
        p = BULLET / rel
        if p.is_file():
            out.append(p)
    return out


def compile_one(src: Path, force: bool = False) -> tuple[str, int, str]:
    rel = src.relative_to(BULLET).as_posix()
    obj = OUT / (rel + ".o")
    if obj.is_file() and not force:
        return rel, 0, ""
    obj.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(EMXX)] + CXXFLAGS
    for inc in INCLUDES:
        cmd += ["-I", str(inc)]
    cmd += ["-isystem", str(BOOST), "-c", str(src), "-o", str(obj)]
    log = LOGS / ("bullet_" + rel.replace("/", "_") + ".log")
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
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    srcs = sources()
    print(f"bullet sources: {len(srcs)}", flush=True)
    failures = []
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(compile_one, s, a.force) for s in srcs]):
            rel, rc, log = fut.result()
            done += 1
            if rc:
                failures.append((rel, rc, log))
                print("FAIL", rel, log, flush=True)
            if done % 20 == 0 or done == len(srcs):
                print(f"  {done}/{len(srcs)}", flush=True)
    print(f"\n{len(srcs)-len(failures)}/{len(srcs)} OK, {len(failures)} failed", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
