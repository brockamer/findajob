# Exposing findajob to the public internet

By default findajob has no authentication. That is fine when access is restricted by the network perimeter (the perimeter VPN, loopback, lab network). To expose an instance to the public internet — for example at `https://findajob-{handle}.example.com` — turn on HTTP Basic Auth via two env vars.

## Threat model

This is **shared-secret authentication**, not an identity system. It defends against:

- Drive-by scanning of the open internet
- Indexing by search engines and archive crawlers
- Casual probing by anyone who happens to learn the URL
- A page on another site driving the UI with the credential your browser replays (see [Cross-site request protection](#cross-site-request-protection))

It does **not** defend against:

- A determined attacker who learns the credential
- A compromised endpoint (the password lives in `compose.yaml` plaintext)
- Anything your reverse proxy + TLS layer don't already handle

For real per-user identity (RBAC, 2FA, OIDC), a dedicated auth layer is needed. That is intentionally out of scope here — it is a separate, future change.

## Topology

```
https://findajob-{handle}.example.com
        ↓
   Geo-IP filter (e.g. Firewalla, restrict to expected regions)
        ↓
   Reverse proxy (TLS termination — e.g. the reverse-proxy admin UI)
        ↓
   <host>:<port>
        ↓
   FastAPI BasicAuthMiddleware  ← this layer
        ↓
   FastAPI CrossSiteRequestMiddleware  ← always on, see below
        ↓
   findajob route handlers
```

The middleware sits inside the findajob FastAPI app. There is no separate auth LXC — auth ships with the app, gated on env vars.

## Setup

1. **Generate a strong password** (≥24 chars):

       openssl rand -base64 32

2. **Set the env vars in `compose.yaml`**:

       services:
         scheduler:
           environment:
             FINDAJOB_AUTH_USER: youruser
             FINDAJOB_AUTH_PASS: <long-random-string>

3. **Apply**:

       docker compose up -d

4. **Verify the gate is on**:

       curl -I https://findajob.example.com/

   Expected: `401 Unauthorized` with `WWW-Authenticate: Basic realm="findajob"`.

5. **Verify the credential works**:

       curl -I -u youruser:<password> https://findajob.example.com/

   Expected: `200 OK`.

6. **Wire the reverse proxy**: in your reverse-proxy UI point your domain at `<host>:<port>`.

## Allowlist

Three paths bypass the auth gate even when the env vars are set:

- `/healthz` — health checks; no PII; needed by the reverse proxy and any monitoring.
- `/static/*` — CSS/JS the browser must fetch *before* it can render the auth-prompt page. Contains no PII.
- `/favicon.ico` — same rationale as `/static/`.

Every other path requires the credential.

## Rotation

To rotate the credential:

1. Edit `FINDAJOB_AUTH_PASS` in `compose.yaml`.
2. `docker compose up -d` — the container restarts with the new credential.
3. Share the new credential out-of-band with anyone who needs access.

## Disabling

Remove (or empty) `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` and `docker compose up -d`. The middleware becomes a no-op and all requests pass through.

## Cross-site request protection

Browsers replay HTTP Basic Auth on cross-site form posts and `fetch` calls. Without a request-origin check, a page on any other site could drive every state-changing route in the UI as you — trigger an update, upload a restore tarball, change stages, edit config. `findajob.web.middleware.CrossSiteRequestMiddleware` is that check. It is **always on**, whether or not the auth env vars are set: a LAN-only instance is just as reachable from a hostile page open in your browser.

Every `POST`, `PUT`, `PATCH` and `DELETE` is checked before routing:

1. If the browser sends `Sec-Fetch-Site` (every current browser does), its verdict is final: `cross-site` and `same-site` get `403`; `same-origin` and `none` (a typed URL or bookmark) pass.
2. Otherwise, if the browser sends `Origin`, its hostname must match the request's `Host` hostname, or the first hostname in `X-Forwarded-Host` when your proxy forwards one. `Origin: null` is rejected.
3. Requests with neither header pass. Scripts, `curl` and the test client send neither, and they are not a browser replaying a credential.

`GET` is never affected, so links and bookmarks work as before. A rejected request gets a plain-text `403 Cross-site request rejected.` and one `WARNING` line in the container log naming the method, path, `Origin` and `Host`.

**If you see that 403 on your own instance**, your reverse proxy is almost certainly rewriting `Host` to its upstream name and your browser is old enough to omit `Sec-Fetch-Site` (Safari before 16.4). Fix it at the proxy: pass the original host through (`proxy_set_header Host $host;` in nginx) or forward it in `X-Forwarded-Host`. Caddy and Traefik pass `Host` through by default. Current browsers are unaffected either way, because rule 1 never consults `Host`.

There is no switch to disable this check.

**Second line: the `HX-Request` header.** Most state-changing routes in the UI are only ever called by HTMX, which sends `HX-Request: true` on every request it makes. Those routes also require that header (`findajob.web.htmx_guard.require_htmx`). A cross-site HTML form cannot set a custom header at all, and cross-site JavaScript can only send one after a CORS preflight the app never approves — so these routes stay closed even to a browser too old to send `Sec-Fetch-Site` or `Origin`. Routes reached by a plain `<form method="post">` or a `fetch` call do not carry the gate; `tests/test_web_htmx_guard.py` holds the inventory and fails when a route is added without being classified. A request to a gated route without the header gets `403` with a short JSON `detail`.

## What this does not change

- **the perimeter VPN access still works** for deployments that don't set the env vars. The middleware is opt-in.
- **`/config/` is still un-rate-limited and trusts whoever the gate let in.** This is per-instance auth, not per-user authorization. Anyone holding the credential can edit pipeline config.
