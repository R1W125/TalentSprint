"""
Load and clean the student and company CSVs into tidy DataFrames.

Key decisions documented here:
  - The single join key for the whole pipeline is BU Email. We lowercase and
    strip it and join resumes on it. We never join on the auto-collected
    Google "Email Address" column.
  - The companies CSV in this dataset has no Timestamp column and its first
    column header is mislabeled (it shows a company name instead of a generic
    "Company Name" label). We therefore force the first column name to a known
    internal label by position, then map the rest by their exact headers.
  - Company name matching between the "Choices [...]" headers and the companies
    CSV is done on a normalized form so small label differences still match
    (for example "PJMF" vs "PJMF (Patrick J. McGovern Foundation)"). The text
    before any parenthesis is used, lowercased and stripped.
"""

import re

import pandas as pd

import config
from parse_resumes import normalize_email


def _norm_company_key(name):
    """
    Normalize a company name for matching across the two CSVs.

    We lowercase, strip, drop anything in parentheses, and collapse spaces.
    This lets "PJMF" match "PJMF (Patrick J. McGovern Foundation)".
    """
    if name is None:
        return ""
    text = str(name)
    text = re.sub(r"\(.*?\)", "", text)  # drop parenthetical detail
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _parse_grad_year(text):
    """
    Pull a 4 digit graduation year out of a free text graduation date.

    Handles plain years ("2027") and richer formats ("May 2027", "05/2027",
    "2027-05-15"). Returns the year as an int, or None if no year is found.
    """
    m = re.search(r"(?:19|20)\d{2}", str(text))
    return int(m.group()) if m else None


def _dedupe_keep_latest(raw, key_col, key_normalizer=None, timestamp_col=None, label="rows"):
    """
    Collapse rows that share the same key, keeping the latest submission.

    If timestamp_col is present, "latest" is the most recent parseable timestamp.
    Rows with no timestamp column (or an unparseable timestamp) fall back to file
    order, where a later row is treated as the more recent submission. Blank keys
    are never merged (each is treated as unique). Prints a note listing the keys
    that had duplicates. Returns the deduplicated DataFrame in original order.
    """
    if key_col not in raw.columns or len(raw) == 0:
        return raw

    work = raw.copy()

    # Normalized grouping key. Blank keys get a unique placeholder so distinct
    # blank rows are not collapsed into one.
    norm = work[key_col].astype(str)
    if key_normalizer:
        norm = norm.apply(key_normalizer)
    keys = [
        k if str(k).strip() else f"__blank_{i}"
        for i, k in zip(range(len(norm)), norm)
    ]
    work["_dedup_key"] = keys

    # Order rows so the row to KEEP is last for each key.
    if timestamp_col and timestamp_col in work.columns:
        order = pd.to_datetime(work[timestamp_col], errors="coerce")
        work["_dedup_order"] = order
        # Stable sort by timestamp ascending; unparseable timestamps sort first
        # so a row with a real timestamp is preferred. File order breaks ties.
        work = work.sort_values("_dedup_order", kind="stable", na_position="first")

    # Report which real keys had duplicates (ignore the blank placeholders).
    real = work[~work["_dedup_key"].astype(str).str.startswith("__blank_")]
    dup_keys = sorted(set(real["_dedup_key"][real["_dedup_key"].duplicated(keep=False)]))

    deduped = work.drop_duplicates(subset="_dedup_key", keep="last")
    # Restore original file order and drop the helper columns.
    helper_cols = [c for c in ["_dedup_key", "_dedup_order"] if c in deduped.columns]
    deduped = deduped.sort_index().drop(columns=helper_cols).reset_index(drop=True)

    if dup_keys:
        print(
            f"NOTE: collapsed duplicate {label} (kept latest submission) for: "
            f"{', '.join(dup_keys)}"
        )
    return deduped


def slot_minutes(slot):
    """
    Sort key for a time slot like "3:15 pm": minutes since midnight.

    Returns a large number for anything unparseable so it sorts last.
    """
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap]m)\s*$", str(slot).lower())
    if not m:
        return 10**9
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


