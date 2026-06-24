"""
Test scenario 1: waitlist behavior with limited capacity.

Setup: only 2 companies (Bloomberg and IBM), 5 slots each, all 16 students.
That is 10 seats for 16 students, so 6 students must land on the waitlist.
This exercises the company-proposing Gale-Shapley matcher and the waitlist
output exactly as the real pipeline would, but with a tiny, easy to read setup.

It reuses the cached scores at talent_sprint/output/scores.csv, so it makes no
API calls. Run the real pipeline once first so that cache exists.

Usage:
  python test_waitlist_2companies.py
"""

import os
import sys

import pandas as pd

# Make the talent_sprint modules importable from this script, wherever it lives
# (project root or a subfolder like Tests/). Walk up until we find talent_sprint/.
_dir = os.path.dirname(os.path.abspath(__file__))
for _ in range(5):
    if os.path.isdir(os.path.join(_dir, "talent_sprint")):
        break
    _dir = os.path.dirname(_dir)
sys.path.insert(0, os.path.join(_dir, "talent_sprint"))

import config  # noqa: E402
from load_data import load_students, load_companies  # noqa: E402
from match import run_match  # noqa: E402

# Test knobs.
TEST_COMPANIES = ["Bloomberg", "IBM"]
SLOTS = 5


def main():
    if not os.path.exists(config.SCORES_CSV):
        print("No cached scores found. Run `python talent_sprint/main.py` first.")
        return

    # Override capacity for this test only (the matcher reads it at run time).
    config.SLOTS_PER_COMPANY = SLOTS

    # Load students (no resume parsing needed: matching only uses scores +
    # preferences + sponsorship, all already captured).
    students, _, _ = load_students(resume_texts={})
    companies = load_companies()

    # Keep only the two test companies.
    companies = companies[companies["name"].isin(TEST_COMPANIES)].reset_index(drop=True)
    if len(companies) != len(TEST_COMPANIES):
        print(f"WARNING: expected {TEST_COMPANIES}, found {list(companies['name'])}")

    # Restrict the cached scores to those companies.
    scores_df = pd.read_csv(config.SCORES_CSV).fillna("")
    scores_df = scores_df[scores_df["company_name"].isin(TEST_COMPANIES)].reset_index(drop=True)

    matches, waitlist, _, _ = run_match(students, companies, scores_df)

    print("=" * 70)
    print("TEST 1: WAITLIST WITH 2 COMPANIES, 5 SLOTS EACH")
    print("=" * 70)
    print(f"Companies: {TEST_COMPANIES}")
    print(f"Slots per company: {SLOTS}  (total seats: {SLOTS * len(TEST_COMPANIES)})")
    print(f"Students: {len(students)}")
    print(f"Matched: {len(matches)}   Waitlisted: {len(waitlist)}")

    expected_matched = min(len(students), SLOTS * len(TEST_COMPANIES))
    expected_waitlisted = len(students) - expected_matched
    ok = len(matches) == expected_matched and len(waitlist) == expected_waitlisted
    print(
        f"Expected: matched {expected_matched}, waitlisted {expected_waitlisted}  "
        f"-> {'PASS' if ok else 'CHECK'}"
    )

    print("\nMATCHED:")
    mdf = pd.DataFrame(matches).sort_values(["matched_company", "combined_score"], ascending=[True, False])
    print(mdf[["student_name", "matched_company", "fit_score", "preference_rank", "combined_score"]].to_string(index=False))

    print("\nWAITLIST (strongest near-misses first):")
    wdf = pd.DataFrame(waitlist)
    print(wdf[["student_name", "best_company", "best_combined_score", "best_fit_score"]].to_string(index=False))
    print("=" * 70)


if __name__ == "__main__":
    main()
