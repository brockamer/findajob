#!/usr/bin/env python3
# Args: company, jd_text, outdir, [file_prefix], [timestamp_fn]
"""
Find LinkedIn connections at a company and generate outreach drafts.
Called by prep_application.py with: company, jd_text, outdir,
[file_prefix], [timestamp_fn]. The last two are optional; if omitted the
script reads the prefix from profile.md and generates its own timestamp
(useful for running this script directly, outside of a prep cycle).
"""

import csv
import os
import re
import subprocess
import sys
from datetime import datetime

from findajob.paths import AICHAT, BASE
from findajob.utils import (
    build_outreach_filename,
    load_env,
    load_voice_samples,
    log_event,
    read_candidate_name,
    read_file_prefix,
)


def company_match(search, contact_company):
    def normalize_co(s):
        s = s.lower().strip()
        s = re.sub(r"\b(inc|llc|ltd|corp|co|\.com|\.io)\b\.?", "", s)
        return re.sub(r"\s+", " ", s).strip()

    s = normalize_co(search)
    c = normalize_co(contact_company)
    # Guard: blank company matches nothing. '' in 'anything' is True in Python.
    if not s or not c:
        return False
    return s in c or c in s


CONNECTIONS = f"{BASE}/data/connections.csv"
PROFILE_PATH = f"{BASE}/candidate_context/profile.md"

load_env()


def find_contacts(company):
    # connections.csv is optional — missing file means the user has no LinkedIn
    # export configured. Return empty without logging an error. True parse/IO
    # failures still log via the narrowed except below.
    if not os.path.exists(CONNECTIONS):
        return []
    contacts = []
    try:
        with open(CONNECTIONS) as f:
            for row in csv.DictReader(f):
                if company_match(company, row.get("Company", "")):
                    contacts.append(
                        {
                            "name": f"{row['First Name']} {row['Last Name']}",
                            "first": row["First Name"],
                            "title": row.get("Position", ""),
                            "company": row.get("Company", ""),
                            "connected_on": row.get("Connected On", ""),
                            "url": row.get("URL", ""),
                        }
                    )
    except FileNotFoundError:
        return []
    except Exception as e:
        log_event("find_contacts_error", error=str(e))
    return contacts


def rank_contacts(contacts):
    def score(c):
        s = 0
        title_lower = c["title"].lower()
        if any(k in title_lower for k in ["director", "vp", "vice president", "head of", "principal", "staff"]):
            s += 3
        if any(k in title_lower for k in ["senior", "lead", "manager"]):
            s += 2
        if any(k in title_lower for k in ["npi", "data center", "infrastructure", "hardware", "operations", "ops"]):
            s += 2
        if any(k in title_lower for k in ["recruiter", "talent", "recruiting", "hr", "people"]):
            s += 1
        return s

    return sorted(contacts, key=score, reverse=True)


def generate_outreach(
    contact,
    company,
    jd_text,
    outdir,
    profile_text,
    file_prefix,
    timestamp_fn,
    candidate_name,
    voice_samples,
    is_synthetic=False,
):
    """Call aichat-ng outreach_drafter role. Profile + voice samples injected directly — no RAG."""
    voice_section = f"VOICE SAMPLES:\n{voice_samples}\n\n" if voice_samples else ""
    mode_marker = "<<SPECULATIVE_MODE>>\n\n" if is_synthetic else ""
    prompt = (
        f"{mode_marker}"
        f"CANDIDATE PROFILE:\n{profile_text}\n\n"
        f"{voice_section}"
        f"Draft a LinkedIn outreach message from {candidate_name} to {contact['name']}, "
        f"who is a {contact['title']} at {company}.\n\n"
        f"Context: {candidate_name} is exploring a role at {company}.\n\n"
        f"JD:\n{jd_text}"
    )
    cmd = [AICHAT, "--role", "outreach_drafter", "-S", prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    draft = result.stdout.strip()

    filename = build_outreach_filename(contact["name"], company, timestamp_fn, file_prefix)
    outpath = os.path.join(outdir, filename)
    os.makedirs(outdir, exist_ok=True)
    with open(outpath, "w") as f:
        f.write(f"TO: {contact['name']} — {contact['title']} at {contact['company']}\n")
        f.write(f"PROFILE: {contact['url']}\n")
        f.write(f"CONNECTED: {contact['connected_on']}\n\n")
        f.write("--- DRAFT ---\n\n")
        f.write(draft)
        f.write("\n\n--- END DRAFT ---\n")
        f.write("\n[ ] Reviewed  [ ] Sent  [ ] Response received\n")

    return outpath


def main():
    if len(sys.argv) < 4:
        print("Usage: find_contacts.py <company> <jd_text> <outdir> [file_prefix] [timestamp_fn] [is_synthetic]")
        sys.exit(1)

    company = sys.argv[1]
    jd_text = sys.argv[2]
    outdir = sys.argv[3]
    file_prefix = sys.argv[4] if len(sys.argv) > 4 else read_file_prefix()
    timestamp_fn = sys.argv[5] if len(sys.argv) > 5 else datetime.now().strftime("%Y%m%d-%H%M%S")
    is_synthetic = sys.argv[6] == "1" if len(sys.argv) > 6 else False

    try:
        with open(PROFILE_PATH) as f:
            profile_text = f.read()
    except FileNotFoundError:
        profile_text = "[Profile not found]"

    candidate_name = read_candidate_name()
    voice_samples = load_voice_samples()
    log_event("voice_samples_loaded", caller="find_contacts", chars=len(voice_samples))

    contacts = find_contacts(company)
    ranked = rank_contacts(contacts)
    top = ranked[:5]

    if not top:
        log_event("find_contacts", company=company, found=0)
        return

    log_event("find_contacts", company=company, found=len(contacts), drafting=len(top))

    for contact in top:
        generate_outreach(
            contact,
            company,
            jd_text,
            outdir,
            profile_text,
            file_prefix,
            timestamp_fn,
            candidate_name,
            voice_samples,
            is_synthetic=is_synthetic,
        )

    print(f"Generated {len(top)} outreach drafts for {company}")


if __name__ == "__main__":
    main()
