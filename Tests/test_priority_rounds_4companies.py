"""
Test scenario 2: priority by running the matcher in two rounds.

Setup: 4 companies, 3 slots each, all 16 students.
  Round 1: run the real Gale-Shapley matcher with ONLY Bloomberg and IBM over
           the full student pool. They get first pick.
  Round 2: run the same matcher again with the REST of the companies (Wayfair,
           Amazon) over only the students who went unmatched in round 1.

This keeps the production matching algorithm (match.run_match) intact and gives
Bloomberg and IBM priority simply by letting them match first against everyone.
Each company gets up to 3 students. 4 companies x 3 seats = 12, so 4 students
land on the final waitlist.

It reuses the cached scores at talent_sprint/output/scores.csv, so it makes no
API calls. Run `python talent_sprint/main.py` once first so that cache exists.

Usage:
  python test_priority_rounds_4companies.py
"""

import os
import sys

import pandas as pd

# Make the talent_sprint modules importable from this outside-the-folder script.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "talent_sprint"))

import config  # noqa: E402
from load_data import load_students, load_companies  # noqa: E402
from match import run_priority_match  # noqa: E402

# The four companies in this test, and which two get first pick.
ALL_COMPANIES = ["Bloomberg", "IBM", "Wayfair", "Amazon"]
PRIORITY = ["Bloomberg", "IBM"]
SLOTS = 3


def main():
    if not os.path.exists(config.SCORES_CSV):
        print("No cached scores found. Run `python talent_sprint/main.py` first.")
        return

    # Override capacity and priority for this test only (read at run time).
    config.SLOTS_PER_COMPANY = SLOTS
    config.PRIORITY_COMPANIES = PRIORITY

    students, _, _ = load_students(resume_texts={})
    companies = load_companies()
    companies = companies[companies["name"].isin(ALL_COMPANIES)].reset_index(drop=True)

    scores_df = pd.read_csv(config.SCORES_CSV).fillna("")
    scores_df = scores_df[scores_df["company_name"].isin(ALL_COMPANIES)].reset_index(drop=True)

    print("=" * 70)
    print("TEST 2: PRIORITY VIA TWO MATCHING ROUNDS (uses match.run_priority_match)")
    print("=" * 70)
    print(f"Companies: {ALL_COMPANIES}")
    print(f"Priority (first pick): {PRIORITY}")
    print(f"Slots per company: {SLOTS}   Students: {len(students)}")

    # This is the exact same driver the real pipeline uses.
    matches, waitlist = run_priority_match(students, companies, scores_df)

    print("\nMATCHES:")
    mdf = pd.DataFrame(matches).sort_values(["matched_company", "combined_score"], ascending=[True, False])
    print(mdf[["matched_company", "student_name", "fit_score", "preference_rank", "combined_score"]].to_string(index=False))

    expected_matched = min(len(students), SLOTS * len(ALL_COMPANIES))
    ok = len(matches) == expected_matched and len(waitlist) == len(students) - expected_matched
    print(f"\nTOTAL matched: {len(matches)}   waitlisted: {len(waitlist)}")
    print(f"Expected matched {expected_matched}, waitlisted {len(students) - expected_matched}  "
          f"-> {'PASS' if ok else 'CHECK'}")

    print("\nFINAL WAITLIST (strongest near-misses first):")
    print(pd.DataFrame(waitlist)[["student_name", "best_company", "best_combined_score", "best_fit_score"]].to_string(index=False))
    print("=" * 70)


if __name__ == "__main__":
    main()
