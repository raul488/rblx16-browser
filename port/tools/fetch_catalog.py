#!/usr/bin/env python3
"""Build the offline avatar catalog in web/catalog/ from the Roblox website.

    python3 port/tools/fetch_catalog.py            # everything, 2016 and older
    python3 port/tools/fetch_catalog.py --types face --limit 20

Collects classic Shirts, T-Shirts, Pants, head Hats and classic Faces made by
armitroner, the ROBLOX account, a list of 2016-era administrators and the
prolific pre-2016 clothing designers, keeps only items first created in 2016 or
earlier, and writes:

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
import html
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

# Classic-clothing designers who were already prolific before 2017. Clothing
# only, so their hats/hair do not flood the other tabs; names are the ones the
# clothes were sold under, not today's username (commented where they differ).
CLOTHING_CREATORS = {
    916: "Matt Dusek",            # 2006 account; Smiley Flower t-shirt
    469797: "VendettaTheGreat",   # 2008 Golden Shirt of Bling Bling
    119813: "Plad",               # 2008 armour shirts
    723341: "Keletho",            # 2009 skate jackets
    441825: "GPR3",
    926275: "1UK",
    3928497: "Shaddic",
    3487360: "ibloxu20",
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

# classic clothing asset types -> manifest type
CLOTHING_ASSET_TYPES = {2: "tshirt", 11: "shirt", 12: "pants"}

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
    cache_write(path, data)
    return data


def cache_write(path, data):
    """A stopped scrape must not leave a half-written cache behind."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def search(creator, category, subcategory=None):
    """Yield every catalog item of one category for one creator."""
    cursor = None
    while True:
        url = ("https://catalog.roblox.com/v1/search/items/details"
               f"?Category={category}")
        if subcategory is not None:
            url += f"&Subcategory={subcategory}"
        url += (f"&CreatorTargetId={creator}&CreatorType=User&Limit=30"
                "&IncludeNotForSale=true")
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
    # 2007 decals store the URL XML-escaped ("&amp;id="), so unescape first.
    text = html.unescape(data.decode("utf-8", "replace"))
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
    for cid, cname in {**CREATORS, **CLOTHING_CREATORS}.items():
        clothing = [k for k in types if k in CLOTHING_ASSET_TYPES.values()]
        if clothing:
            items += clothing_items(cid, cname, clothing)
        if cid in CLOTHING_CREATORS:
            continue
        for kind in types:
            if kind in CLOTHING_ASSET_TYPES.values():
                continue
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


