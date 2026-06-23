"""
Company-proposing Gale-Shapley matching with a capacity of 10 students per
company.

Both sides rank by the same combined score, so the result is a stable
assignment: no company-student pair would both prefer each other over their
current assignment.

  combined = FIT_WEIGHT * fit_score + PREFERENCE_WEIGHT * preference_score

Companies propose to their highest combined-score students first. Each student
holds at most one offer at a time, keeping whichever proposing company gives
the higher combined score. Companies that get rejected move down their list.
The process ends when every company has either filled its 10 slots or run out
of students to propose to.
"""

import config
from load_data import preference_score, preference_rank, _norm_company_key


def build_combined_scores(students, companies, scores_df):
    """
    Build lookup tables keyed by (student_email, company_name):
      combined[(e, c)] -> combined score (float)
      fit[(e, c)]      -> fit score (int)
      forbidden        -> set of (e, c) pairs the matcher must never assign

    A pair is forbidden when the student needs sponsorship and the company does
    not sponsor. This is the true, enforced form of the work authorization hard
    filter: zeroing the fit score alone is not enough, because the preference
    component could still drag a forbidden pair into a match when capacity is
    tight. The matcher excludes these pairs entirely.
    """
    # Fast lookup of fit score from the scores DataFrame.
    fit = {}
    for _, row in scores_df.iterrows():
        fit[(str(row["student_email"]).strip().lower(), str(row["company_name"]))] = int(
            float(row["fit_score"])
        )

    combined = {}
    forbidden = set()
    for _, student in students.iterrows():
        email = student["email"]
        needs_sponsorship = bool(student["needs_sponsorship"])
        for _, company in companies.iterrows():
            cname = company["name"]
            fit_score = fit.get((email, cname), 0)
            pref = preference_score(student, cname)
            combined[(email, cname)] = (
                config.FIT_WEIGHT * fit_score + config.PREFERENCE_WEIGHT * pref
            )
            if needs_sponsorship and not bool(company["sponsors"]):
                forbidden.add((email, cname))
    return combined, fit, forbidden


def gale_shapley(students, companies, combined, forbidden=None):
    """
    Run company-proposing Gale-Shapley with capacity SLOTS_PER_COMPANY.

    Pairs in the forbidden set (work authorization hard filter) are left out of
    every company's preference list, so they can never be assigned.

    Returns:
      assignment: dict student_email -> company_name (matched students only)
    """
    forbidden = forbidden or set()
    cap = config.SLOTS_PER_COMPANY
    student_emails = list(students["email"])
    company_names = list(companies["name"])

    # Each company's preference list: eligible students sorted by combined desc.
    # Forbidden pairs are excluded. Ties are broken by email for determinism.
    pref_lists = {}
    for cname in company_names:
        eligible = [e for e in student_emails if (e, cname) not in forbidden]
        ordered = sorted(
            eligible,
            key=lambda e, c=cname: (-combined[(e, c)], e),
        )
        pref_lists[cname] = ordered

    next_index = {c: 0 for c in company_names}
    holds = {c: set() for c in company_names}  # company -> set of held emails
    student_offer = {}  # email -> (company_name, combined_score)

    def company_can_propose(c):
        return len(holds[c]) < cap and next_index[c] < len(pref_lists[c])

    # Continue while any company still has an open slot and someone to ask.
    while True:
        proposer = next((c for c in company_names if company_can_propose(c)), None)
        if proposer is None:
            break

        target = pref_lists[proposer][next_index[proposer]]
        next_index[proposer] += 1
        offer_score = combined[(target, proposer)]

        current = student_offer.get(target)
        if current is None:
            # Student is free: tentatively accept.
            student_offer[target] = (proposer, offer_score)
            holds[proposer].add(target)
        else:
            cur_company, cur_score = current
            # Student keeps the higher combined score offer.
            if offer_score > cur_score:
                holds[cur_company].discard(target)
                student_offer[target] = (proposer, offer_score)
                holds[proposer].add(target)
            # else: student rejects the new offer, nothing changes.

    assignment = {email: comp for email, (comp, _) in student_offer.items()}
    return assignment