def _parse_availability(text):
    """
    Parse the comma separated availability cell into an ordered list of
    normalized time slots (earliest first). Unparseable tokens are dropped.
    """
    slots = []
    for token in str(text).split(","):
        token = token.strip().lower()
        if not token:
            continue
        if re.match(r"^\d{1,2}:\d{2}\s*[ap]m$", token):
            # Normalize internal spacing, for example "3:15  pm" -> "3:15 pm".
            token = re.sub(r"\s+", " ", token)
            if token not in slots:
                slots.append(token)
    slots.sort(key=slot_minutes)
    return slots


def _to_bool_authorized(value):
    """"Yes" means authorized (no sponsorship needed). Anything else is treated
    as needing sponsorship only when it is an explicit "No"; blanks are treated
    as authorized to avoid wrongly hard-filtering a student on missing data."""
    return str(value).strip().lower() == "no"


def _to_bool_sponsors(value):
    """A company sponsors only when it explicitly answers "Yes"."""
    return str(value).strip().lower() == "yes"


def _parse_choices(row, choices_headers):
    """
    Parse the "Choices [Company]" cells for one student row into a
    {company_name: rank} dict, including only companies they actually ranked.
    """
    ranks = {}
    for header, company_name in choices_headers.items():
        raw = row.get(header, "")
        if raw is None:
            continue
        text = str(raw).strip()
        if text == "" or text.lower() == "nan":
            continue
        # Ranks may arrive as "1", "1.0", etc. Pull the first integer.
        m = re.search(r"\d+", text)
        if not m:
            continue
        ranks[company_name] = int(m.group())
    return ranks


def load_companies(path=config.COMPANIES_CSV, doc_texts=None):
    """
    Load the companies CSV into a clean DataFrame.

    doc_texts, when provided, is the dict from parse_company_docs (normalized
    company key -> job description text). Each company's jd_text is taken from
    its matching document; if a company has no document, it falls back to the
    CSV column (now usually empty) and is collected in a printed warning.
    """
    raw = pd.read_csv(path, dtype=str).fillna("")

    # Force the first column (mislabeled in the export) to a known label.
    first_col = raw.columns[0]
    if first_col != config.COMPANY_NAME_INTERNAL:
        raw = raw.rename(columns={first_col: config.COMPANY_NAME_INTERNAL})

    # Collapse duplicate company submissions, keeping the latest. The companies
    # export has no Timestamp column, so file order stands in for recency.
    raw = _dedupe_keep_latest(
        raw,
        key_col=config.COMPANY_NAME_INTERNAL,
        key_normalizer=_norm_company_key,
        timestamp_col=None,
        label="companies",
    )

    c = config.COMPANY_COLS

    def col(internal):
        """Return the source column as a stripped string Series, or blanks."""
        header = c[internal]
        if header in raw.columns:
            return raw[header].astype(str).str.strip()
        return pd.Series([""] * len(raw), index=raw.index)

    companies = pd.DataFrame(
        {
            "name": col("name"),
            "contact": col("contact"),
            "work_types": col("work_types"),
            "roles": col("roles"),
            "skills": col("skills"),
            "experience_level": col("experience_level"),
            "jd_text": col("jd_text"),
            "ideal_candidate": col("ideal_candidate"),
            "sponsors": col("sponsors").apply(_to_bool_sponsors),
        }
    )

    # Drop any fully blank company-name rows defensively.
    companies = companies[companies["name"].str.strip() != ""].reset_index(drop=True)
    companies["name_key"] = companies["name"].apply(_norm_company_key)

    # Attach job descriptions from the parsed company documents, keyed by the
    # normalized company name. Fall back to the CSV column when no doc matched.
    if doc_texts is not None:
        missing_docs = []
        jd = []
        for _, comp in companies.iterrows():
            text = doc_texts.get(comp["name_key"], "")
            if not text:
                text = comp["jd_text"]  # CSV fallback (usually empty now)
                if not str(text).strip():
                    missing_docs.append(comp["name"])
            jd.append(text)
        companies["jd_text"] = jd
        if missing_docs:
            print(
                "WARNING: no job description document found for: "
                + ", ".join(missing_docs)
            )

    return companies


