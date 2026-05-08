# Notifications

Push notifications via [ntfy.sh](https://ntfy.sh) — free, open source, cross-platform.

## Setup

1. Go to <https://ntfy.sh> and pick a topic name (e.g. `yourname-jobsearch`).
   - Topics are public by default — choose something non-guessable.
   - Or self-host ntfy for privacy.
2. Install the ntfy app on your phone (iOS and Android available).
3. Subscribe to your topic in the app.
4. Add to `data/.env`:
   ```bash
   NTFY_TOPIC=your-topic-name
   ```
5. Test:
   ```bash
   docker compose exec scheduler python3 scripts/notify.py daily-stats
   ```

For the list of notification types, schedules, and how to add new ones, see
[`../operations/README.md#notifications`](../operations/README.md#notifications).
