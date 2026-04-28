# Exposing findajob to the public internet

By default findajob has no authentication. That is fine when access is restricted by the network perimeter (Wireguard, loopback, lab network). To expose a per-tester instance to the public internet — for example at `https://findajob-{tester}.example.com` — turn on HTTP Basic Auth via two env vars.

## Threat model

This is **shared-secret authentication**, not an identity system. It defends against:

- Drive-by scanning of the open internet
- Indexing by search engines and archive crawlers
- Casual probing by anyone who happens to learn the URL

It does **not** defend against:

- A determined attacker who learns the credential
- A compromised tester endpoint (the password lives in `compose.yaml` plaintext)
- Anything your reverse proxy + TLS layer don't already handle

For real per-user identity (RBAC, 2FA, OIDC), a dedicated auth layer is needed. That is intentionally out of scope here — it is a separate, future change.

## Topology

```
https://findajob-{tester}.example.com
        ↓
   Geo-IP filter (e.g. Firewalla, restrict to expected regions)
        ↓
   Reverse proxy (TLS termination — e.g. Synology DSM)
        ↓
   docker.lan:<per-tester-port>
        ↓
   FastAPI BasicAuthMiddleware  ← this layer
        ↓
   findajob route handlers
```

The middleware sits inside the findajob FastAPI app. There is no separate auth LXC — auth ships with the app, gated on env vars.

## Setup

1. **Generate a strong password per tester** (≥24 chars):

       openssl rand -base64 32

2. **Set the env vars in that tester's `compose.yaml`**:

       services:
         scheduler:
           environment:
             FINDAJOB_AUTH_USER: alice
             FINDAJOB_AUTH_PASS: <long-random-string>

3. **Apply**:

       docker compose up -d

4. **Verify the gate is on**:

       curl -I https://findajob-alice.example.com/

   Expected: `401 Unauthorized` with `WWW-Authenticate: Basic realm="findajob"`.

5. **Verify the credential works**:

       curl -I -u alice:<password> https://findajob-alice.example.com/

   Expected: `200 OK`.

6. **Wire the reverse proxy**: in your reverse-proxy UI (Synology DSM, etc.) point `findajob-alice.example.com` at `docker.lan:<port>`.

## Allowlist

Three paths bypass the auth gate even when the env vars are set:

- `/healthz` — health checks; no PII; needed by the reverse proxy and any monitoring.
- `/static/*` — CSS/JS the browser must fetch *before* it can render the auth-prompt page. Contains no PII.
- `/favicon.ico` — same rationale as `/static/`.

Every other path requires the credential.

## Rotation

To rotate a tester's credential:

1. Edit `FINDAJOB_AUTH_PASS` in their `compose.yaml`.
2. `docker compose up -d` on that stack — the container restarts with the new credential.
3. Notify the tester out-of-band.

## Disabling

Remove (or empty) `FINDAJOB_AUTH_USER` / `FINDAJOB_AUTH_PASS` and `docker compose up -d`. The middleware becomes a no-op and all requests pass through.

## What this does not change

- **Wireguard access still works** for stacks that don't set the env vars (the operator's own deployment, for instance). The middleware is opt-in per stack.
- **`/config/` is still un-rate-limited and trusts whoever the gate let in.** This is per-instance auth, not per-user authorization. Anyone holding the credential can edit pipeline config.
