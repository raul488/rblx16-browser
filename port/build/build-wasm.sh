#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PATH=/home/raul/python312/bin:$PATH
# shellcheck disable=SC1091
source /home/raul/emsdk/emsdk_env.sh >/dev/null 2>&1

echo "== case-insensitive include mirror"
python3 "$ROOT/port/tools/gen_ci_includes.py"

echo "== engine objects"
python3 "$ROOT/port/build/build.py" --project all --jobs 2

echo "== link WebAssembly"
python3 "$ROOT/port/build/link.py"

echo "== done: web/js/rblx2016.js + web/js/rblx2016.wasm"
