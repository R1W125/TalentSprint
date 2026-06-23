"""
End to end Talent Sprint matching pipeline.

Run order: parse resumes, load data, score (or load cache), match, write
outputs, then print a summary report.

Usage:
  python main.py            # uses cached output/scores.csv if present
  python main.py --rescore  # forces a fresh Gemini scoring run
"""

import sys

import pandas as pd

import config
from parse_resumes import parse_resumes
from load_data import (
    load_students,
    load_companies,
    validate_and_report,
)
from score import score_all
from match import run_priority_match


def _write_matches(matches):
    cols = [
        "student_name",
        "student_email",
        "matched_company",
        "fit_score",
        "preference_rank",
        "combined_score",
        "reasoning",
    ]
    df = pd.DataFrame(matches, columns=cols)
    df.to_csv(config.MATCHES_CSV, index=False)
    return df


def _write_waitlist(waitlist):
    cols = [
        "student_name",
        "student_email",
        "best_company",
        "best_combined_score",
        "best_fit_score",
    ]
    df = pd.DataFrame(waitlist, columns=cols)
    df.to_csv(config.WAITLIST_CSV, index=False)
    return df


def _print_summary(students, companies, matches, waitlist, missing_resume, scores_df):
    total = len(students)
    matched = len(matches)
    wl = len(waitlist)

    print()
    print("=" * 70)
    print("TALENT SPRINT SUMMARY REPORT")
    print("=" * 70)
    print(f"Total students:    {total}")
    print(f"Total matched:     {matched}")
    print(f"Total waitlisted:  {wl}")

    # Per company: slots filled and average fit score of matched students.
    print("\nPER COMPANY (slots filled / capacity, average fit of matched):")
    matches_by_company = {}
    for m in matches:
        matches_by_company.setdefault(m["matched_company"], []).append(m)

    underfilled = []
    for _, company in companies.iterrows():
        cname = company["name"]
        ms = matches_by_company.get(cname, [])
        filled = len(ms)
        avg_fit = (sum(m["fit_score"] for m in ms) / filled) if filled else 0.0
        print(
            f"  {cname:42} {filled:2d}/{config.SLOTS_PER_COMPANY}   "
            f"avg fit {avg_fit:5.1f}"
        )
        if filled < config.SLOTS_PER_COMPANY:
            underfilled.append((cname, filled))

    # Preference outcome distribution.
    buckets = {"1st": 0, "2nd": 0, "3rd": 0, "lower (4+)": 0, "unranked": 0}
    for m in matches:
        rank = m["preference_rank"]
        if rank == "unranked":
            buckets["unranked"] += 1
        elif rank == 1:
            buckets["1st"] += 1
        elif rank == 2:
            buckets["2nd"] += 1
        elif rank == 3:
            buckets["3rd"] += 1
        else:
            buckets["lower (4+)"] += 1

    print("\nPREFERENCE OUTCOMES (matched students):")
    for label in ["1st", "2nd", "3rd", "lower (4+)", "unranked"]:
        print(f"  {label:12} {buckets[label]}")

    # Students filtered to 0 by the sponsorship hard filter.
    filtered = scores_df[
        scores_df["reasoning"].astype(str).str.startswith("Hard filter:")
    ]
    filtered_students = sorted(set(filtered["student_email"]))
    print(f"\nSTUDENTS HIT BY THE SPONSORSHIP HARD FILTER: {len(filtered_students)}")
    for e in filtered_students:
        n_companies = len(filtered[filtered["student_email"] == e])
        print(f"  {e:25} zeroed for {n_companies} non-sponsoring companies")

    # Missing resumes.
    print(f"\nSTUDENTS MISSING RESUMES: {len(missing_resume)}")
    for e in missing_resume:
        print(f"  {e}")

    # Underfilled note.
    if underfilled:
        print("\nNOTE: the following companies did not fill all "
              f"{config.SLOTS_PER_COMPANY} slots:")
        for cname, filled in underfilled:
            print(f"  {cname:42} {filled}/{config.SLOTS_PER_COMPANY}")
        print("  (Expected when there are fewer students than total capacity.)")
    else:
        print(f"\nAll companies filled their {config.SLOTS_PER_COMPANY} slots.")
    print("=" * 70)


def main():
    rescore = "--rescore" in sys.argv

    # 1. Parse resumes.
    print("Step 1: parsing resumes...")
    resume_texts, unmatched_resume_files = parse_resumes()
    print(f"  parsed {len(resume_texts)} resume(s).")
    if unmatched_resume_files:
        print(f"  unmatched resume files (name is not a valid email): {unmatched_resume_files}")

    # 2. Load data.
    print("\nStep 2: loading data...")
    students, missing_resume, choice_company_names = load_students(
        resume_texts=resume_texts
    )
    companies = load_companies()
    validate_and_report(students, companies, missing_resume, choice_company_names)

    # 3. Score (or load cache).
    print("\nStep 3: scoring fit...")
    scores_df = score_all(students, companies, rescore=rescore)

    # 4. Match (priority aware; falls back to a single round when no priority set).
    print("\nStep 4: matching...")
    if config.PRIORITY_COMPANIES:
        print(f"  priority companies configured: {config.PRIORITY_COMPANIES}")
    matches, waitlist = run_priority_match(students, companies, scores_df)

    # 5. Write outputs.
    print("\nStep 5: writing outputs...")
    _write_matches(matches)
    _write_waitlist(waitlist)
    print(f"  wrote {config.MATCHES_CSV}")
    print(f"  wrote {config.WAITLIST_CSV}")

    # Summary.
    _print_summary(students, companies, matches, waitlist, missing_resume, scores_df)


if __name__ == "__main__":
    main()
