"""LinkedIn-connections contact lookup for ingested jobs.

Reads `data/connections.csv` and returns matching contacts for a company.
Extracted from `scripts/triage.py` in M3 (#537).
"""

import csv

from findajob.paths import BASE

CONNECTIONS = f"{BASE}/data/connections.csv"


def find_contacts(company: str | None) -> list[str]:
    contacts: list[str] = []
    if not company or not company.strip():
        return contacts
    try:
        with open(CONNECTIONS) as f:
            for row in csv.DictReader(f):
                contact_co = row.get("Company", "").strip()
                if not contact_co:
                    continue  # guard: '' in 'anything' is True in Python
                if company.lower() in contact_co.lower():
                    contacts.append(f"{row['First Name']} {row['Last Name']} ({row['Position']})")
    except Exception:
        pass
    return contacts
