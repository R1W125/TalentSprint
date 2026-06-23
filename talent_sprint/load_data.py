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


def load_companies(path=config.COMPANIES_CSV):
    """Load the companies CSV into a clean DataFrame."""
    raw = pd.read_csv(path, dtype=str).fillna("")

    # Force the first column (mislabeled in the export) to a known label.
    first_col = raw.columns[0]
    if first_col != config.COMPANY_NAME_INTERNAL:
        raw = raw.rename(columns={first_col: config.COMPANY_NAME_INTERNAL})

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
        }
    )

    students = students[students["email"].str.strip() != ""].reset_index(drop=True)

    # The set of company names that appear in the Choices headers, for validation.
    choice_company_names = sorted(set(choices_headers.values()))
    return students, missing_resume, choice_company_names


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
