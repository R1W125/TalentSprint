"""
Regression test: two students contending for the same single time slot.

Both students are available ONLY at 4:00 pm and are matched to the SAME company,
which can run one interview per slot. The greedy-by-score scheduler should give
the one open slot to the higher-scored student and leave the other unscheduled
with reason "no available interview slot".

This makes no API calls; it drives schedule_interviews directly with synthetic
data, so it always runs and stays deterministic.

Usage:
  python Tests/test_slot_conflict.py
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

from schedule import schedule_interviews  # noqa: E402


def main():
    # Two students, both only free at 4:00 pm, both matched to the same company.
    students = pd.DataFrame(
        [
            {"email": "taylor@bu.edu", "name": "Taylor Test", "availability": ["4:00 pm"]},
            {"email": "jordan@bu.edu", "name": "Jordan Test", "availability": ["4:00 pm"]},
        ]
    )
    matches = [
        {
            "student_name": "Taylor Test",
            "student_email": "taylor@bu.edu",
            "matched_company": "Acme",
            "fit_score": 88,
            "combined_score": 88,
            "preference_rank": 1,
            "reasoning": "strong",
        },
        {
            "student_name": "Jordan Test",
            "student_email": "jordan@bu.edu",
            "matched_company": "Acme",
            "fit_score": 74,
            "combined_score": 74,
            "preference_rank": 1,
            "reasoning": "good",
        },
    ]

    scheduled, unscheduled = schedule_interviews(matches, students)

    print("=" * 70)
    print("TEST: two students, one shared 4:00 pm slot, same company")
    print("=" * 70)
    print("SCHEDULED:")
    for m in scheduled:
        print(f"  {m['student_name']:14} fit {m['fit_score']} -> "
              f"{m['matched_company']} @ {m['time_slot']}")
    print("UNSCHEDULED (-> waitlist):")
    for u in unscheduled:
        print(f"  {u['student_name']:14} fit {u['best_fit_score']} -> "
              f"{u['best_company']}  reason: {u['reason']}")

    # Assertions: higher score keeps the slot, lower score is bumped.
    checks = []
    checks.append(("exactly one scheduled", len(scheduled) == 1))
    checks.append(("exactly one unscheduled", len(unscheduled) == 1))
    if scheduled:
        checks.append(("higher score (Taylor) is scheduled",
                       scheduled[0]["student_email"] == "taylor@bu.edu"))
        checks.append(("scheduled slot is 4:00 pm",
                       scheduled[0]["time_slot"] == "4:00 pm"))
    if unscheduled:
        checks.append(("lower score (Jordan) is unscheduled",
                       unscheduled[0]["student_email"] == "jordan@bu.edu"))
        checks.append(("reason is no available interview slot",
                       unscheduled[0]["reason"] == "no available interview slot"))

    print("\nSANITY CHECKS:")
    all_ok = True
    for label, ok in checks:
        all_ok = all_ok and ok
        print(f"  {label:45} {'PASS' if ok else 'FAIL'}")
    print("=" * 70)
    print("RESULT:", "PASS" if all_ok else "FAIL")
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