def run_match(students, companies, scores_df):
    """
    Full matching step.

    Returns:
      matches: list of dicts (one per matched student) with keys
        student_name, student_email, matched_company, fit_score,
        preference_rank, combined_score, reasoning
      waitlist: list of dicts (one per unmatched student) with keys
        student_name, student_email, best_company, best_combined_score,
        best_fit_score
      combined, fit: the lookup tables (used by the summary report)
    """
    combined, fit, forbidden = build_combined_scores(students, companies, scores_df)
    assignment = gale_shapley(students, companies, combined, forbidden)

    # Reasoning lookup from the scores DataFrame.
    reason = {}
    for _, row in scores_df.iterrows():
        reason[
            (str(row["student_email"]).strip().lower(), str(row["company_name"]))
        ] = str(row.get("reasoning", ""))

    matches = []
    matched_emails = set()
    for _, student in students.iterrows():
        email = student["email"]
        if email not in assignment:
            continue
        cname = assignment[email]
        rank = preference_rank(student, cname)
        matches.append(
            {
                "student_name": student["name"],
                "student_email": email,
                "matched_company": cname,
                "fit_score": fit.get((email, cname), 0),
                "preference_rank": rank if rank is not None else "unranked",
                "combined_score": round(combined[(email, cname)], 2),
                "reasoning": reason.get((email, cname), ""),
            }
        )
        matched_emails.add(email)

    # Waitlist: unmatched students, best eligible company first.
    waitlist = _build_waitlist(students, companies, combined, fit, matched_emails, forbidden)
    return matches, waitlist, combined, fit


def _build_waitlist(students, companies, combined, fit, matched_emails, forbidden=None):
    """
    Build the waitlist for students not in matched_emails, ranked by their best
    combined score across the eligible companies (strongest near-misses first).

    Forbidden pairs (work authorization hard filter) are skipped, so the listed
    best company is always one the student could actually have been matched to.
    A student for whom every company is forbidden has no eligible company.
    """
    forbidden = forbidden or set()
    company_names = list(companies["name"])
    waitlist = []
    for _, student in students.iterrows():
        email = student["email"]
        if email in matched_emails:
            continue
        best_company, best_combined = None, -1.0
        for cname in company_names:
            if (email, cname) in forbidden:
                continue
            val = combined[(email, cname)]
            if val > best_combined:
                best_combined, best_company = val, cname
        waitlist.append(
            {
                "student_name": student["name"],
                "student_email": email,
                "best_company": best_company if best_company is not None else "none eligible",
                "best_combined_score": round(best_combined, 2) if best_company is not None else 0.0,
                "best_fit_score": fit.get((email, best_company), 0) if best_company is not None else 0,
            }
        )
    waitlist.sort(key=lambda r: r["best_combined_score"], reverse=True)
    return waitlist


def run_priority_match(students, companies, scores_df):
    """
    Priority aware matching driver.

    Companies named in config.PRIORITY_COMPANIES match first, in their own
    round, against the full student pool. The remaining companies then match in
    a second round against only the students still unmatched. With an empty
    PRIORITY_COMPANIES list this reduces to a single normal matching round, so
    the default behavior is unchanged.

    Returns (matches, waitlist) with the same shapes as run_match.
    """
    priority_keys = {_norm_company_key(n) for n in config.PRIORITY_COMPANIES}

    # Warn about any priority name that does not match a loaded company.
    if priority_keys:
        loaded_keys = set(companies["name_key"])
        for name in config.PRIORITY_COMPANIES:
            if _norm_company_key(name) not in loaded_keys:
                print(
                    f"WARNING: priority company '{name}' did not match any loaded "
                    "company and will be ignored."
                )

    is_priority = companies["name_key"].isin(priority_keys)
    priority_companies = companies[is_priority].reset_index(drop=True)
    rest_companies = companies[~is_priority].reset_index(drop=True)

    # No (valid) priority configured: behave exactly like a single round.
    if len(priority_companies) == 0:
        matches, waitlist, _, _ = run_match(students, companies, scores_df)
        return matches, waitlist

    print(f"  priority round 1 companies: {list(priority_companies['name'])}")
    print(f"  round 2 companies:          {list(rest_companies['name'])}")

    # Round 1: priority companies match against everyone.
    r1_matches, _, _, _ = run_match(students, priority_companies, scores_df)
    r1_emails = {m["student_email"] for m in r1_matches}

    # Round 2: remaining companies match against the leftover students only.
    remaining = students[~students["email"].isin(r1_emails)].reset_index(drop=True)
    if len(rest_companies) > 0 and len(remaining) > 0:
        r2_matches, _, _, _ = run_match(remaining, rest_companies, scores_df)
    else:
        r2_matches = []
    r2_emails = {m["student_email"] for m in r2_matches}

    matches = r1_matches + r2_matches
    matched_all = r1_emails | r2_emails

    # Final waitlist ranks unmatched students by best combined across ALL companies.
    combined, fit, forbidden = build_combined_scores(students, companies, scores_df)
    waitlist = _build_waitlist(students, companies, combined, fit, matched_all, forbidden)
    return matches, waitlist
