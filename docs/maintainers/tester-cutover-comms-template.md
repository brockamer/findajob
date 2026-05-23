# Tester cutover comms template

This is a **template**, not a comm. The operator copies it, fills the
placeholders below, sanity-checks the per-tester quirks, and sends the
filled version to each tester whose findajob stack is migrating from
the operator-administered docker host to their own Fly.io account.

Per [`roadmap.md`](../roadmap.md) Decision 26, this migration applies to
the four self-onboarded tester stacks (`findajob-{dave,judy,papa,tango}`).
**alice is out of scope** — see the footnote at the bottom of this file.

The filled-in comm itself is operator-private and should land in
`candidate_context/` (gitignored), not under any tracked path. The
template stays tracked so the procedure is auditable.

Companion artifacts the template links to:
- [`install-fly.md`](../getting-started/install-fly.md) — the public install runbook the tester reads.
- [`tester-migration.md`](tester-migration.md) — the operator's migration runbook.
- [`release-process.md`](release-process.md) — covers the dual-track release window (#817).

## Placeholders

Each `{name}` below should be replaced with the per-tester value before
sending:

| Placeholder | Source | Example |
|---|---|---|
| `{tester_handle}` | operator's tester table (handle column) | `papa` |
| `{tester_greeting}` | operator's choice — first name or handle | (operator-private) |
| `{cutover_date}` | operator-scheduled cutover window | `Saturday, June 7, 2026` |
| `{cutover_window_local}` | tester's local time | `10:00 AM – 12:00 PM PT` |
| `{operator_email}` | operator's contact for cutover-day coordination | (operator-private) |
| `{operator_first_name}` | sign-off name | (operator-private) |
| `{old_url}` | tester's current docker subdomain | `findajob-papa.<operator-domain>` |
| `{new_url}` | tester's new Fly URL post-cutover | `findajob-papa.fly.dev` |

The tester is asked to choose two values themselves (see "What you do before cutover day" below): their **Fly org slug** and their **Fly app handle**. These don't get pre-filled — the comm asks for them.

---

# Template body

> *Everything between the horizontal rules below is the comm to copy + fill + send. Don't include the placeholder table or this preamble.*

---

Hi {tester_greeting},

A quick heads-up about findajob: I'm moving your stack from my server to your own Fly.io account. This is a one-time change. Your daily findajob experience won't change — same dashboard, same job board, same history. What's changing is **who runs the underlying infrastructure**.

I'd like to do the cutover on **{cutover_date}, {cutover_window_local}**. The whole thing takes about an hour of your time spread across cutover day, plus 10–15 minutes when findajob is actually offline. Read on for what changes, what you need to do, and what I'll handle.

## Why now

When you came on as a beta tester, I hosted your findajob on a Linux server I run at my house. That made sense for the early going — fewer moving parts for you, faster iteration for me. Now that findajob has a smooth self-service install path on [Fly.io](https://fly.io/), it's time to graduate everyone to their own account.

Two reasons this matters for you:

- **You own your stuff.** Your job data, your LLM spend, your decisions about scaling and uptime. No middleman.
- **You can keep using findajob after I'm done with my job search.** When I get hired and stop running this, your instance on your own Fly account keeps working independently.

The findajob software itself doesn't change. The full daily-utility flow — onboarding, board tabs, materials prep, settings — works the same way it does today. This is purely a move to a different hosting substrate.

## What stays the same

- Your dashboard URL changes (see below) but **your login credential does not** — same basic-auth username and password.
- All of your data carries over: job board, applied/rejected/waitlisted history, materials, profile, role prompts, notes. I'm using a migration tool I built and tested specifically for this — it's been round-tripped end-to-end (see [`tester-migration.md`](https://github.com/brockamer/findajob/blob/main/docs/maintainers/tester-migration.md) if you're curious about the mechanics).
- Findajob's feature surface, including ntfy push notifications, Gmail rejection-email detection (if you have it set up), and the daily triage schedule.
- I'm still around to answer questions and fix bugs — your account just runs on your Fly, not my server.

## What changes

- Your dashboard URL: from `https://{old_url}/` to `https://{new_url}/`.
- Your monthly costs become directly yours, billed by Fly and OpenRouter. Today I'm covering both. The Fly hosting tier is small — roughly **$3–5/month for the always-on machine + 8GB volume**. LLM spend depends on your usage; expect **$5–20/month for steady-state daily triage**, more if you actively prep multiple applications a week. You can cap LLM spend at any dollar amount via `/settings/spend-ceiling/` in the app — the pipeline halts new LLM calls when the running monthly total crosses your cap. Set one before cutover day if you want a hard ceiling. The full cost breakdown is at [`cost.md`](https://github.com/brockamer/findajob/blob/main/docs/operations/cost.md).
- You'll need three accounts in your own name — Fly, OpenRouter, and (optional) RapidAPI. See the next section.

## What you do before cutover day

These steps take about 30–45 minutes. Do them at your own pace any time before {cutover_date}.

1. **Sign up for Fly.io.** Go to <https://fly.io/app/sign-up>, create an account, then add a credit card under **Billing** in the left nav. (Without a card, Fly rejects deployments with a confusing 422 error — fix it before cutover day.) Cost: $0 to sign up; ~$4/month once your app is running.

2. **Sign up for OpenRouter.** Go to <https://openrouter.ai/> and create an account. Then go to <https://openrouter.ai/credits> and add at least **$10 of credit** (pay-as-you-go; this is a balance the system draws from, not a subscription).

3. **(Optional) Sign up for RapidAPI** for LinkedIn / Indeed / Bing job-search ingestion. Go to <https://rapidapi.com/auth/sign-up> and subscribe to the **Jobs API by API14 — BASIC plan** (free, 150 requests/month, no credit card required). Skipping this means LinkedIn / Indeed search is inactive — Greenhouse, Ashby, Lever, and Gmail alerts still work without it.

4. **Pick a Fly app handle.** This becomes the leftmost label of your new URL — e.g., if your handle is `jane`, your URL is `findajob-jane.fly.dev` (same convention as the public install-fly.md). Lowercase letters, digits, hyphens; no underscores. It must be globally unique across Fly, so if your first choice is taken, try `findajob-<handle>-<year>` or your initials. Send me the chosen handle.

5. **Find your Fly org slug.** After signing up, your Fly dashboard URL contains your org slug — `https://fly.io/dashboard/<your-org-slug>/`. Send me that slug too.

6. **Skim [`install-fly.md`](https://github.com/brockamer/findajob/blob/main/docs/getting-started/install-fly.md).** This is the install runbook you'll follow on cutover day. You won't need to run the onboarding interview — your existing config migrates over — but the deploy steps (`fly auth login`, the deploy script, the secrets prompts) are what you'll do.

Reply to this email with: **Fly app handle, Fly org slug, RapidAPI yes/no, target spend ceiling (optional)**.

## What I do on cutover day

Once you've sent me the handle / org slug, I'll coordinate timing and:

1. **Stop your docker stack** at the agreed window start. Your dashboard becomes unreachable at this point.
2. **Export your state** — database, companies folder, profile, role prompts — into a single tarball using the migration tool.
3. **Coordinate a brief Fly access window with you** so I can run the import against your new app. You'll add me to your Fly org as an admin temporarily, I do the import, then you remove me. (Alternative: I screen-share and walk you through running the import yourself, which keeps me out of your account entirely. Tell me which you prefer when you reply.)
4. **Verify the import** — row counts and database checksums must match the source exactly. If anything's off, I roll back to your docker stack (it's untouched and can restart immediately).
5. **Hand off** the new URL and confirm everything looks right.

Total findajob downtime: **10–15 minutes** if everything goes cleanly. If we hit a snag, I roll back and we reschedule — you'll be back on your old URL within the same window.

## What you do on cutover day

Before the window:

1. **Run the install-fly.md deploy** on your laptop. This creates the empty Fly app I'll import your data into. (You can do this earlier, even days before — the empty app costs ~$0.10/day to sit idle.) Reply to me when the deploy is done and your new URL responds with a basic-auth prompt.

During the window (~20 minutes of your time):

2. **Be reachable** by email or text in case I need a quick decision.
3. **Add me to your Fly org as admin** (or screen-share — whichever you picked) when I message that I'm ready. Fly's docs at <https://fly.io/docs/about/billing-org-management/> have the exact steps for adding a member.
4. **Log into your new URL** when I message that the import is done. Same basic-auth credential you've been using. Confirm your job board looks right (recent jobs, your history intact, your profile loaded).
5. **Remove me from your Fly org** once we're both confident the migration is clean.

After the window: you're on your own infrastructure. Going forward, you handle:
- Fly billing (paid directly to Fly).
- OpenRouter top-ups (no subscription, just add credit when your balance gets low).
- Future findajob updates — same `fly deploy` command from the install runbook; release notes are at <https://github.com/brockamer/findajob/blob/main/CHANGELOG.md>.

I'll send you a separate short note covering the **first week of self-hosted findajob** — things like how to update to a new release, where to find cost dashboards, and what to do if a release breaks something. That note isn't part of cutover day; it's just a "you've got the keys now, here's the glove box" follow-up.

## If you have second thoughts

You don't have to migrate. If self-hosting feels like more than you want to take on, tell me. Your stack stays on my server until I'm done with my own job search — at which point I'll stop running findajob entirely and your data is yours to take or archive however you like.

The migration is the recommended path because it puts you in control of an asset that's been useful, but it's a recommendation, not a requirement.

## Questions

Reply to this email, or text/call me at {operator_email}. I'd rather over-communicate before cutover day than have us hit a snag mid-window.

— {operator_first_name}

---

# Footnote: alice exception

Per Decision 26, alice's stack moves to operator's Fly account — not to her own. alice is the only tester who stays under operator administration post-sunset. The reason is operational, not technical: alice is the longest-running beta tester and is co-piloting some of the generalization work; keeping her stack on the operator's Fly account means the operator can iterate on her instance without coordinating a third-party Fly login every time.

alice doesn't receive this comm. Instead, the operator handles her migration as an internal-to-operator move (operator's docker stack → operator's Fly account, same migration tool, no tester-side action required).

If a future tester needs the same arrangement — e.g., a tester whose technical comfort level makes self-hosting a burden — copy this footnote pattern: the migration still happens, but the destination is operator's Fly account rather than the tester's. Tell that tester separately that they're staying under operator administration; don't send them the self-host comms template.

## Cross-references

- **Roadmap:** [`docs/roadmap.md`](../roadmap.md) Decision 26 (post-launch tester sunset arc).
- **Issue:** [`#818`](https://github.com/brockamer/findajob/issues/818) (this template).
- **Migration tool:** [`#816`](https://github.com/brockamer/findajob/issues/816), [`tester-migration.md`](tester-migration.md).
- **Dual-track release window:** [`#817`](https://github.com/brockamer/findajob/issues/817), [`release-process.md`](release-process.md).
- **Stack archival (post-cutover):** [`#819`](https://github.com/brockamer/findajob/issues/819).
- **Umbrella tracking:** [`#749`](https://github.com/brockamer/findajob/issues/749).
