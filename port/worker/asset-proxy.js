/* Asset proxy for RBLX2016.
 *
 * assetdelivery.roblox.com sends no CORS headers and needs a Roblox session,
 * so a static site cannot fetch rbxassetid content itself. This Worker holds
 * the session as a secret, fetches the asset server-side, and caches the bytes
 * at the edge.
 *
 *   GET /asset/?id=N  ->  asset bytes (application/octet-stream)
 *
 * Deploy:  npx wrangler deploy   (or upload through the Cloudflare API)
 * Secret:  npx wrangler secret put ROBLOX_COOKIE
 *
 * Anyone who knows the URL can use it to pull assets with this session, so
 * keep the URL out of public places you do not control.
 */

const UPSTREAM = "https://assetdelivery.roblox.com/v1/asset?id=";

// Only these pages may use the proxy. Requests without an Origin (curl,
// scripts) are still allowed: the URL itself is the secret, and a browser
// cannot forge an Origin header. Override with the ALLOWED_ORIGINS variable.
const DEFAULT_ORIGINS = [
  "https://raul488.github.io",
  "http://127.0.0.1:8080",
  "http://localhost:8080",
  "http://127.0.0.1:8081",
  "http://localhost:8081",
];

function allowedOrigins(env) {
  const configured = (env && env.ALLOWED_ORIGINS) || "";
  const extra = configured.split(",").map((o) => o.trim()).filter(Boolean);
  return new Set([...DEFAULT_ORIGINS, ...extra]);
}

function originAllowed(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return true;
  return allowedOrigins(env).has(origin);
}

function notFound(message, status) {
  return new Response(message, {
    status,
    headers: { "Access-Control-Allow-Origin": "*", "Content-Type": "text/plain" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (!originAllowed(request, env)) return notFound("origin not allowed", 403);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, OPTIONS",
          "Access-Control-Allow-Headers": "*",
        },
      });
    }
    if (request.method !== "GET") return notFound("method not allowed", 405);
    if (!/^\/asset\/?$/.test(url.pathname)) return notFound("not found", 404);

    const id = url.searchParams.get("id") || url.searchParams.get("assetId") || "";
    if (!/^\d{1,20}$/.test(id)) return notFound("bad asset id", 400);
    if (!env.ROBLOSECURITY_COOKIE) return notFound("proxy has no session cookie", 503);

    const cache = caches.default;
    const key = new Request("https://asset-proxy.internal/" + id);
    const cached = await cache.match(key);
    if (cached) return cached;

    let upstream;
    try {
      upstream = await fetch(UPSTREAM + id, {
        headers: {
          Cookie: ".ROBLOSECURITY=" + env.ROBLOSECURITY_COOKIE,
          "User-Agent": "Roblox/WinInet",
          Accept: "*/*",
        },
        redirect: "follow",
      });
    } catch (e) {
      return notFound("upstream fetch failed: " + e.message, 502);
    }
    if (!upstream.ok || !upstream.body) {
      return notFound("asset " + id + " failed: " + upstream.status, upstream.status || 502);
    }

    const response = new Response(upstream.body, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Access-Control-Allow-Origin": "*",
        "X-Asset-Id": id,
      },
    });
    ctx.waitUntil(cache.put(key, response.clone()));
    return response;
  },
};
