# Pull requests from forks

A pull request from a fork runs CI with less than a same-repository pull request
gets. GitHub gives it a read-only `GITHUB_TOKEN` and passes it no Actions secrets.
Two required checks change their behavior because of that (#1223):

| Check | Same-repository pull request | Pull request from a fork |
|---|---|---|
| `build-push` | Builds the image and pushes `ghcr.io/<owner>/findajob:pr-<N>` | Builds the image and pushes nothing. There is no `pr-<N>` image to test on a stack |
| `scan` | Scans the added lines against `PII_PATTERNS_REGEX`. If the secret is missing, the check fails | The secret is not available. The check passes, but **nothing was scanned**. The job summary says so |

A run that Dependabot triggers gets no Actions secrets either, so `scan` treats it
the same way as a fork. If you want Dependabot pull requests scanned in CI, store the
same patterns as a Dependabot secret too:
`gh secret set PII_PATTERNS_REGEX --app dependabot < <patterns-file>`. Use the
extraction recipe in [config-reference.md](../operations/config-reference.md#ci-side-defense-defense-in-depth-314)
to make the patterns file.

**Do not move either workflow to `pull_request_target`** to get the token or the
secret back. That event runs the contributor's code with this repository's secrets
and a write token.

## Before you merge a pull request from a fork

1. **Approve the workflow run.** GitHub holds CI for a first-time contributor until a
   maintainer clicks **Approve and run workflows** on the pull request. Read the diff
   first, especially `.github/`, `Dockerfile`, and anything under `scripts/`.
2. **Scan the diff locally with your pre-commit hook.** CI did not do it. The steps
   below run your installed `.git/hooks/pre-commit` (the same `PATTERNS` and
   `SECRET_PATHS` lists as the secret) against everything the pull request adds. Run
   them from your clone of this repository:

   ```bash
   N=1234                        # the pull request number
   SCAN_DIR=../findajob-scan-pr-$N
   git fetch origin main "pull/$N/head:scan-pr-$N"
   git worktree add "$SCAN_DIR" "scan-pr-$N"
   cd "$SCAN_DIR"
   # Stage the pull request's whole change on top of its merge base, so the hook
   # sees the same added lines as the CI scan (origin/main...HEAD).
   git reset --soft "$(git merge-base origin/main HEAD)"
   bash "$(git rev-parse --git-path hooks/pre-commit)"; echo "exit=$?"
   cd -
   git worktree remove --force "$SCAN_DIR"
   git branch -D "scan-pr-$N"
   ```

   - `exit=0` and the `pre-commit: PII scan: N patterns × M added lines` line: clean.
     M is the number of lines the pull request adds.
   - `exit=1` and `blocked — …`: the pull request adds a match. Ask the contributor to
     remove it, or remove it yourself before merging. Do not merge it as it is.
   - No `PII scan:` line: the hook did not run. Check that `.git/hooks/pre-commit` is
     installed (see [CONTRIBUTING.md](../../CONTRIBUTING.md#dev-setup)).

   The steps run your own hook from `.git/hooks/`, not a file from the pull request,
   and they execute none of the pull request's code.
3. **Read the diff for what the hook cannot match.** Patterns catch known identifiers.
   They do not catch operator topology or field-locked content (see the
   PII and Domain-Neutrality section of `AGENTS.md`).

CI does not scan the merge either: `pii-scan.yml` runs only on `pull_request`, not on
a push to `main`. For a pull request from a fork, the local scan is the only scan.
