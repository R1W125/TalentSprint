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
from parse_company_docs import parse_company_docs
from load_data import (
    load_students,
    load_companies,
    validate_and_report,
    filter_by_graduation,
    filter_by_sponsorship,
)
from score import score_all
from match import run_priority_match
from schedule import schedule_interviews


def _write_matches(matches):
    cols = [
        "student_name",
        "student_email",
        "matched_company",
        "time_slot",
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
        "reason",
    ]
    df = pd.DataFrame(waitlist, columns=cols)
    df.to_csv(config.WAITLIST_CSV, index=False)
    return df


def _write_schedule(matches):
    """Write the timetable, sorted by company then time slot."""
    from load_data import slot_minutes

    cols = ["matched_company", "time_slot", "student_name", "student_email", "fit_score"]
    rows = sorted(
        matches,
        key=lambda m: (m["matched_company"], slot_minutes(m.get("time_slot", ""))),
    )
    df = pd.DataFrame(rows, columns=cols)
    df.to_csv(config.SCHEDULE_CSV, index=False)
    return df


def _print_summary(
    students,
    companies,
    matches,
    waitlist,
    missing_resume,
    scores_df,
    students_loaded=None,
    excluded_grad=None,
    excluded_sponsor=None,
):
    excluded_grad = excluded_grad or []
    excluded_sponsor = excluded_sponsor or []
    eligible = len(students)  # the pool that was scored and matched
    if students_loaded is None:
        students_loaded = eligible + len(excluded_grad) + len(excluded_sponsor)
    matched = len(matches)
    wl = len(waitlist)

    print()
    print("=" * 70)
    print("TALENT SPRINT SUMMARY REPORT")
    print("=" * 70)

    # Stage by stage funnel: registered -> excluded -> eligible -> matched/waitlist.
    print("STUDENT FUNNEL")
    print(f"  Registered (loaded):            {students_loaded}")
    print(f"  Excluded, graduation year:      {len(excluded_grad)}")
    print(f"  Excluded, work authorization:   {len(excluded_sponsor)}")
    print(f"  Eligible (scored and matched):  {eligible}")
    print(f"  Matched:                        {matched}")
    print(f"  Waitlisted:                     {wl}")

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

    # Interview scheduling outcomes.
    from load_data import slot_minutes

    no_slot = [w for w in waitlist if w.get("reason") == "no available interview slot"]
    per_slot = {}
    for m in matches:
        per_slot[m.get("time_slot", "?")] = per_slot.get(m.get("time_slot", "?"), 0) + 1
    print("\nINTERVIEW SCHEDULING:")
    print(f"  scheduled into a time slot:     {len(matches)}")
    print(f"  could not be scheduled (no slot): {len(no_slot)}")
    if per_slot:
        print("  interviews per time slot:")
        for slot in sorted(per_slot, key=slot_minutes):
            print(f"    {slot:10} {per_slot[slot]}")
    for w in no_slot:
        print(f"  unscheduled: {w['student_email']:25} (matched to {w['best_company']})")

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

    # Excluded BEFORE scoring, by stage.
    print(f"\nEXCLUDED BEFORE SCORING - GRADUATION YEAR: {len(excluded_grad)}")
    for ex in excluded_grad:
        print(f"  {ex['email']:25} grad={ex['graduation_date']!r:8} ({ex['reason']})")

    print(f"\nEXCLUDED BEFORE SCORING - WORK AUTHORIZATION: {len(excluded_sponsor)}")
    for ex in excluded_sponsor:
        print(f"  {ex['email']:25} ({ex['reason']})")

    # Per-pair sponsorship hard filter at match time. Only relevant when the
    # work authorization exclusion above is off and such students remain in play.
    in_play = set(students["email"])
    filtered = scores_df[
        scores_df["reasoning"].astype(str).str.startswith("Hard filter:")
        & scores_df["student_email"].isin(in_play)
    ]
    filtered_students = sorted(set(filtered["student_email"]))
    if filtered_students:
        print(f"\nSTUDENTS HIT BY THE PER-PAIR SPONSORSHIP HARD FILTER: {len(filtered_students)}")
        for e in filtered_students:
            n_companies = len(filtered[filtered["student_email"] == e])
            print(f"  {e:25} zeroed for {n_companies} non-sponsoring companies")

    # Missing resumes (flagged, but NOT excluded; they are still scored/matched).
    print(f"\nFLAGGED - MISSING RESUMES (still included): {len(missing_resume)}")
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

    # 1. Parse resumes and company job description documents.
    print("Step 1: parsing resumes and company documents...")
    resume_texts, unmatched_resume_files = parse_resumes()
    print(f"  parsed {len(resume_texts)} resume(s).")
    if unmatched_resume_files:
        print(f"  unmatched resume files (name is not a valid email): {unmatched_resume_files}")
    doc_texts, unmatched_doc_files = parse_company_docs()
    print(f"  parsed {len(doc_texts)} company document(s).")
    if unmatched_doc_files:
        print(f"  unmatched company document files: {unmatched_doc_files}")

    # 2. Load data.
    print("\nStep 2: loading data...")
    students, missing_resume, choice_company_names = load_students(
        resume_texts=resume_texts
    )
    companies = load_companies(doc_texts=doc_texts)
    students_loaded = len(students)  # original count, before any eligibility filter

    # Eligibility filters, applied BEFORE scoring so ineligible students never
    # reach the API or the match. Graduation first, then work authorization;
    # each excluded student is attributed to the first filter that removed them.
    students, excluded_grad = filter_by_graduation(students)
    students, excluded_sponsor = filter_by_sponsorship(students)

    # Keep the missing-resume list consistent with who actually remains.
    kept_emails = set(students["email"])
    missing_resume = [e for e in missing_resume if e in kept_emails]

    validate_and_report(students, companies, missing_resume, choice_company_names)

    # 3. Score (or load cache).
    print("\nStep 3: scoring fit...")
    scores_df = score_all(students, companies, rescore=rescore)

    # 4. Match (priority aware; falls back to a single round when no priority set).
    print("\nStep 4: matching...")
    if config.PRIORITY_COMPANIES:
        print(f"  priority companies configured: {config.PRIORITY_COMPANIES}")
    matches, waitlist = run_priority_match(students, companies, scores_df)

    # 4b. Schedule each matched student into an interview time slot (greedy by
    # score). Students who cannot get any available slot move to the waitlist.
    print("\nStep 4b: scheduling interview times...")
    matches, unscheduled = schedule_interviews(matches, students)
    for w in waitlist:
        w.setdefault("reason", "not matched (no remaining capacity)")
    waitlist = waitlist + unscheduled
    waitlist.sort(key=lambda r: r["best_combined_score"], reverse=True)
    print(f"  scheduled {len(matches)}, could not schedule {len(unscheduled)}")

    # 5. Write outputs.
    print("\nStep 5: writing outputs...")
    _write_matches(matches)
    _write_waitlist(waitlist)
    _write_schedule(matches)
    print(f"  wrote {config.MATCHES_CSV}")
    print(f"  wrote {config.WAITLIST_CSV}")
    print(f"  wrote {config.SCHEDULE_CSV}")

    # Summary.
    _print_summary(
        students,
        companies,
        matches,
        waitlist,
        missing_resume,
        scores_df,
        students_loaded=students_loaded,
        excluded_grad=excluded_grad,
        excluded_sponsor=excluded_sponsor,
    )


if __name__ == "__main__":
    main()
