#!/usr/bin/env python3
"""Build the offline avatar catalog in web/catalog/ from the Roblox website.

    python3 port/tools/fetch_catalog.py            # everything, 2016 and older
    python3 port/tools/fetch_catalog.py --types face --limit 20

Collects classic Shirts, T-Shirts, Pants, head Hats and classic Faces made by
armitroner, the ROBLOX account and a list of 2016-era administrators, keeps
only items first created in 2016 or earlier, and writes:

    web/catalog/files/<type>/<id>.<png|rbxm|rbxmx>   template / hat model
    web/catalog/thumbs/<id>.png                      catalog thumbnail
    web/catalog/manifest.json                        what the page and the client read

Roblox only serves user-uploaded assets to a logged-in session, so pass
--cookie, set ROBLOX_COOKIE, or keep the cookie the page already saves in
asset-cookie.txt next to web/. Roblox's own items download without one.

Faces are no longer listed by catalog search (they were retired in 2026), so
their ids come from synpixel/roblox-classic-faces; each one is still dated and
downloaded from Roblox, with the archive PNG as a fallback.
"""
import argparse
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "web", "catalog")
FACE_ARCHIVE = ("https://raw.githubusercontent.com/synpixel/roblox-classic-faces/"
                "main/classic-faces.json")
FACE_ARCHIVE_PNG = ("https://raw.githubusercontent.com/synpixel/roblox-classic-faces/"
                    "main/classic-faces/{id}.png")
CUTOFF = "2016-12-31"

# 2016-era Roblox administrators. Edit freely; ids must be user ids.
CREATORS = {
    1: "Roblox",
    610948: "armitroner",
    156: "builderman",
    261: "Shedletsky",
    504316: "BrightEyes",
    861865: "Kiseki",
    29341155: "JacksSmirkingRevenge",
    13268404: "Sorcus",
    909739228: "erev6",
    1726: "knightmare",
}

# type -> (catalog Category, Subcategory) queried per creator
CATEGORIES = {
    "shirt": (3, 56),
    "tshirt": (3, 55),
    "pants": (3, 57),
    "hat": (11, 54),        # head
    "hair": (18, 20),   # Body > Hair
    "face": None,           # classic faces come from the archive below
    "shoulder": (11, 23),
    "back": (11, 25),
}

# types stored as engine models (rbxmx/rbxm) rather than a texture
MODEL_TYPES = {"hat", "hair", "shoulder", "back"}

def thumbnails(items):
    """Batch the 150x150 catalog thumbnails; they are public CDN URLs."""
    ids = [it["id"] for it in items]
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = get_json("https://thumbnails.roblox.com/v1/assets?assetIds="
                        + ",".join(map(str, chunk))
                        + "&size=150x150&format=Png&isCircular=false").get("data", [])
        by_id = {d.get("targetId"): d.get("imageUrl") for d in data}
        for aid in chunk:
            url = by_id.get(aid)
            if not url:
                continue
            try:
                download(url, os.path.join(OUT, "thumbs", f"{aid}.png"))
            except Exception as e:
                print(f"  thumbnail {aid} failed: {e}", file=sys.stderr)


ASSET_REF = re.compile(rb"(?:roblox\.com/asset/[^\s<\"]*?[?&]id=|rbxassetid://)(\d{1,20})")


def hat_assets(items):
    """Mesh/texture assets the hat models point at; fetched so hats work offline."""
    ids = set()
    for it in items:
        if it["type"] not in MODEL_TYPES:
            continue
        try:
            with open(os.path.join(OUT, it["file"]), "rb") as f:
                blob = f.read()
        except OSError:
            continue
        ids.update(m.decode() for m in ASSET_REF.findall(blob))
    return sorted(ids)


