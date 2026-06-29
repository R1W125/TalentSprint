"""
Interview time-slot scheduling.

This runs AFTER matching. Matching assigns each student to exactly one company,
so every student has exactly one interview and can never have a cross-company
time conflict. The only conflict is within a company: two of its matched
students wanting the same 15 minute slot. A company runs one interview per slot,
so the number of distinct time slots is the real ceiling on its interviews.

Rule (greedy by score): for each company, assign slots highest fit score first.
Each student takes the earliest slot they marked available that is not already
taken at that company. A student who cannot get any available slot is left
unscheduled and moved to the waitlist by main.py.
"""

from load_data import slot_minutes


def _num(value):
    """Coerce a score to float for sorting; non numeric becomes 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def all_time_slots(students):
    """The universe of time slots, as the union of every student's availability,
    ordered earliest first."""
    slots = set()
    for _, student in students.iterrows():
        slots.update(student["availability"])
    return sorted(slots, key=slot_minutes)


def schedule_interviews(matches, students):
    """
    Assign each matched student an interview time slot at their company.

    Greedy by fit score: within a company, higher scored students pick first and
    take the earliest open slot they are available for. Ties break by combined
    score, then email, for determinism.

    Returns:
      scheduled: list of match dicts, each with an added "time_slot"
      unscheduled: list of waitlist dicts for students who got no slot, with keys
        student_name, student_email, best_company, best_combined_score,
        best_fit_score, reason
    """
    availability = {s["email"]: list(s["availability"]) for _, s in students.iterrows()}

    # Group matched students by company.
    by_company = {}
    for m in matches:
        by_company.setdefault(m["matched_company"], []).append(m)

    scheduled = []
    unscheduled = []
    for company in by_company:
        ranked = sorted(
            by_company[company],
            key=lambda m: (-_num(m["fit_score"]), -_num(m["combined_score"]), m["student_email"]),
        )
        taken = set()  # slots already used at this company
        for m in ranked:
            email = m["student_email"]
            chosen = None
            for slot in availability.get(email, []):  # already earliest first
                if slot not in taken:
                    chosen = slot
                    break
            if chosen is None:
                unscheduled.append(
                    {
                        "student_name": m["student_name"],
                        "student_email": email,
                        "best_company": company,
                        "best_combined_score": m["combined_score"],
                        "best_fit_score": m["fit_score"],
                        "reason": "no available interview slot",
                    }
                )
            else:
                taken.add(chosen)
                row = dict(m)
                row["time_slot"] = chosen
                scheduled.append(row)

    return scheduled, unscheduled
