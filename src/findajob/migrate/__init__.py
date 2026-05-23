"""Stack migration tooling: docker → Fly.io cold cutover (#816).

Operator-only. Exports a stack's state (data/, companies/, candidate_context/)
to a single verifiable tarball, then imports it into a freshly-provisioned
Fly app so a tester's accumulated history survives the platform cutover.

Provisioning is out of scope — `ops/fly-deploy.sh` creates the app + volume;
this module only moves state. Secrets handoff is also out of scope; the
runbook prescribes `fly secrets import` against the source `data/.env`.

Stop-the-stack invariant: the operator must `docker compose stop` the source
stack before export. The exporter refuses to run against a stack whose
SQLite WAL is non-empty after `wal_checkpoint(TRUNCATE)`, which is how a
running scheduler/web pair would present.
"""
