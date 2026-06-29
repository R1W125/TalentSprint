"""
Test scenario: 3 companies, all students, full match + schedule flow.

Setup: only 3 companies (IBM, Bloomberg, Wayfair), all students, each company
with SLOTS seats. With all students competing for just 3 companies this forces
both a capacity waitlist and real time-slot contention, so it exercises the
matcher, the waitlist, and the new interview scheduler end to end.

It reuses the cached scores at talent_sprint/output/scores.csv, so it makes no
API calls. Run `python talent_sprint/main.py --rescore` first if the cache is
stale relative to the current students/companies.

Usage:
  python Tests/test_3companies_all_students.py
"""

import os
import sys

import pandas as pd

# Make the talent_sprint modules importable from this script, wherever it lives.
_dir = os.path.dirname(os.path.abspath(__file__))
for _ in range(5):
    if os.path.isdir(os.path.join(_dir, "talent_sprint")):
        break
    _dir = os.path.dirname(_dir)
sys.path.insert(0, os.path.join(_dir, "talent_sprint"))

import config  # noqa: E402
from load_data import load_students, load_companies, slot_minutes  # noqa: E402
from match import run_match  # noqa: E402
from schedule import schedule_interviews  # noqa: E402

TEST_COMPANIES = ["IBM", "Bloomberg", "Wayfair"]
SLOTS = 4  # seats per company; 3 x 4 = 12 seats for all students


def main():
    if not os.path.exists(config.SCORES_CSV):
        print("No cached scores found. Run `python talent_sprint/main.py --rescore` first.")
        return

    # Override capacity for this test; no priority, no eligibility filtering here
    # (we want ALL students in the pool).
    config.SLOTS_PER_COMPANY = SLOTS

    students, _, _ = load_students(resume_texts={})
    companies = load_companies()
    companies = companies[companies["name"].isin(TEST_COMPANIES)].reset_index(drop=True)

    scores_df = pd.read_csv(config.SCORES_CSV).fillna("")
    scores_df = scores_df[scores_df["company_name"].isin(TEST_COMPANIES)].reset_index(drop=True)

    # Match, then schedule interview times.
    matches, waitlist, _, _ = run_match(students, companies, scores_df)
    matches, unscheduled = schedule_interviews(matches, students)
    for w in waitlist:
        w.setdefault("reason", "not matched (no remaining capacity)")
    waitlist = waitlist + unscheduled
    waitlist.sort(key=lambda r: r["best_combined_score"], reverse=True)

    print("=" * 70)
    print("TEST: 3 COMPANIES, ALL STUDENTS, MATCH + SCHEDULE")
    print("=" * 70)
    print(f"Companies: {TEST_COMPANIES}   Seats each: {SLOTS}")
    print(f"Students: {len(students)}")
    print(f"Matched and scheduled: {len(matches)}   Waitlisted: {len(waitlist)}")

    print("\nSCHEDULE (by company, then time):")
    sched = sorted(matches, key=lambda m: (m["matched_company"], slot_minutes(m["time_slot"])))
    for m in sched:
        print(f"  {m['matched_company']:12} {m['time_slot']:9} {m['student_name']:18} "
              f"fit {m['fit_score']}")

    print("\nWAITLIST (with reason):")
    for w in waitlist:
        print(f"  {w['student_name']:18} best={w['best_company']:12} "
              f"score {w['best_combined_score']:5}  ({w['reason']})")

    # Sanity checks.
    counts_ok = len(matches) + len(waitlist) == len(students)
    # No company double-books a time slot.
    pairs = [(m["matched_company"], m["time_slot"]) for m in matches]
    no_clash = len(pairs) == len(set(pairs))
    # Every scheduled slot is one the student actually marked available.
    avail = {s["email"]: set(s["availability"]) for _, s in students.iterrows()}
    avail_ok = all(m["time_slot"] in avail.get(m["student_email"], set()) for m in matches)

    print("\nSANITY CHECKS:")
    print(f"  matched + waitlisted == students ({len(students)}): {'PASS' if counts_ok else 'FAIL'}")
    print(f"  no company double-books a slot:                {'PASS' if no_clash else 'FAIL'}")
    print(f"  every slot is in the student's availability:   {'PASS' if avail_ok else 'FAIL'}")
    print("=" * 70)


if __name__ == "__main__":
    main()
