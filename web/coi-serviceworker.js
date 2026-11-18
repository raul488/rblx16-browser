/* Cross-origin isolation + local asset store for static hosting.
 *
 * The engine is built with pthreads, so the page needs SharedArrayBuffer,
 * which browsers only expose to cross-origin isolated pages. On a host that
 * cannot send COOP/COEP (GitHub Pages) this worker adds the headers and
 * reloads the page once.
 *
 * It also answers /asset/?id=N from web/assets/<id>.gz, the packed copy of
 * every mesh and texture the bundled places and catalog accessories use.
 * Only the items a player actually loads are fetched, and responses are cached
 * so later visits (and offline) are instant.
 */
const ASSET_CACHE = "rblx2016-assets";
let assetIndex = null;

async function packedAssetIds() {
  if (assetIndex) return assetIndex;
  try {
    const response = await fetch(new URL("assets/index.json", self.registration.scope), { cache: "no-cache" });
    assetIndex = new Set(response.ok ? (await response.json()).ids || [] : []);
  } catch (_) {
    assetIndex = new Set();
  }
  return assetIndex;
}

async function gunzip(response) {
  if (typeof DecompressionStream === "undefined" || !response.body) return response.arrayBuffer();
  const stream = response.body.pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).arrayBuffer();
}

function withIsolationHeaders(response) {
  if (response.status === 0) return response;
  const headers = new Headers(response.headers);
  headers.set("Cross-Origin-Embedder-Policy", "require-corp");
  headers.set("Cross-Origin-Opener-Policy", "same-origin");
  headers.set("Cross-Origin-Resource-Policy", "cross-origin");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: headers,
  });
}

async function servePackedAsset(event) {
  const url = new URL(event.request.url);
  // A project page lives under /<repo>/, so match the path suffix.
  const path = url.pathname.replace(/\/+$/, "");
  if (!path.endsWith("/asset")) return null;
  const id = url.searchParams.get("id") || url.searchParams.get("assetId");
  if (!id || !/^\d{1,20}$/.test(id)) return null;
  if (!(await packedAssetIds()).has(id)) return null;
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(event.request);
  if (cached) return cached;
  try {
    const packed = await fetch(new URL("assets/" + id + ".gz", self.registration.scope));
    if (!packed.ok) return null;
    const body = await gunzip(packed);
    const response = new Response(body, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "public, max-age=31536000, immutable",
      },
    });
    await cache.put(event.request, response.clone());
    return response;
  } catch (_) {
    return null;
  }
}

if (typeof window === "undefined") {
  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
  self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.cache === "only-if-cached" && request.mode !== "same-origin") return;
    event.respondWith(
      (async () => {
        try {
          const packed = await servePackedAsset(event);
          if (packed) return withIsolationHeaders(packed);
          return withIsolationHeaders(await fetch(request));
        } catch (_) {
          return Response.error();
        }
      })(),
    );
  });
} else if (!window.crossOriginIsolated && window.isSecureContext && navigator.serviceWorker) {
  window.addEventListener("load", () => {
    if (sessionStorage.getItem("rblx2016.coi-reloaded") === "1") return;
    navigator.serviceWorker.register("coi-serviceworker.js").then((registration) => {
      const reload = () => {
        sessionStorage.setItem("rblx2016.coi-reloaded", "1");
        location.reload();
      };
      if (registration.active) reload();
      else if (registration.installing || registration.waiting) {
        navigator.serviceWorker.ready.then(reload);
      }
    }).catch((error) => console.warn("[rbx2016] cross-origin isolation unavailable:", error));
  });
}
