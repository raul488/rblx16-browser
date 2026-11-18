#!/usr/bin/env python3
"""Pack the assets the catalog models and bundled places reference.

    python3 port/tools/pack_assets.py

Writes web/assets/<id>.gz for every rbxassetid a scraped catalog accessory
model or a file in web/places/ points at, plus web/assets/index.json. The site
serves those files and the service worker answers /asset/?id=N from them, so a
player only downloads the items they actually wear (and the places they open),
instead of the whole asset set.

Meshes compress about 8:1; textures are already PNG. Anything missing from
web/asset-cache is downloaded first (needs the same Roblox session cookie as
fetch_catalog.py).
"""
import gzip
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_catalog as fc

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "web", "catalog")
PLACES = os.path.join(ROOT, "web", "places")
OUT = os.path.join(ROOT, "web", "assets")
CACHE = os.path.join(ROOT, "web", "asset-cache")
MODEL_TYPES = {"hat", "hair", "shoulder", "back"}

ASSET_REF = re.compile(rb"(?:roblox\.com/asset/[^\s<\"']*?[?&]id=|rbxassetid://)(\d{1,20})")


def catalog_ids():
    path = os.path.join(CATALOG, "manifest.json")
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        items = json.load(f).get("items", [])
    ids = set()
    for item in items:
        if item.get("type") == "package":
            for part in item.get("parts", []):
                for key in ("mesh", "texture"):
                    if part.get(key):
                        ids.add(str(part[key]))
            for extra in item.get("extras", []):
                if extra.get("type") == "packhead":
                    ids.add(str(extra["id"]))
            continue
        if item.get("type") not in MODEL_TYPES:
            continue
        try:
            with open(os.path.join(CATALOG, item["file"]), "rb") as f:
                ids.update(m.decode() for m in ASSET_REF.findall(f.read()))
        except OSError:
            pass
    return ids


def cached_ids():
    try:
        return {name for name in os.listdir(CACHE) if name.isdigit()}
    except OSError:
        return set()


def place_ids():
    ids = set()
    for name in os.listdir(PLACES):
        if not name.lower().endswith((".rbxl", ".rbxlx")):
            continue
        with open(os.path.join(PLACES, name), "rb") as f:
            ids.update(m.decode() for m in ASSET_REF.findall(f.read()))
    return ids


def main():
    sys.stdout.reconfigure(line_buffering=True)
    fc.OUT = CATALOG
    os.environ.setdefault("ROBLOX_COOKIE", "")
    if not fc.COOKIE:
        for candidate in ("asset-cookie.txt", "asset-cookie"):
            path = os.path.join(ROOT, candidate)
            if os.path.isfile(path):
                fc.COOKIE = open(path, encoding="utf-8", errors="ignore").read().strip() or None
                if fc.COOKIE:
                    break
    print("cookie:", "yes" if fc.COOKIE else "no")

    # Everything the places and catalog reference, plus anything already cached
    # from a local session: place scripts build some asset ids at runtime, so a
    # text scan alone misses them.
    ids = sorted(catalog_ids() | place_ids() | cached_ids())
    print(f"{len(ids)} asset ids referenced by the catalog, places and cache")

    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    missing = [i for i in ids if not os.path.isfile(os.path.join(CACHE, i))]
    if missing:
        print(f"downloading {len(missing)} uncached assets")
        fc.prefetch_assets(missing)

    packed = skipped = empty = 0
    for asset_id in ids:
        source = os.path.join(CACHE, asset_id)
        target = os.path.join(OUT, asset_id + ".gz")
        if os.path.isfile(target) and os.path.getmtime(target) >= os.path.getmtime(source):
            skipped += 1
            continue
        try:
            data = open(source, "rb").read()
        except OSError:
            empty += 1
            continue
        with open(target, "wb") as f:
            f.write(gzip.compress(data, 9))
        packed += 1

    present = sorted(i for i in ids if os.path.isfile(os.path.join(OUT, i + ".gz")))
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump({"ids": present}, f)
    size = sum(os.path.getsize(os.path.join(OUT, i + ".gz")) for i in present)
    print(f"packed {packed}, unchanged {skipped}, unavailable {empty}")
    print(f"web/assets: {len(present)} files, {size / 1048576:.1f} MB")


if __name__ == "__main__":
    main()