def clothing_items(cid, cname, kinds):
    """One Category=3 sweep per creator, split by assetType.

    Three per-subcategory searches cost three times the rate-limited catalog
    pages for the same clothes.
    """
    cached = {}
    for kind in kinds:
        path = os.path.join(OUT, ".cache", f"list-{cid}-{kind}.json")
        if not os.path.isfile(path):
            break
        with open(path, encoding="utf-8") as f:
            cached[kind] = json.load(f)
    if len(cached) == len(kinds):
        items = []
        for kind in kinds:
            items += cached[kind]
            print(f"  {cname:22} {kind:7} {len(cached[kind])} (cached)")
        return items

    found = {kind: [] for kind in kinds}
    for item in search(cid, 3):
        kind = CLOTHING_ASSET_TYPES.get(item.get("assetType"))
        if kind not in found:
            continue
        created = (item.get("itemCreatedUtc") or "")[:10]
        if created and created > CUTOFF:
            continue
        found[kind].append({
            "id": item["id"],
            "type": kind,
            "name": item.get("name") or "",
            "creator": cname,
            "created": created,
        })
    items = []
    for kind in kinds:
        path = os.path.join(OUT, ".cache", f"list-{cid}-{kind}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(found[kind], f)
        items += found[kind]
        print(f"  {cname:22} {kind:7} {len(found[kind])}")
    return items


# Classic head/hair pieces that only show up through a name search. Keep as a
# fallback for items the collectibles sweep still misses.
LEGACY_NAMES = [
    "Dominus", "Ice Crown", "Sparkle Time", "Valkyrie", "Clockwork",
    "Sparkle Time Fedora", "Bighead", "Shaggy", "Wisp", "Federation",
    "Domino Crown", "Hooded", "Antlers", "Halo", "Teapot", "Bluesteel",
    "Red Valk", "Bacon Hair", "Pal Hair", "Roblox Cap", "Bucket", "Crown",
]
# 42 is a 3D face accessory (a model), not the 2D face decal type.
ACCESSORY_TYPES = {8: "hat", 41: "hair", 42: "hat", 43: "back",
                   44: "shoulder", 45: "back", 46: "back", 47: "back"}

# Category=11 only returns the ordinary listings; limiteds and other
# collectibles live behind SalesTypeFilter=2, and IncludeNotForSale keeps the
# ones no longer purchasable.
SWEEPS = {
    "collectibles": "&SalesTypeFilter=2",
}


def accessory_sweep(name, extra):
    """Walk one Roblox accessory listing.

    30 items a page at 12 requests a minute, so the result and cursor are
    cached and the walk resumes after a stop; delete the cache to start over.
    """
    cache = os.path.join(OUT, ".cache", f"accessories-{name}.json")
    found, cursor, pages = [], None, 0
    if os.path.isfile(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                state = json.load(f)
            found, cursor, pages = state["items"], state["cursor"], state["pages"]
            print(f"  {name} sweep: {len(found)} kept, page {pages} (cached)")
        except (ValueError, KeyError):
            found, cursor, pages = [], None, 0
    while cursor is not None or pages == 0:
        url = ("https://catalog.roblox.com/v1/search/items/details"
               "?Category=11&CreatorTargetId=1&CreatorType=User&Limit=30"
               "&IncludeNotForSale=true" + extra)
        if cursor:
            url += "&Cursor=" + urllib.parse.quote(cursor)
        page_data = get_json(url)
        for item in page_data.get("data", []):
            kind = ACCESSORY_TYPES.get(item.get("assetType"))
            created = (item.get("itemCreatedUtc") or "")[:10]
            if kind and (not created or created <= CUTOFF):
                found.append({"id": item["id"], "type": kind,
                              "name": item.get("name") or "",
                              "creator": "Roblox", "created": created})
        cursor = page_data.get("nextPageCursor")
        pages += 1
        cache_write(cache, {"items": found, "cursor": cursor, "pages": pages})
        if pages % 20 == 0:
            print(f"  {name} page {pages}: {len(found)} kept")
        if pages >= 1200:
            print(f"  {name} sweep hit the page cap", file=sys.stderr)
            break
    print(f"  {name} sweep: {len(found)} kept over {pages} pages")
    return found


def extra_accessories():
    """Add the accessory listings the per-creator category searches miss."""
    found = []
    for name, extra in SWEEPS.items():
        found += accessory_sweep(name, extra)

    for name in LEGACY_NAMES:
        # The plain listing and the collectibles listing rank differently:
        # Dominus Empyreus is only in the first, The Ice Crown only in the
        # second. Cache per name so a rerun costs one sweep, not 44 searches.
        cache = os.path.join(OUT, ".cache", "name-"
                             + re.sub(r"[^a-z0-9]+", "-", name.lower()) + ".json")
        if os.path.isfile(cache):
            try:
                with open(cache, encoding="utf-8") as f:
                    found += json.load(f)
                continue
            except ValueError:
                pass
        items = []
        for extra in ("", "&SalesTypeFilter=2"):
            try:
                page_data = get_json(
                    "https://catalog.roblox.com/v1/search/items/details"
                    "?Keyword=" + urllib.parse.quote(name)
                    + "&Category=11&CreatorTargetId=1&CreatorType=User"
                    "&Limit=30&IncludeNotForSale=true" + extra)
            except Exception as exc:
                print(f"  search {name} failed: {exc}", file=sys.stderr)
                continue
            for item in page_data.get("data", []):
                kind = ACCESSORY_TYPES.get(item.get("assetType"))
                created = (item.get("itemCreatedUtc") or "")[:10]
                if kind and created and created <= CUTOFF:
                    items.append({"id": item["id"], "type": kind,
                                  "name": item.get("name") or "",
                                  "creator": "Roblox", "created": created})
        cache_write(cache, items)
        found += items
    return found


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
    if "hat" in types:
        print("accessories sweep:")
        items += extra_accessories()
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

    # Hand-made items (web/catalog/local-items.json) survive a rescrape.
    local = os.path.join(OUT, "local-items.json")
    if os.path.isfile(local):
        with open(local, encoding="utf-8") as f:
            for entry in json.load(f).get("items", []):
                if entry.get("file") and os.path.isfile(os.path.join(OUT, entry["file"])):
                    manifest.append(entry)

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
