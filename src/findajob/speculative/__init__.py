"""Speculative ingest pipeline — see docs/superpowers/specs/2026-04-28-speculative-ingest-131-design.md.

Modules:
- runner.py    : orchestrates the briefing + role-synth call sequence
- parser.py    : validates LLM output into role-card dicts
- approver.py  : on operator approve, writes jobs rows from kept role cards
- storage.py   : creates speculative briefing folder + writes briefing.md
"""
