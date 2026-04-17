# Claude Code Integration

This project uses Claude Code (the Anthropic CLI tool) as a daily operator — for writing new features, debugging pipeline issues, updating role prompts, and working with the codebase interactively.

---

## How It Works

Claude Code reads `CLAUDE.md` at the start of every session. `CLAUDE.md` contains:
- Architectural rules and non-obvious invariants
- Tool path governance (what binary to call for each operation)
- Model/role assignments
- Sheet layout and status workflow
- Working style preferences

At the bottom of `CLAUDE.md` is:
```
@CLAUDE.local.md
```

This directive causes Claude Code to also load `CLAUDE.local.md` (gitignored), which contains your personal identifiers: name, target companies, ntfy topic, platform-specific paths, Google Form URL.

This split means the public repo's `CLAUDE.md` is fully generic, while your session still has full context.

---

## Session Context

Claude Code is a stateless CLI — each session starts fresh. `CLAUDE.md` is how you give it standing context that persists across sessions. It's read every time.

The memory system (in `~/.claude/projects/.../memory/`) provides additional cross-session recall for learned preferences, feedback, and project state. This complements CLAUDE.md (which is static) with dynamic learned context.

---

## What Claude Code Does in This Project

Claude Code is used for:

- **Feature development** — writing new scripts, adding pipeline stages, improving role prompts
- **Debugging** — diagnosing triage failures, sheet sync issues, API errors from log output
- **Role prompt iteration** — editing `config/roles/*.md` after observing poor output quality
- **Infrastructure changes** — modifying scheduler configs, adding new systemd units
- **Documentation** — updating CLAUDE.md, GitHub issues, and these docs

Claude Code is NOT used to:
- Run the pipeline autonomously (that's the scheduler's job)
- Access your real resume or profile (those are gitignored)
- Write to Google Sheets directly (it edits scripts that do this)

---

## CLAUDE.md Maintenance

`CLAUDE.md` should stay up to date as the project evolves. Update it when:
- A new script is added (add to Key File Locations)
- A model changes (update the Pipeline Context Table)
- An architectural rule is discovered or changes
- The sheet layout changes

`CLAUDE.local.md` should be updated when:
- Your target company list changes
- You migrate to a new machine (update binary paths if they differ)
- Your ntfy topic or Google Form URL changes

---

## CLAUDE.local.md Template

See `CLAUDE.local.md.example` for the template. Key sections:

```markdown
## Who You Are Helping
[Your name and background — Claude Code uses this to calibrate tone and relevance]

## Critical Name Clarification
[Any abbreviations that LLMs commonly misinterpret]

## Platform
[Binary paths and aichat-ng config location]

## Systemd Schedule
[Your actual timer names and timing]

## Notification
[Your ntfy topic]

## Google Services
[Form URL, sheet IDs]
```

---

## Publishing This Project

The `findajob` repo is designed to be public without exposing personal data.

**What is public:**
- All scripts (generic, no personal identifiers)
- Role prompts (sanitized — references to candidate name/location removed)
- Configuration templates (`.example` files)
- This documentation
- `CLAUDE.md` (generic, no personal info)

**What is gitignored:**
- `candidate_context/profile.md` — your candidate profile
- `candidate_context/master_resume.md` — your master resume
- `candidate_context/voice_samples/*.txt` — your writing samples
- `config/jsearch_queries.txt` — your search queries
- `config/feed_urls.txt` — your Greenhouse company slugs
- `config/target_companies.md` — your target list
- `data/.env` — all API keys
- `config/*.json`, `config/sheet_id.txt` — Google credentials
- `config/paths.env` — your local binary paths
- `CLAUDE.local.md` — your personal Claude context
- `data/connections.csv` — your LinkedIn connections
- `data/pipeline.db` — your job database
- `companies/` — your prep folders
- `logs/` — your pipeline logs

A new user cloning the repo gets a complete, working codebase with `.example` templates for every personal file they need to create.

---

## Tips for Working With Claude Code in This Project

**Always read before editing.** Claude Code reads files before modifying them. If you tell it to "change X in triage.py", it reads the current state first. This prevents stale edits.

**Use `!` for shell commands.** In the Claude Code CLI, prefix a command with `!` to run it in the current session. The output lands in the conversation, so Claude Code can see results:
```
! python3 scripts/triage.py 2>&1 | tail -20
```

**Reference GitHub Issues for open work.** The [project board](https://github.com/users/brockamer/projects/1) tracks all bugs and enhancement ideas. Use `gh issue list` for context. When you close an issue, close it on GitHub.

**Binary path governance.** Claude Code is instructed never to guess binary paths. If it suggests a path you don't recognize, check `config/paths.py` and `config/paths.env`. The right answer is always in those files.

**Role prompt changes.** The role `.md` files in `config/roles/` have YAML frontmatter (model, temperature, max_tokens). Changing the `model:` line in a role file changes which model runs for that role. Claude Code can edit these directly.
