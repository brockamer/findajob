#!/usr/bin/env python3
"""Find LinkedIn connections at a company and generate outreach drafts.

Thin entry-point shim. Real logic lives in ``findajob.find_contacts``.
Spawned as a subprocess from ``findajob.prep.orchestrator``.
"""

from findajob.find_contacts import main

if __name__ == "__main__":
    main()
