# Gmail job-alert ingestion

findajob can ingest LinkedIn (and, via a configurable allowlist, other)
job-alert emails from your Gmail.

There are two entry points for the one-time setup:

- **During onboarding** — fresh stacks land at the Gmail-config gate as the
  final step of the in-app interview (after the chat-emit phase and any
  feed-source key collection). You can save+verify your IMAP credentials
  there or click **Skip for now** to defer; either way the onboarding
  sentinel writes and you continue to the dashboard.
- **At any later time** — `/config/gmail/` on your stack is the same page
  outside the onboarding chrome. You can also re-enter the onboarding gate
  via `/onboarding/?mode=rerun` if you'd rather walk the structured flow.

The instructions below apply to both paths — the form fields and the IMAP
test mechanic are identical.

## What findajob will and won't access

> ⓘ **What findajob does — and doesn't do — with your Gmail.**

findajob reads job-alert emails from senders you list (LinkedIn by
default) so it can score them and surface them on your board. It
**does not** read other mail, send mail, modify labels, or move
messages. Your app password lives only in your findajob — never on a
server we control.

findajob is open source. You can audit the exact code that touches
your mailbox before granting access:

- [`src/findajob/gmail_imap.py`](https://github.com/brockamer/findajob/blob/main/src/findajob/gmail_imap.py) — IMAP client
- [`src/findajob/fetchers/adapters/gmail.py:GmailLinkedInAdapter`](https://github.com/brockamer/findajob/blob/main/src/findajob/fetchers/adapters/gmail.py)

<details>
<summary>Show full disclosure</summary>

### 1. Exact scope (what we touch)

- IMAP `LOGIN` to `imap.gmail.com:993`
- IMAP `SEARCH (FROM "<sender>")` for each allowlisted sender, with `UID > <last seen>`
- IMAP `FETCH (BODY.PEEK[])` of message bodies
- IMAP `LOGOUT`

That is the entire wire protocol. No `STORE`, no `COPY`, no `EXPUNGE`, no `APPEND`, no folder traversal beyond INBOX.

### 2. What we can't do

- findajob has no SMTP code path. It cannot send mail.
- findajob never calls `STORE`/`MOVE`/`EXPUNGE`. It cannot modify, label, or delete your messages.
- findajob never reads outside your allowlisted senders. The `SEARCH FROM` filter is applied server-side by Gmail before anything is fetched.

### 3. Where your credentials live

- `config/gmail.json` in your findajob only (chmod 600). Bind-mounted to the host filesystem at `state/config/gmail.json`. Never transmitted off this machine. Never logged. Never sent to any LLM, scoring service, or external API.
- Both `config/gmail.json` and `config/gmail_state.json` are gitignored — they cannot be accidentally committed.

### 4. How to revoke access

- **At Google (instant, total revocation):** [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — revoke the app password labeled `findajob-<your-handle>`.
- **In findajob (instant, your copy only):** click *Disconnect Gmail integration* on `/config/gmail/`. Wipes both config files. Google-side app password remains valid until separately revoked there.
- **Recommendation:** revoke at Google for any "I want this to stop now" scenario.

</details>

The single source of truth for this text is `src/findajob/web/templates/_gmail_disclosure.html` (the Jinja partial used by `/config/gmail/` and the onboarding gate). The CI test in `tests/test_gmail_disclosure_sync.py` asserts the text above stays in sync with that partial — edits must touch both files.

## Step-by-step setup

### 1. Turn on 2-Step Verification

App passwords cannot be created without 2-Step Verification on your
Google Account. If you don't already have it on, follow Google's guide:
[Turn on 2-Step Verification](https://support.google.com/accounts/answer/185839).

### 2. Generate an app password

Go to <https://myaccount.google.com/apppasswords>. Sign in if prompted.

In the **App name** field, enter `findajob-<your-handle>` (e.g.
`findajob-myname`). Click **Create**.

Google displays a 16-character password with spaces — for example,
`abcd efgh ijkl mnop`. **Copy it now.** Google will not show it again
once you close the dialog.

### 3. Configure findajob

(After your stack is deployed:) open `/config/gmail/` on your findajob
stack. Paste your Gmail address and the 16-character app password.
Click **Save**, then **Test connection**. Within ~3 seconds the status
pill should change to **● Authorized**.

### 4. (Optional) Add other senders

The default sender allowlist is `jobalerts-noreply@linkedin.com`. To
pull alerts from additional sources, add their email addresses (one
per line) and click Save again. To find a sender's exact address:
open one of their alert emails in your Gmail inbox and click
**Show details** to see the From: header.

## Account types that won't work

App passwords are not available for:

- Accounts with 2-Step Verification configured **only** with security
  keys (no fallback method).
- Google Workspace accounts where the admin has disabled app
  passwords for users.
- Accounts enrolled in **Advanced Protection**.

If yours is one of these, Gmail integration in findajob is not
available and the pipeline runs without it (Greenhouse / Ashby /
Lever direct fetches and RapidAPI LinkedIn search still cover most
ingestion volume).

## Troubleshooting

| Status pill | Likely cause | Fix |
|---|---|---|
| `● Login failed` | App password revoked, mistyped, or 2FA was disabled | Generate a new app password and re-save. |
| `● Connection error` | Transient network or IMAP issue | Should clear on the next triage run. Persistent errors may indicate port 993 blocked at the deploy host. |
| Status is `● Authorized` but no new jobs appear | Sender allowlist mismatch | Click into a real LinkedIn alert in your inbox; verify the From: header matches what's in the allowlist. |

## How to revoke access

See **§4 How to revoke access** in the disclosure above. Two surfaces:

1. **At Google** — instant, total revocation:
   <https://myaccount.google.com/apppasswords>.
2. **In findajob** — Disconnect button on `/config/gmail/`. Wipes both
   config files in your findajob only; Google-side app password remains
   valid until separately revoked.

## Authoritative sources

This guide was validated against:

- [Sign in with app passwords — Google Account Help](https://support.google.com/accounts/answer/185833?hl=en) (accessed 2026-04-30)
- [Add Gmail to another email client — Gmail Help](https://support.google.com/mail/answer/7126229?hl=en) (accessed 2026-04-30)
- [Choose your IMAP email client settings for Gmail](https://support.google.com/mail/answer/78892?hl=en) (accessed 2026-04-30)
