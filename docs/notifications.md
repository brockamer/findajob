# Notifications

Push notifications via [ntfy.sh](https://ntfy.sh) — free, open source, cross-platform.

---

## Setup

1. Go to https://ntfy.sh and pick a topic name (e.g. `yourname-jobsearch`)
   - Topics are public by default — choose something non-guessable
   - Or self-host ntfy for privacy
2. Install the ntfy app on your phone (iOS and Android available)
3. Subscribe to your topic in the app
4. Add to `data/.env`:
   ```bash
   NTFY_TOPIC=your-topic-name
   ```
5. Test: `python3 scripts/notify.py daily-stats`

---

## Notification Types

All notifications sent by `scripts/notify.py`. Pass the subcommand as the argument.

### `daily-stats` — Morning Summary
**Default schedule:** 7:05 AM daily (5 min after triage starts)

Content:
- Number of jobs currently in the actionable queue (score ≥ 7, not rejected)
- Jobs added in the last 24h
- Total in-progress applications (prepped, applied, interviewing)
- Timestamp of last successful triage run

---

### `health-check` — Pipeline Health
**Default schedule:** 9:10 AM daily (2h+ after 7:00 AM triage start, to give triage time to complete)

Content:
- Whether triage completed in the last 25h (looks for `pipeline_complete` event in logs)
- Any error events from the last 25h in `pipeline.jsonl`
- Count of `manual_review` jobs (potential scoring failures)
- Last known completion timestamp

**Note:** The health check fires 2h+ after triage deliberately — triage can take 30–60 min. If health-check fires before triage finishes, it will incorrectly report the previous day's triage as the last completion.

---

### `issues-ping` — Open Issues Reminder
**Default schedule:** Mon/Wed/Fri 8:00 AM

Content:
- Open issues from GitHub (`gh issue list --state open`)
- Count of open issues

Only fires if there are open issues. Silent if the list is clean.

---

### `apply-reminder` — Daily Nudge
**Default schedule:** 5:00 AM daily

Content:
- Rotating motivational quip (changes daily by day-of-year index)
- Reminder to submit at least one application today

The quips are mildly sarcastic tech-industry humor. Edit `notify.py` to customize them.

---

### `feedback-review` — Rejection Pattern Alert
**Default schedule:** Sunday 8:00 AM

Content:
- Fires only when `feedback_log` table has ≥ 10 entries
- Prompts you to review rejection patterns and adjust scoring or profile

To review manually:
```bash
sqlite3 -csv data/pipeline.db \
  "SELECT reject_reason, count(*) as n FROM feedback_log GROUP BY reject_reason ORDER BY n DESC;"
```

---

### `send-raw` — Arbitrary Notification
**No schedule — manual only.**

Send a custom notification with any title and body:
```bash
python3 scripts/notify.py send-raw "My Title" "My message body"
```

Useful for testing ntfy connectivity or sending one-off alerts from other scripts.

---

### `ci-check` — CI Failure Alert
**Default schedule:** triggered by systemd timer after each push (or run manually)

Checks the latest GitHub Actions CI run. If it failed, sends a high-priority notification with the run title and URL. Silent if CI is passing.

---

## Schedule Summary

| Notification | Schedule |
|---|---|
| `daily-stats` | 7:05 AM daily |
| `health-check` | 9:10 AM daily |
| `apply-reminder` | 5:00 AM daily |
| `issues-ping` | Mon/Wed/Fri 8:00 AM |
| `feedback-review` | Sunday 8:00 AM |
| `send-raw` | Manual only |
| `ci-check` | Manual / on-push |

Schedules are defined in systemd unit files at `~/.config/systemd/user/findajob-notify-*.timer`.

---

## Sending Manual Notifications

```bash
python3 scripts/notify.py daily-stats
python3 scripts/notify.py health-check
python3 scripts/notify.py apply-reminder
python3 scripts/notify.py issues-ping
python3 scripts/notify.py feedback-review
python3 scripts/notify.py send-raw "Title" "Body"
python3 scripts/notify.py ci-check
```

---

## Customizing

All notification content is in `scripts/notify.py`. The file is straightforward Python — edit the strings directly.

To add a new notification type:
1. Add a new function in `notify.py` (follow the pattern of existing ones)
2. Add a new `elif` branch in `main()` for the new subcommand name
3. Add a new systemd timer + service unit

ntfy supports additional features (priorities, tags, actions) via curl headers:
```bash
curl -H "Priority: high" -H "Tags: warning" -d "message" https://ntfy.sh/$TOPIC
```
See `notify.py`'s `send()` function to add header support.
