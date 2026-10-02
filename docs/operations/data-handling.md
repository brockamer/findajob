# Data handling for operator-run instances

This page is for the person who runs a findajob instance. It matters most when
you run one for someone else. It says where an instance keeps data, what
leaves the host, who can reach it, and how to remove it.

One instance serves one person. Running findajob for several people means one
instance per person, each with its own `state/` directory and its own keys.
See Decision 37(c) in [`../roadmap.md`](../roadmap.md). Separating the keys is
the operator's job, and the code does not enforce it. The steps are in
[Multi-tenant hosts](install-docker.md).

## Who controls what

| Party | Controls |
|---|---|
| Operator | The host, the `state/` directory, every API key in `state/data/.env`, the sign-in pair, and the backups. |
| Data owner | Their own materials and decisions. They hold no technical control unless the operator hands over the sign-in pair. |
| findajob project | Nothing at run time. The code has no telemetry and no phone-home. |

When the operator and the data owner are different people, the operator
decides what the instance sends out. Agree this with the data owner before
you start.

## Where the data lives

Everything is under the stack's `state/` directory. Nothing personal is in the
image or the repository.

| Path under `state/` | Holds |
|---|---|
| `data/pipeline.db` | Jobs, scores, notes, notifications, the audit log, the onboarding interview |
| `data/.env` | API keys, the notification topic, the sign-in pair. Mode 0600. This is the only place API keys are stored. |
| `candidate_context/` | Profile, master resume, writing samples |
| `companies/` | Briefings, tailored resumes, cover letters and other prep output |
| `config/` | Personal settings. `gmail.json` holds the Gmail app password if Gmail is connected. |
| `logs/`, `.backups/` | Run logs and local backup files |

## What leaves the host

Only the services you configure are called. Each row is a separate recipient.

| Recipient | Used for | What it receives |
|---|---|---|
| OpenRouter, and the model vendors it routes to | Every AI task: scoring, briefings, cover letters, resume tailoring, outreach, interview prep, company research, the onboarding interview | Profile, master resume, writing samples, job descriptions, contact names and companies for outreach, and the data owner's interview answers |
| Google Gemini (direct) | Podcast audio, only when a Gemini key is set | The podcast script text, built from the job and briefing content |
| RapidAPI | Job search | Search terms and locations |
| Public job boards and company career APIs | Fetching job listings | The search term, company name or board identifier. No candidate content. |
| Gmail over IMAP | Job-alert ingestion and rejection detection, only when Gmail is connected | The app password. The scan uses read-only commands and nothing is sent back. |
| ntfy.sh | Push notifications | The notification text, sent to a topic name that works as a shared secret |
| GitHub | Update check | A request for the latest release. No instance data. |
| Browser CDNs (Tailwind, unpkg, jsDelivr) | Loading web UI scripts | Whatever a browser sends to a CDN. This happens in the viewer's browser, not on the server. |

How long OpenRouter, the model vendors and Google keep a request is set by
their own terms and by the account settings of the key. Review those settings
for each key before you use it for a client.

## Who can reach the instance

- One HTTP Basic pair protects the web UI. The operator and the data owner
  share it if both sign in. There are no per-person accounts.
- The audit log field `changed_by` records which code path made a change. It
  does not record which person did. It cannot tell the operator's action from
  the data owner's.
- Anyone who can sign in can download a backup. A backup holds the candidate
  materials and the database. It holds no credentials. See
  [Restore from backup](restore.md#credentials-are-not-in-a-backup).
- If you expose the instance to the internet, follow
  [`internet-exposure.md`](internet-exposure.md).

## Removing an instance

When the data owner leaves, do all of these:

1. Stop the stack: `docker compose down`.
2. Delete the whole `state/` directory. This removes the database, the
   materials, the keys, the logs and the local backups.
3. Delete every backup file you copied elsewhere.
4. Delete the instance's OpenRouter key and its RapidAPI and Gemini keys at
   the provider. Revoke the Gmail app password in the Google account.
5. Unsubscribe the notification topic in the ntfy app and stop using the topic.
6. Remove the image and any reverse-proxy entry for the instance.

Data already sent to a provider stays under that provider's retention rules.
Deleting the instance's key does not delete it there.