def prefetch_assets(ids):
    """Copy hat meshes/textures into web/asset-cache, decompressed."""
    def fetch_one(asset_id):
        path = os.path.join(ASSET_CACHE, asset_id)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return asset_id, "cached"
        try:
            data = fetch(f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}")
        except Exception as e:
            return asset_id, f"failed: {e}"
        os.makedirs(ASSET_CACHE, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return asset_id, "ok"

    counts = {"ok": 0, "cached": 0, "failed": 0}
    failures = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for asset_id, status in pool.map(fetch_one, ids):
            if status.startswith("failed"):
                counts["failed"] += 1
                failures.append(asset_id)
                print(f"  asset {asset_id} {status}", file=sys.stderr)
            else:
                counts[status] += 1
    print(f"  hat assets: {counts['ok']} fetched, {counts['cached']} cached, "
          f"{counts['failed']} unavailable")
    return failures


def main():
    sys.stdout.reconfigure(line_buffering=True)
    global OUT, COOKIE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cookie", help=".ROBLOSECURITY value (or ROBLOX_COOKIE env)")
    ap.add_argument("--out", default=OUT, help=f"output folder (default: {OUT})")
    ap.add_argument("--types", default="shirt,tshirt,pants,hat,hair,shoulder,back,face")
    ap.add_argument("--limit", type=int, default=0, help="debug: stop after N items per type")
    args = ap.parse_args()

    OUT = os.path.abspath(args.out)
    COOKIE = (args.cookie or os.environ.get("ROBLOX_COOKIE") or "").replace(
        ".ROBLOSECURITY=", "").strip() or None
    if not COOKIE:
        for path in (os.path.join(ROOT, "asset-cookie.txt"),
                     os.path.join(ROOT, "asset-cookie")):
            if os.path.isfile(path):
                COOKIE = open(path, encoding="utf-8", errors="ignore").read().strip() or None
                if COOKIE:
                    break
    print(f"cookie: {'yes' if COOKIE else 'no (user-uploaded assets will 401)'}")

    types = [t.strip().lower() for t in args.types.split(",") if t.strip()]
    unknown = [t for t in types if t not in CATEGORIES]
    if unknown:
        sys.exit(f"unknown types: {', '.join(unknown)}")

    items = []
    print("catalog listings:")
    items += creator_items([t for t in types if t in CATEGORIES])
    if "face" in types:
        print("classic faces:")
        items += face_items()

    # one entry per id; a face and a clothing item can never share an id
    seen = {}
    for it in items:
        seen.setdefault(it["id"], it)
    items = sorted(seen.values(), key=lambda i: (i["type"], i["created"], i["name"]))
    if args.limit:
        by_type = {}
        limited = []
        for it in items:
            n = by_type.get(it["type"], 0)
            if n < args.limit:
                by_type[it["type"]] = n + 1
                limited.append(it)
        items = limited
    print(f"{len(items)} items to save")

    def save_item(index_item):
        n, it = index_item
        aid, kind = it["id"], it["type"]
        rel = f"{ITEM_DIR}/{aid}"
        try:
            path = os.path.join(OUT, rel)
            if kind in MODEL_TYPES:
                if not os.path.isfile(path) or os.path.getsize(path) == 0:
                    data = normalize_model_urls(
                        fetch(f"https://assetdelivery.roblox.com/v1/asset?id={aid}"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
            else:
                path = os.path.join(OUT, rel)
                keep = os.path.isfile(path) and os.path.getsize(path) > 0
                if not keep:
                    try:
                        data = template_image(aid)
                    except Exception:
                        if kind != "face":
                            raise
                        data = fetch(FACE_ARCHIVE_PNG.format(id=aid))
                    if not is_image(data):
                        raise ValueError("asset did not yield an image")
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(data)
            return n, it, rel, None
        except Exception as e:
            return n, it, None, str(e)

    manifest = []
    ok = failed = 0
    total = len(items)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for n, it, rel, error in pool.map(save_item, enumerate(items, 1)):
            if error:
                failed += 1
                print(f"[{n}/{total}] {it['type']:6} {it['id']:>12} FAILED: {error}", file=sys.stderr)
                continue
            ok += 1
            print(f"[{n}/{total}] {it['type']:6} {it['id']:>12} {it['created']} {it['name'][:40]}")
            manifest.append({**it, "file": rel, "thumb": f"thumbs/{it['id']}.png"})

    # Manifest first: a thumbnail hiccup must not cost us the catalog.
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump({"items": manifest}, f, indent=1)
    print(f"saved {ok}, failed {failed} -> {os.path.join(OUT, 'manifest.json')}")

    print("thumbnails:")
    thumbnails(manifest)

    print("hat meshes/textures:")
    prefetch_assets(hat_assets(manifest))
    print("done")


if __name__ == "__main__":
    main()