def load_students(path=config.STUDENTS_CSV, resume_texts=None):
    """
    Load the students CSV into a clean DataFrame and attach resume text.

    resume_texts is the dict returned by parse_resumes (normalized email ->
    text). Students with no matching resume get an empty string and are
    collected in the returned missing_resume list.
    """
    resume_texts = resume_texts or {}
    raw = pd.read_csv(path, dtype=str).fillna("")
    s = config.STUDENT_COLS

    # Collapse duplicate submissions by the same student (same BU Email), keeping
    # the most recent one by Timestamp. This handles a student filling the form
    # out more than once (for example to correct an answer).
    raw = _dedupe_keep_latest(
        raw,
        key_col=s["bu_email"],
        key_normalizer=normalize_email,
        timestamp_col=s.get("timestamp", "Timestamp"),
        label="student submissions",
    )

    def col(internal):
        header = s[internal]
        if header in raw.columns:
            return raw[header].astype(str).str.strip()
        return pd.Series([""] * len(raw), index=raw.index)

    emails = col("bu_email").apply(normalize_email)

    # Identify the per-company preference (Choices) columns and the company
    # name embedded in each header.
    choices_headers = {}
    for header in raw.columns:
        if header.startswith(config.CHOICES_PREFIX) and header.endswith(
            config.CHOICES_SUFFIX
        ):
            company = header[
                len(config.CHOICES_PREFIX) : -len(config.CHOICES_SUFFIX)
            ].strip()
            choices_headers[header] = company

    preferences = []
    resumes_attached = []
    missing_resume = []
    for idx in raw.index:
        row = raw.loc[idx]
        preferences.append(_parse_choices(row, choices_headers))
        email = emails.loc[idx]
        text = resume_texts.get(email, "")
        resumes_attached.append(text)
        if not text:
            missing_resume.append(email)

    students = pd.DataFrame(
        {
            "name": col("name"),
            "email": emails,  # BU Email, the unique id and join key
            "needs_sponsorship": col("authorized").apply(_to_bool_authorized),
            "work_type": col("work_type"),
            "years_experience": col("years_experience"),
            "environment": col("environment"),
            "project_text": col("project_text"),
            "next_role_text": col("next_role_text"),
            "resume_text": resumes_attached,
            "preferences": preferences,
            "graduation_date": col("graduation_date"),
            "graduation_year": col("graduation_date").apply(_parse_grad_year),
            "availability": col("availability").apply(_parse_availability),
        }
    )

    students = students[students["email"].str.strip() != ""].reset_index(drop=True)

    # The set of company names that appear in the Choices headers, for validation.
    choice_company_names = sorted(set(choices_headers.values()))
    return students, missing_resume, choice_company_names


def filter_by_graduation(students):
    """
    Drop students whose graduation year is not in config.ALLOWED_GRADUATION_YEARS.

    An empty allowlist disables the filter and keeps everyone (opt in, like the
    priority feature). When the filter is on, students with a missing or
    unreadable graduation date are also dropped, since we cannot confirm they
    are eligible.

    Returns (kept_students, excluded) where excluded is a list of dicts with
    keys: email, graduation_date, reason.
    """
    allowed = set(config.ALLOWED_GRADUATION_YEARS)
    if not allowed:
        return students.reset_index(drop=True), []

    keep_flags = []
    excluded = []
    for _, student in students.iterrows():
        year = student["graduation_year"]
        # pandas may store the year column as float (a None promotes ints to
        # floats), so detect missing with isna and compare as an int.
        if pd.isna(year):
            keep_flags.append(False)
            reason = "missing or unreadable graduation date"
        elif int(year) in allowed:
            keep_flags.append(True)
            continue
        else:
            keep_flags.append(False)
            reason = f"graduation year {int(year)} not in allowed list"
        excluded.append(
            {
                "email": student["email"],
                "graduation_date": student["graduation_date"],
                "reason": reason,
            }
        )

    mask = pd.Series(keep_flags, index=students.index)
    kept = students[mask].reset_index(drop=True)
    return kept, excluded


