#!/usr/bin/env python3
# scripts/diag/debug_contacts.py
# Shows contact matching for a batch of jobs, with per-match diagnostics.

import csv
import re
import sqlite3

from findajob.db import connect
from findajob.paths import BASE

DB = f"{BASE}/data/pipeline.db"
CSV = f"{BASE}/data/connections.csv"


def normalize_co(s):
    s = s.lower().strip()
    s = re.sub(r"\b(inc|llc|ltd|corp|co|\.com|\.io)\b\.?", "", s)
    return re.sub(r"\s+", " ", s).strip()


def company_match(search, contact_company):
    s = normalize_co(search)
    c = normalize_co(contact_company)
    return s in c or c in s


def match_reason(search, contact_company):
    s = normalize_co(search)
    c = normalize_co(contact_company)
    reasons = []
    if not c:
        reasons.append("BLANK_COMPANY")
    elif s in c:
        reasons.append(f's_in_c ("{s}" ⊆ "{c}")')
    elif c in s:
        reasons.append(f'c_in_s ("{c}" ⊆ "{s}")')
    return ", ".join(reasons)


# Load contacts
contacts = []
with open(CSV) as f:
    for row in csv.DictReader(f):
        contacts.append(row)

print(f"Loaded {len(contacts)} contacts from CSV")
blank_cos = [r for r in contacts if not r.get("Company", "").strip()]
print(f"Contacts with blank Company field: {len(blank_cos)} — these match EVERY job\n")

# Pull 20 jobs from DB
con = connect(DB, timeout=5.0)
con.row_factory = sqlite3.Row
rows = con.execute("SELECT id, title, company, relevance_score FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
con.close()

print(f"{'ID':<8} {'Score':<6} {'Company':<35} {'Title':<45} Matches (non-blank co)")
print("-" * 130)

for job in rows:
    jid = job["id"]
    title = (job["title"] or "")[:44]
    company = job["company"] or ""
    score = job["relevance_score"]

    matches = []
    for r in contacts:
        if company_match(company, r.get("Company", "")):
            name = f"{r['First Name']} {r['Last Name']}".strip()
            contact_co = r.get("Company", "").strip()
            reason = match_reason(company, contact_co)
            matches.append((name, contact_co, reason))

    # Separate blank-company noise from real matches
    real = [(n, c, r) for n, c, r in matches if "BLANK" not in r]
    ghosts = [(n, c, r) for n, c, r in matches if "BLANK" in r]
    satrom = [x for x in matches if "Satrom" in x[0]]

    flag = " *** SATROM ***" if satrom else ""
    print(f"{str(jid):<8} {str(score or ''):<6} {company:<35} {title:<45} real={len(real)} ghost={len(ghosts)}{flag}")

    if real:
        for name, co, reason in real[:5]:
            print(f"         -> {name:<30} {co:<35} [{reason}]")
    if satrom:
        for name, co, reason in satrom:
            print(f"  !SATROM: {name:<30} {co:<35} [{reason}]")
    if real or satrom:
        print()
