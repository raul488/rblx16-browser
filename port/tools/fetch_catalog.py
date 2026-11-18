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

# Classic body packages ("Man", "Woman", ...) are bundles of body-part meshes.
BODY_PARTS = {27: 1, 28: 3, 29: 2, 30: 4, 31: 5}  # assetType -> CharacterMesh BodyPart
HEAD_MESH_TYPE = 17
WEARABLE_TYPES = {2: "tshirt", 11: "shirt", 12: "pants", 18: "face", 8: "hat",
                  41: "hair", 43: "back", 44: "shoulder", 45: "back", 46: "back",
                  47: "back", 42: "face"}
BUNDLE_SEARCH = ("https://catalog.roblox.com/v1/search/items/details"
                 "?Category=18&Subcategory=37&CreatorTargetId=1&CreatorType=User"
                 "&Limit=30&IncludeNotForSale=true")
# Items are stored as catalog/item/<id> with no extension, so any static host
# can serve them at the URL the engine asks for.
ITEM_DIR = "item"

ASSET_CACHE = os.path.join(ROOT, "web", "asset-cache")
UA = {"User-Agent": "Mozilla/5.0 (rblx2016-catalog)"}
COOKIE = None
_last = {}
_last_lock = threading.Lock()


def headers():
    return {**UA, "Cookie": f".ROBLOSECURITY={COOKIE}"} if COOKIE else dict(UA)


def throttle(url):
    """catalog.roblox.com allows 12 requests/minute per IP; the rest are roomy.

    Slots are reserved under the lock so the download pool can share one host
    budget instead of sleeping in lockstep.
    """
    host = urllib.parse.urlsplit(url).netloc
    gap = 5.5 if host == "catalog.roblox.com" else 0.12
    with _last_lock:
        now = time.monotonic()
        wait = _last.get(host, 0) + gap - now
        _last[host] = max(now, _last.get(host, 0) + gap)
    if wait > 0:
        time.sleep(wait)


def fetch(url, tries=5):
    for attempt in range(1, tries + 1):
        throttle(url)
        try:
            req = urllib.request.Request(url, headers=headers())
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
                for _ in range(3):
                    if data[:2] != b"\x1f\x8b":
                        break
                    try:
                        unzipped = gzip.decompress(data)
                    except OSError:
                        break
                    if len(unzipped) < 16 or unzipped == data:
                        break
                    data = unzipped
                return data
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    f"{e.code} for {url[:70]} - Roblox only serves user-uploaded "
                    "assets to a logged-in session; pass --cookie")
            if e.code == 404 or attempt == tries:
                raise
            delay = float(e.headers.get("retry-after") or 60) if e.code == 429 else 2 ** attempt
            print(f"  {e.code} on {url[:60]}... waiting {delay:.0f}s", file=sys.stderr)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == tries:
                raise
            time.sleep(2 ** attempt)


def get_json(url):
    return json.loads(fetch(url))


