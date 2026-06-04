# Updating findajob

Updates bring new features, bug fixes, and improved LLM prompts. The process differs by how you deployed.

---

## When to update

Watch [CHANGELOG.md](https://github.com/brockamer/findajob/blob/main/CHANGELOG.md) for release announcements. Updates are announced with a version number (e.g. `v0.31.0`) and a summary of what changed. You do not need to update on every release — stable deployments can skip versions and update to the latest in one step.

---

## Fly.io users

If you installed via the web "Launch an App" flow (no terminal), update from the Fly dashboard: open your app's overview page and click **Deploy** to redeploy the latest release. (Fly's **Auto-Deploy on push** does *not* track findajob releases — it only fires when the GitHub repo you connected is pushed, and a fork's `main` doesn't move when this project releases upstream.) See [`getting-started/install-fly.md` → Updating to a new release](getting-started/install-fly.md#updating-to-a-new-release) for the walkthrough.

**Power-user (local repo clone).** If you have `flyctl` and a clone of the repo, run this after any release you want to pick up:

    fly deploy --config ops/fly.toml

This pulls the latest image from the container registry, deploys it to your Fly machine, and restarts the container. The update takes 30–60 seconds.

After deploying, verify the auth gate is still up:

    fly ssh console --app findajob-<handle> --command "python -m findajob.web.verify_auth"

A zero exit means the gate is active. Any non-zero exit means the stack is unverified — check `fly logs` before using the app again.

---

## Docker users — Watchtower enabled

If your compose stack has Watchtower configured to watch the `findajob` service, updates happen automatically within an hour of each new release. No action needed — Watchtower pulls the new image and recreates the container.

To confirm Watchtower is watching your service, check that your compose file does **not** have this label on the scheduler service:

    com.centurylinklabs.watchtower.enable: "false"

If that label is absent (or set to `"true"`), Watchtower is active.

### Optional: an "Update now" button in the dashboard

If you'd rather trigger an update on demand (instead of waiting up to an hour
for Watchtower's poll), enable Watchtower's HTTP API and tell findajob about it:

1. Start Watchtower with `--http-api-update`, a token via
   `WATCHTOWER_HTTP_API_TOKEN`, and `--http-api-periodic-polls` (so scheduled
   polling still runs).
2. Set two env vars on the findajob container:
   - `FINDAJOB_WATCHTOWER_HTTP_URL` — e.g. `http://watchtower:8080`
   - `FINDAJOB_WATCHTOWER_HTTP_TOKEN` — the same token
3. When both are set and an update is available, the dashboard banner shows an
   **Update now** button. It asks Watchtower (which runs outside the container)
   to pull and recreate the findajob image only. Watchtower auto-update stays
   the zero-effort default — the button is just an on-demand shortcut.

---

## Docker users — manual update

Pull the new image and restart the container:

    docker compose pull && docker compose up -d

The pull downloads the updated image from the registry. `up -d` recreates the running container using it. The container is briefly unavailable during the restart — typically under 10 seconds.

---

## What updates do not touch

Updates replace the application code and LLM prompts inside the container. They do not touch anything in your `state/` directory.

Everything in `state/` is preserved across every update:

- `state/data/pipeline.db` — your job history, scores, notes, and stage transitions
- `state/config/` — your API keys, filter rules, target companies, excluded employers
- `state/candidate_context/` — your profile, master resume, voice samples
- `state/companies/` — briefings, tailored resumes, cover letters, prep materials
- `state/.backups/` — backup tarballs

If a release requires a schema migration (a structural change to the database), it runs automatically on first startup after the update. The CHANGELOG entry for that release will include a `### Migration required` section describing what changes.

---

## Checking your version

Open [CHANGELOG.md](https://github.com/brockamer/findajob/blob/main/CHANGELOG.md) — the first versioned heading (e.g. `## [0.31.3] - 2026-05-28`) is the latest release. Compare it to what your container is running.

To see the version your running container was built from:

    docker compose exec scheduler python3 -c "
    from pathlib import Path
    from findajob.paths import BASE
    cl = Path(BASE) / 'CHANGELOG.md'
    for line in cl.read_text().splitlines():
        if line.startswith('## [') and ']' in line:
            v = line.split('[',1)[1].split(']',1)[0]
            if v[:1].isdigit():
                print(v)
                break
    "

For Fly deployments, prefix with `fly ssh console --app findajob-<handle> --command` instead of `docker compose exec scheduler`.

This reads the CHANGELOG baked into the running image and prints the first versioned release heading — the same mechanism the pipeline uses internally.