def filter_by_sponsorship(students):
    """
    Drop students who need sponsorship (answered "No" to work authorization).

    Controlled by config.EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP. When that flag is
    off, this is a no op and everyone is kept (the per-pair hard filter in
    match.py then handles sponsorship at match time instead). When on, these
    students are removed before scoring so they never reach the AI or the match.

    Returns (kept_students, excluded) where excluded is a list of dicts with
    keys: email, reason.
    """
    if not config.EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP:
        return students.reset_index(drop=True), []

    keep_flags = []
    excluded = []
    for _, student in students.iterrows():
        if bool(student["needs_sponsorship"]):
            keep_flags.append(False)
            excluded.append(
                {
                    "email": student["email"],
                    "reason": "needs sponsorship (answered No to work authorization)",
                }
            )
        else:
            keep_flags.append(True)

    mask = pd.Series(keep_flags, index=students.index)
    kept = students[mask].reset_index(drop=True)
    return kept, excluded


def preference_score(student, company_name):
    """
    Return preference points for a student/company pair.

    Ranked companies use the rank map; unranked companies return the unranked
    default. Company names are compared on the normalized key so label
    differences between the two CSVs do not break preference lookups.
    """
    prefs = student["preferences"] if isinstance(student, dict) else student.preferences
    target_key = _norm_company_key(company_name)
    for ranked_name, rank in prefs.items():
        if _norm_company_key(ranked_name) == target_key:
            return config.PREFERENCE_SCORE_MAP.get(
                rank, config.UNRANKED_PREFERENCE_SCORE
            )
    return config.UNRANKED_PREFERENCE_SCORE


def preference_rank(student, company_name):
    """Return the integer rank a student gave a company, or None if unranked."""
    prefs = student["preferences"] if isinstance(student, dict) else student.preferences
    target_key = _norm_company_key(company_name)
    for ranked_name, rank in prefs.items():
        if _norm_company_key(ranked_name) == target_key:
            return rank
    return None


def validate_and_report(students, companies, missing_resume, choice_company_names):
    """Print load counts and any company-name mismatches between the CSVs."""
    print("=" * 70)
    print("DATA LOAD VALIDATION")
    print("=" * 70)
    print(f"Students loaded:  {len(students)}")
    print(f"Companies loaded: {len(companies)}")

    company_keys = set(companies["name_key"])
    choice_keys = {_norm_company_key(n): n for n in choice_company_names}

    # Choices headers that have no matching company in the companies CSV.
    in_choices_not_companies = [
        original
        for key, original in choice_keys.items()
        if key not in company_keys
    ]
    # Companies in the companies CSV that no student could rank (no Choices col).
    in_companies_not_choices = [
        name
        for name, key in zip(companies["name"], companies["name_key"])
        if key not in choice_keys
    ]

    if in_choices_not_companies:
        print("\nWARNING: Choices columns with no matching company in companies.csv:")
        for n in in_choices_not_companies:
            print(f"  - {n}")
    else:
        print("\nAll Choices columns matched a company in companies.csv.")

    if in_companies_not_choices:
        print("\nWARNING: companies with no Choices column (students cannot rank them):")
        for n in in_companies_not_choices:
            print(f"  - {n}")
    else:
        print("All companies have a matching Choices column.")

    if missing_resume:
        print(f"\nStudents missing a resume ({len(missing_resume)}):")
        for e in missing_resume:
            print(f"  - {e}")
    else:
        print("\nAll students have a matching resume.")
    print("=" * 70)


if __name__ == "__main__":
    from parse_resumes import parse_resumes

    texts, _ = parse_resumes()
    studs, missing, choice_names = load_students(resume_texts=texts)
    comps = load_companies()
    validate_and_report(studs, comps, missing, choice_names)
