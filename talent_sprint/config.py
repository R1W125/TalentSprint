"""
Central configuration for the Talent Sprint matching pipeline.

Everything tunable lives here. If a CSV header changes, edit the column
mappings below and nothing else needs to change. Note the deliberate style
choice across this project: no em dashes anywhere in comments or output.
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# All paths are resolved relative to this file so the pipeline runs the same
# regardless of the current working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
RESUMES_DIR = os.path.join(DATA_DIR, "resumes")

STUDENTS_CSV = os.path.join(DATA_DIR, "students.csv")
COMPANIES_CSV = os.path.join(DATA_DIR, "companies.csv")

SCORES_CSV = os.path.join(OUTPUT_DIR, "scores.csv")
MATCHES_CSV = os.path.join(OUTPUT_DIR, "matches.csv")
WAITLIST_CSV = os.path.join(OUTPUT_DIR, "waitlist.csv")

ENV_PATH = os.path.join(BASE_DIR, ".env")

# ---------------------------------------------------------------------------
# Matching weights and capacity
# ---------------------------------------------------------------------------
FIT_WEIGHT = 0.6
PREFERENCE_WEIGHT = 0.4
SLOTS_PER_COMPANY = 2

# ---------------------------------------------------------------------------
# Graduation eligibility filter
# ---------------------------------------------------------------------------
# The event is for juniors and seniors only. List the graduation YEARS that are
# allowed; any student whose graduation year is not in this list is dropped
# before scoring and matching (so we do not even spend API calls on them).
# Leave the list EMPTY to disable the filter and include everyone.
# The year is read from the "Graduation Date" column, and we pull the 4 digit
# year out of whatever format it is in (so "2027", "May 2027", "05/2027" all
# read as 2027). Students with a missing or unreadable graduation date are
# excluded when the filter is on, and are reported so you can chase them down.
# Example for a 2026 event (juniors and seniors graduate 2026 to 2028):
#   ALLOWED_GRADUATION_YEARS = [2026, 2027, 2028]
ALLOWED_GRADUATION_YEARS = [2026, 2027,2028]

# ---------------------------------------------------------------------------
# Work authorization eligibility
# ---------------------------------------------------------------------------
# When True, students who answered "No" to the work authorization question (so
# they would need sponsorship) are treated as ineligible for the event and are
# dropped BEFORE scoring. They are never sent to the AI and never matched.
# When False, those students are kept and instead only blocked from
# non-sponsoring companies at match time (the older per-pair hard filter).
EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP = True

# Optional priority. Companies listed here match FIRST, in their own round,
# against the full student pool. Every other company then matches in a second
# round against only the students still unmatched. This is opt in: leave the
# list empty for the default behavior where all companies are equal and match
# in a single round. Names are matched the same forgiving way as elsewhere, so
# "PJMF" still finds "PJMF (Patrick J. McGovern Foundation)". A name that
# matches no loaded company is ignored with a warning.
# Example: PRIORITY_COMPANIES = ["Bloomberg", "IBM"]
PRIORITY_COMPANIES = []

# Preference score map: a student's rank for a company maps to these points.
# Any company a student did not rank scores UNRANKED_PREFERENCE_SCORE.
PREFERENCE_SCORE_MAP = {
    1: 100,
    2: 90,
    3: 82,
    4: 74,
    5: 65,
    6: 60, 
}
UNRANKED_PREFERENCE_SCORE = 10

# ---------------------------------------------------------------------------
# Scoring criteria weights (used only inside the Gemini prompt text)
# ---------------------------------------------------------------------------
# These describe how Gemini should weight each criterion. They are documented
# as percentages in the prompt; the matching code never re-applies them.
CRITERIA_WEIGHTS = {
    "technical_skills": 30,
    "experience_level": 40,
    "project_relevance": 10,
    "soft_skills_environment": 20,
}

# ---------------------------------------------------------------------------
# Gemini SDK note
# ---------------------------------------------------------------------------
# We use the current official SDK, the "google-genai" package (imported as
# "from google import genai"). The older "google-generativeai" package is
# deprecated, so we do not use it. The model id and API key come from .env.
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
MAX_SCORING_RETRIES = 2  # retries per company after the first attempt (parse errors)

# Rate limiting. The Gemini free tier allows only about 15 requests per minute.
# We can make several requests per company (one per student batch), so every API
# call is paced centrally to stay under that limit, with a back off when the API
# reports a 429 quota error. On a paid tier you can safely lower
# REQUEST_INTERVAL_SECONDS to 0 to run faster.
REQUEST_INTERVAL_SECONDS = 5  # wait between API calls (5s gives ~12 req/min)
RATE_LIMIT_MAX_RETRIES = 6  # extra retries reserved for 429 quota errors
RATE_LIMIT_BACKOFF_SECONDS = 35  # fallback wait if the API gives no retry delay

# Students per scoring call. We score one company at a time, but split its
# students into batches of this size so a single prompt never grows too large.
# Large prompts (for example 200 full resumes at once) risk hitting the context
# limit, dilute scoring quality, and make one bad response retry everyone. With
# a few hundred students, batches of about 30 keep each prompt focused. Set to 0
# to disable batching and send all students in one call.
STUDENTS_PER_SCORING_BATCH = 30

# ---------------------------------------------------------------------------
# Column name mappings
# ---------------------------------------------------------------------------
# Map our internal field names to the exact header strings in the source CSVs.
# If a form question is reworded, only these dictionaries need editing.

# Students CSV. The join key for the whole pipeline is BU_EMAIL, not the
# auto-collected Google "Email Address" column.
STUDENT_COLS = {
    "timestamp": "Timestamp",  # used to keep the latest of duplicate submissions
    "auto_email": "Email Address",  # auto-collected by Google, NOT the join key
    "name": "Full Name",
    "bu_email": "BU Email",  # the join key and unique student id
    "graduation_date": "Graduation Date",  # used by the graduation eligibility filter
    "authorized": (
        "Are you authorized to work in the US? "
        "(Important but need to check if we can ask this)"
    ),
    "work_type": "Preferred work type",
    # Note the source header has the typo "relevent"; we mirror it exactly.
    "years_experience": "Years of relevent experience",
    "environment": "What type of work environment do you prefer?",
    "project_text": (
        "Brief description of your strongest project or work experience "
        "(short paragraph)"
    ),
    "next_role_text": (
        "What are you looking for in your next role or internship? "
        "(short paragraph)"
    ),
    "resume_link": "Resume",  # a link string, ignored; real PDFs live in resumes/
}

# Prefix of the per-company preference columns, e.g. "Choices [IBM]".
CHOICES_PREFIX = "Choices ["
CHOICES_SUFFIX = "]"

# Companies CSV. Important: the real export has NO Timestamp column and its
# first column header is mislabeled (it shows a company name instead of
# "Company Name"). load_data renames the first column to COMPANY_NAME_INTERNAL
# by position, so the mapping below uses that internal name for the company
# name and the exact header strings for everything else.
COMPANY_NAME_INTERNAL = "Company Name"
COMPANY_COLS = {
    "name": COMPANY_NAME_INTERNAL,  # forced onto column 0 by load_data
    "contact": "Full Name",
    "email": "Email Address",
    "title": "Title",
    # "Number of Representatives Attending" is intentionally NOT used for slots.
    "work_types": "Are you primarily recruiting for: (Select all that apply)",
    "roles": "What roles are you hiring for (and how many)?",
    "skills": (
        "To facilitate candidate matching, please check your primary skill "
        "requirements: (Select all that apply)"
    ),
    "participation": "How would you like to participate? ",
    "experience_level": "Preferred Level of Experience ",
    "jd_text": "Do you have any supporting documents to upload?",  # holds JD text
    "sponsors": "Do you sponsor work authorization?",
    "ideal_candidate": (
        "Any Additional Information– soft skills, work style preference, "
        "additional notes (what does your ideal candidate look like?)"
    ),
    # feedback column is ignored on purpose.
}