def cached_json(url, key):
    """Details lookups are the slow part of a rerun; keep them in the catalog dir."""
    path = os.path.join(OUT, ".cache", key + ".json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            pass
    data = get_json(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def search(creator, category, subcategory):
    """Yield every catalog item of one category for one creator."""
    cursor = None
    while True:
        url = ("https://catalog.roblox.com/v1/search/items/details"
               f"?Category={category}&Subcategory={subcategory}"
               f"&CreatorTargetId={creator}&CreatorType=User&Limit=30&IncludeNotForSale=true")
        if cursor:
            url += "&Cursor=" + urllib.parse.quote(cursor)
        page = get_json(url)
        for item in page.get("data", []):
            if item.get("itemType") == "Asset":
                yield item
        cursor = page.get("nextPageCursor")
        if not cursor:
            return


def template_image(asset_id):
    """Classic clothing/face assets wrap the actual texture; return the image."""
    data = fetch(f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}")
    if data[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
        return data
    text = data.decode("utf-8", "replace")
    m = re.search(r"roblox\.com/asset/[^<\s]*?[?&]id=(\d+)", text) or re.search(
        r"rbxassetid://(\d+)", text)
    if not m or m.group(1) == str(asset_id):
        return data  # a plain decal XML is still usable by the client
    return fetch(f"https://assetdelivery.roblox.com/v1/asset?id={m.group(1)}")


def normalize_model_urls(data):
    """Some 2007-era models store "id= 12345" with a space; the engine can't parse it."""
    if data[:8] != b"<roblox ":
        return data
    text = data.decode("utf-8", "replace")
    fixed = re.sub(r"(id=)\s+(\d)", r"\1\2", text)
    return fixed.encode("utf-8") if fixed != text else data


def is_image(data):
    return data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff"


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name or "").strip(" .")[:60]


def download(url, path, skip_existing=True):
    if skip_existing and os.path.isfile(path) and os.path.getsize(path) > 0:
        return True
    data = fetch(url)
    if not data:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


def creator_items(types):
    """Collect catalog-listed items (clothing + hats) for every creator."""
    items = []
    for cid, cname in CREATORS.items():
        for kind in types:
            if kind not in CATEGORIES or CATEGORIES[kind] is None:
                continue
            cache = os.path.join(OUT, ".cache", f"list-{cid}-{kind}.json")
            if os.path.isfile(cache):
                with open(cache, encoding="utf-8") as f:
                    cached = json.load(f)
                items.extend(cached)
                print(f"  {cname:22} {kind:7} {len(cached)} (cached)")
                continue
            category, subcategory = CATEGORIES[kind]
            found = 0
            this = []
            for item in search(cid, category, subcategory):
                created = (item.get("itemCreatedUtc") or "")[:10]
                if created and created > CUTOFF:
                    continue
                this.append({
                    "id": item["id"],
                    "type": kind,
                    "name": item.get("name") or "",
                    "creator": cname,
                    "created": created,
                })
                found += 1
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump(this, f)
            items.extend(this)
            print(f"  {cname:22} {kind:7} {found}")
    return items


def face_items():
    """Classic faces: ids from the community archive, dated and fetched from Roblox."""
    archive = get_json(FACE_ARCHIVE)
    print(f"  archive: {len(archive)} classic faces")
    items = []
    for entry in archive:
        aid = entry.get("id")
        if not aid:
            continue
        try:
            details = cached_json(f"https://economy.roblox.com/v2/assets/{aid}/details",
                                  f"asset-{aid}")
        except Exception as e:
            print(f"  face {aid} details failed: {e}", file=sys.stderr)
            continue
        if details.get("AssetTypeId") != 18:
            continue
        created = (details.get("Created") or "")[:10]
        if created and created > CUTOFF:
            continue
        items.append({
            "id": aid,
            "type": "face",
            "name": details.get("Name") or entry.get("name") or "",
            "creator": (details.get("Creator") or {}).get("Name") or "Roblox",
            "created": created,
        })
    print(f"  faces  2016 and older: {len(items)}")
    return items


def asset_details(asset_id):
    return cached_json(f"https://economy.roblox.com/v2/assets/{asset_id}/details",
                       f"asset-{asset_id}")


def asset_bytes(asset_id):
    """Downloaded asset bytes (cached on disk)."""
    asset_id = str(asset_id)
    path = os.path.join(fc_cache(), asset_id)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return open(path, "rb").read()
    data = fetch(f"https://assetdelivery.roblox.com/v1/asset?id={asset_id}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data


def fc_cache():
    return os.path.join(ROOT, "web", "asset-cache")


def mesh_and_texture(asset_id):
    """A body-part asset is a small model holding the mesh id and its texture."""
    try:
        blob = asset_bytes(asset_id)
    except Exception:
        return None
    mesh = texture = None
    for ref in {m.decode() for m in ASSET_REF.findall(blob)}:
        if ref == str(asset_id):
            continue
        try:
            data = asset_bytes(ref)
        except Exception:
            continue
        head = data[:8].lstrip()
        if head[:7].lower() == b"version":
            mesh = mesh or int(ref)
        elif data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff":
            texture = texture or int(ref)
    if mesh:
        return {"mesh": mesh, "texture": texture or 0}
    return None


def bundle_items():
    """Classic character packages: bundles whose body parts predate 2017."""
    cursor, bundles = None, []
    while True:
        url = BUNDLE_SEARCH + ("&Cursor=" + urllib.parse.quote(cursor) if cursor else "")
        page = get_json(url)
        for item in page.get("data", []):
            if item.get("itemType") == "Bundle":
                bundles.append(item)
        cursor = page.get("nextPageCursor")
        if not cursor:
            break
    print(f"  body bundles found: {len(bundles)}")

    # Bundle details are limited to 12 requests/minute, so walk the classic
    # ones first (low bundle ids) and stop once enough packages qualify.
    bundles.sort(key=lambda b: b["id"])
    max_packages = 60
    max_bundles = 260
    packages = []
    for index, bundle in enumerate(bundles):
        if len(packages) >= max_packages or index >= max_bundles:
            break
        bid = bundle["id"]
        try:
            detail = cached_json(f"https://catalog.roblox.com/v1/bundles/{bid}/details",
                                 f"bundle-{bid}")
        except Exception as e:
            print(f"  bundle {bid} details failed: {e}", file=sys.stderr)
            continue
        parts, extras, created = [], [], ""
        for part in detail.get("items", []):
            atype, aid = part.get("assetType"), part.get("id")
            if not atype or not aid:
                continue
            if atype not in BODY_PARTS and atype != HEAD_MESH_TYPE and atype not in WEARABLE_TYPES:
                continue
            try:
                info = asset_details(aid)
            except Exception:
                continue
            when = (info.get("Created") or "")[:10]
            if when and when > CUTOFF:
                continue  # post-2016 part: skip it, keep the rest
            if atype in BODY_PARTS:
                created = created or when
                parts.append({"part": BODY_PARTS[atype], "asset": aid})
            elif atype == HEAD_MESH_TYPE:
                extras.append({"id": aid, "type": "packhead", "created": when})
            else:
                extras.append({"id": aid, "type": WEARABLE_TYPES[atype], "created": when})
        if len(parts) < 3:
            continue  # not a usable R6 body
        pkg = {
            "id": bid,
            "type": "package",
            "name": (detail.get("name") or bundle.get("name") or "").strip(),
            "creator": "Roblox",
            "created": created,
            "parts": sorted(parts, key=lambda p: p["part"]),
            "extras": [{"id": e["id"], "type": e["type"]} for e in extras],
        }
        packages.append(pkg)
        print(f"  package {bid:>6} {pkg['name'][:28]:28} parts={len(parts)} extras={len(extras)} {created}")

    # Body-part models are fetched by the engine from catalog/item/<asset id>.
    for pkg in packages:
        for part in pkg["parts"]:
            path = os.path.join(OUT, ITEM_DIR, str(part["asset"]))
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                try:
                    data = asset_bytes(part["asset"])
                except Exception as e:
                    print(f"  part {part['asset']} failed: {e}", file=sys.stderr)
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)

    # Worn package parts are also listed as normal items so they can be worn
    # on their own; they need catalog item files like everything else.
    extra_items = []
    for pkg in packages:
        for extra in pkg["extras"]:
            if extra["type"] == "packhead":
                continue
            extra_items.append({"id": extra["id"], "type": extra["type"],
                                "name": f"{pkg['name']} part", "creator": "Roblox",
                                "created": pkg["created"]})
    return packages, extra_items


def bundle_thumbnails(packages):
    """Bundle images come from a different endpoint than asset thumbnails."""
    if not packages:
        return
    ids = [p["id"] for p in packages]
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        data = get_json("https://thumbnails.roblox.com/v1/bundles/thumbnails?bundleIds="
                        + ",".join(map(str, chunk))
                        + "&size=150x150&format=Png&isCircular=false").get("data", [])
        for entry in data:
            url = entry.get("imageUrl")
            if not url:
                continue
            try:
                download(url, os.path.join(OUT, "thumbs", f"bundle-{entry['targetId']}.png"))
            except Exception as e:
                print(f"  bundle thumbnail {entry.get('targetId')} failed: {e}", file=sys.stderr)


def thumbnails(items):
    """Batch the 150x150 catalog thumbnails; they are public CDN URLs."""
    ids = [it["id"] for it in items if it.get("type") != "package"]
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
    ap.add_argument("--types", default="shirt,tshirt,pants,hat,hair,shoulder,back,face,package")
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
    unknown = [t for t in types if t not in CATEGORIES and t != "package"]
    if unknown:
        sys.exit(f"unknown types: {', '.join(unknown)}")

    items = []
    packages = []
    print("catalog listings:")
    items += creator_items([t for t in types if t in CATEGORIES])
    if "face" in types:
        print("classic faces:")
        items += face_items()
    if "package" in types:
        print("character packages:")
        packages, extra_items = bundle_items()
        items += extra_items

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

    for pkg in packages:
        manifest.append({**pkg, "thumb": f"thumbs/bundle-{pkg['id']}.png"})

    # Manifest first: a thumbnail hiccup must not cost us the catalog.
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump({"items": manifest}, f, indent=1)
    print(f"saved {ok}, failed {failed} -> {os.path.join(OUT, 'manifest.json')}")

    bundle_thumbnails([p for p in manifest if p.get("type") == "package"])

    print("thumbnails:")
    thumbnails(manifest)

    print("hat meshes/textures:")
    prefetch_assets(hat_assets(manifest))
    print("done")


if __name__ == "__main__":
    main()
