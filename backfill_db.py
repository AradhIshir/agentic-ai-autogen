"""
Backfill existing pipeline output files into SQLite (db/qa_testing.db).

Run once to populate the Test Repository with data from previous pipeline runs.
Safe to re-run — each sync replaces existing rows for that ticket so no duplicates.

Usage:
    python backfill_db.py                  # auto-discover all tickets from files
    python backfill_db.py EC-298 EC-299    # specific tickets only
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_sync import db_sync_for_step  # type: ignore


def _discover_ticket_ids() -> list[str]:
    """Scan output folders and return all unique ticket IDs that have at least one file."""
    ids: set[str] = set()

    for f in (ROOT / "TestCases").glob("*_Testcase.txt"):
        ids.add(f.stem.replace("_Testcase", ""))

    for f in (ROOT / "ResultReport").glob("execution_*.json"):
        ids.add(f.stem.replace("execution_", ""))

    for f in (ROOT / "generated_testscript").glob("Script_*.spec.ts"):
        m = re.match(r"Script_(.+?)_?\.spec\.ts$", f.name)
        if m:
            ids.add(m.group(1))

    return sorted(ids)


def backfill(ticket_id: str) -> None:
    print(f"\n{'─' * 52}")
    print(f"  Ticket: {ticket_id}")
    print(f"{'─' * 52}")

    tc_file = ROOT / "TestCases" / f"{ticket_id}_Testcase.txt"
    if tc_file.is_file():
        print(f"  [testcases]  {tc_file.name} → syncing…")
        db_sync_for_step(ticket_id, "testcases")
    else:
        print(f"  [testcases]  no file found – skipped")

    scripts = list((ROOT / "generated_testscript").glob(f"Script_{ticket_id}*.spec.ts"))
    if scripts:
        print(f"  [automation] {', '.join(s.name for s in scripts)} → syncing…")
        db_sync_for_step(ticket_id, "automation")
    else:
        print(f"  [automation] no script found – skipped")

    exec_file = ROOT / "ResultReport" / f"execution_{ticket_id}.json"
    if exec_file.is_file():
        print(f"  [execute]    {exec_file.name} → syncing…")
        db_sync_for_step(ticket_id, "execute")
    else:
        print(f"  [execute]    no execution JSON found – skipped")

    bug_files = list((ROOT / "ResultReport").glob(f"bug_{ticket_id}_*.txt"))
    if bug_files:
        print(f"  [bugs]       {len(bug_files)} bug file(s) → syncing…")
        db_sync_for_step(ticket_id, "bugs")
    else:
        print(f"  [bugs]       no bug files found – skipped")


if __name__ == "__main__":
    tickets = sys.argv[1:] if len(sys.argv) > 1 else _discover_ticket_ids()

    if not tickets:
        print("No ticket files found in TestCases/, ResultReport/, or generated_testscript/.")
        sys.exit(0)

    print(f"Backfilling {len(tickets)} ticket(s): {', '.join(tickets)}")
    for tid in tickets:
        backfill(tid)

    print(f"\n✅  Backfill complete — {len(tickets)} ticket(s) processed.")
    print("    Refresh the Streamlit Test Repository to see the data.")
