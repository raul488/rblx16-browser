/* Cross-origin isolation for static hosts (GitHub Pages) that cannot send
 * COOP/COEP themselves. The engine is built with pthreads, so the page needs
 * SharedArrayBuffer, which browsers only expose to cross-origin isolated
 * pages. Registering this worker makes the page reload once with the headers
 * added. A host that already sends them (web/serve.py) skips all of this.
 */
if (typeof window === "undefined") {
  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
  self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.cache === "only-if-cached" && request.mode !== "same-origin") return;
    event.respondWith(
      fetch(request)
        .then((response) => {
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
        })
        .catch(() => Response.error()),
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
