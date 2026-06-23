"""
Gemini based fit scoring.

For each company we build ONE prompt that contains the company info plus ALL
students, and ask Gemini to return a JSON array with one score per student.
Students are numbered rather than named in the score output to reduce name bias.

After scoring we apply a work authorization HARD FILTER in code (never in the
prompt): a student who needs sponsorship gets a fit score of 0 for any company
that does not sponsor.

Results are cached to output/scores.csv. If that file already exists we load it
and skip all API calls, unless --rescore is passed.
"""

import json
import os
import re
import sys
import time

import pandas as pd

import config


# ---------------------------------------------------------------------------
# Gemini client setup. We use the current official SDK, the "google-genai"
# package, imported as "from google import genai". The older
# "google-generativeai" package is deprecated and intentionally not used.
# ---------------------------------------------------------------------------
def _load_env():
    """Load .env so GEMINI_API_KEY and GEMINI_MODEL are available."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        print("ERROR: python-dotenv is not installed. Run: pip install -r requirements.txt")
        raise
    load_dotenv(config.ENV_PATH)


def _get_client_and_model():
    """
    Build a Gemini client and return (client, model_name).

    Fails with a clear message if the key is missing. The model string is not
    validated here; an invalid model surfaces at call time and we translate
    that into a clear "check GEMINI_MODEL in .env" message.
    """
    _load_env()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    model_name = os.environ.get("GEMINI_MODEL", config.DEFAULT_GEMINI_MODEL).strip()

    if not api_key or api_key == "PASTE_YOUR_KEY_HERE":
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Open talent_sprint/.env and paste your key."
        )

    try:
        from google import genai  # current official SDK: google-genai
    except ImportError:
        raise RuntimeError(
            "google-genai is not installed. Run: pip install -r requirements.txt"
        )

    client = genai.Client(api_key=api_key)
    return client, model_name


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _build_prompt(company, students):
    """
    Build the scoring prompt for one company over a batch of students.

    Layout note: the instructions and the STUDENTS block come FIRST and the
    COMPANY block comes LAST. For a given batch of students that whole leading
    section is identical no matter which company we are scoring, so it forms a
    stable prefix. Putting the company specific text at the end lets Gemini's
    prefix caching reuse the (large) student section across all companies,
    instead of re-reading every resume once per company.
    """
    cw = config.CRITERIA_WEIGHTS

    header = f"""You are an expert technical recruiter scoring candidates for a university career event where each company conducts short interviews. Below is a list of students, followed by ONE company. Score each student from 0 to 100 on how well they fit THAT company's hiring needs.

## SCORING CRITERIA (weights)
- Technical skill alignment with the company's requirements: {cw['technical_skills']}%
- Experience level match: {cw['experience_level']}%
- Project and work relevance to the company's roles and domain: {cw['project_relevance']}%
- Work environment and soft skill alignment: {cw['soft_skills_environment']}%

## SCORING GUIDELINES
- 90 to 100: Exceptional fit, exceeds requirements
- 70 to 89: Strong fit, meets most requirements
- 50 to 69: Moderate fit, meets some requirements
- 30 to 49: Weak fit, significant gaps
- 0 to 29: Poor fit, fundamental mismatch
- Score on substance, not buzzwords. A concrete project with a measurable outcome outweighs a list of keywords or trendy terms with no evidence behind them.
- Do not reward vague claims of expertise. Do not penalize a student for information that is simply absent; score what is present.
- These are students, so weight demonstrated ability through projects more heavily than raw years of experience.

## STUDENTS
"""

    blocks = []
    for i, (_, stu) in enumerate(students.iterrows(), start=1):
        resume = stu["resume_text"] or "(no resume on file)"
        blocks.append(
            f"""[Student {i}: {stu['name']}]
Work type sought: {stu['work_type']}
Years of experience: {stu['years_experience']}
Preferred environment: {stu['environment']}
Strongest project (their words): {stu['project_text']}
Looking for (their words): {stu['next_role_text']}
Resume:
{resume}
"""
        )

    footer = f"""
## COMPANY
Name: {company['name']}
Recruiting for: {company['work_types']}
Roles: {company['roles']}
Required skills: {company['skills']}
Preferred experience level: {company['experience_level']}
Ideal candidate notes: {company['ideal_candidate']}
Job descriptions:
{company['jd_text']}

## OUTPUT FORMAT
Return ONLY a JSON array and nothing else. No markdown, no backticks, no preamble. Exactly one object per student, in order:
[
  {{"student": 1, "score": 78, "reasoning": "one short sentence"}},
  {{"student": 2, "score": 45, "reasoning": "one short sentence"}}
]
"""
    return header + "\n".join(blocks) + footer


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------
def _strip_fences(text):
    """Remove any markdown code fences around a JSON payload."""
    t = text.strip()
    if t.startswith("```"):
        # Drop the first fence line and any trailing fence.
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```$", "", t.strip())
    return t.strip()


def _clamp(score):
    """Clamp a numeric score to the 0 to 100 range as an int."""
    try:
        val = float(score)
    except (TypeError, ValueError):
        val = 0
    return int(max(0, min(100, round(val))))


def _parse_scores(text, expected_count):
    """
    Parse Gemini output into a list of (score, reasoning) of length
    expected_count. Raises ValueError if it cannot be parsed or the count is
    wrong, so the caller can retry.
    """
    cleaned = _strip_fences(text)
    # Be forgiving: grab the outermost JSON array if extra text slipped in.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("Parsed JSON is not a list.")
    if len(data) != expected_count:
        raise ValueError(
            f"Expected {expected_count} scores, got {len(data)}."
        )

    # Order by the "student" index when present so we are robust to reordering.
    def key(obj):
        try:
            return int(obj.get("student", 0))
        except (TypeError, ValueError):
            return 0

    if all(isinstance(o, dict) and "student" in o for o in data):
        data = sorted(data, key=key)

    results = []
    for obj in data:
        if not isinstance(obj, dict):
            raise ValueError("Score entry is not an object.")
        results.append((_clamp(obj.get("score", 0)), str(obj.get("reasoning", "")).strip()))
    return results


# ---------------------------------------------------------------------------
# Scoring driver
# ---------------------------------------------------------------------------
def _is_rate_limit(msg):
    """True if an error message looks like a 429 quota / rate limit error."""
    low = msg.lower()
    return "429" in msg or "resource_exhausted" in low or "rate limit" in low


def _parse_retry_delay(msg):
    """
    Pull the suggested wait (seconds) out of a 429 error message.

    The API returns things like "retryDelay': '33s'" or "retry in 34.27s".
    Returns a float number of seconds, or None if not found.
    """
    m = re.search(r"retry(?:delay)?['\"\s:]*([0-9]+(?:\.[0-9]+)?)\s*s", msg, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in\s*([0-9]+(?:\.[0-9]+)?)\s*s", msg, re.I)
    if m:
        return float(m.group(1))
    return None


# Timestamp of the most recent API call, used to pace requests so we stay under
# the free-tier rate limit even when one company is split into several calls.
_last_api_call_time = 0.0


def _respect_rate_limit():
    """Wait so consecutive API calls are at least REQUEST_INTERVAL_SECONDS apart."""
    global _last_api_call_time
    interval = config.REQUEST_INTERVAL_SECONDS
    if interval > 0:
        elapsed = time.time() - _last_api_call_time
        if elapsed < interval:
            time.sleep(interval - elapsed)
    _last_api_call_time = time.time()


def _score_one_call(client, model_name, company, students_chunk):
    """
    Make ONE Gemini call for a batch of students and return their list of
    (score, reasoning), already paced and retried.

    Parse failures use the normal retry budget. 429 quota errors get their own,
    larger retry budget and wait the delay the API suggests, so a free-tier
    rate limit pauses the run instead of killing it.
    """
    prompt = _build_prompt(company, students_chunk)
    last_error = None
    parse_attempts = 0
    rate_limit_attempts = 0

    while True:
        try:
            _respect_rate_limit()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            text = getattr(response, "text", None) or ""
            return _parse_scores(text, len(students_chunk))
        except Exception as exc:  # noqa: BLE001 - retry then fail loudly below
            last_error = exc
            msg = str(exc)
            low = msg.lower()

            # Translate an obviously bad model id into actionable guidance.
            if "model" in low and ("not found" in low or "invalid" in low or "404" in low):
                raise RuntimeError(
                    f"Gemini model '{model_name}' was rejected. "
                    f"Check GEMINI_MODEL in talent_sprint/.env. Original error: {exc}"
                )

            if _is_rate_limit(msg):
                rate_limit_attempts += 1
                if rate_limit_attempts > config.RATE_LIMIT_MAX_RETRIES:
                    break
                delay = _parse_retry_delay(msg) or config.RATE_LIMIT_BACKOFF_SECONDS
                delay += 1  # small cushion so we clear the window
                print(
                    f"  rate limited on {company['name']} "
                    f"(free tier is ~15 requests/min). Waiting {delay:.0f}s then retrying..."
                )
                time.sleep(delay)
                continue

            parse_attempts += 1
            print(f"  attempt {parse_attempts} failed for {company['name']}: {exc}")
            if parse_attempts > config.MAX_SCORING_RETRIES:
                break

    raise RuntimeError(
        f"Scoring failed for company '{company['name']}'. Last error: {last_error}"
    )


def _score_one_company(client, model_name, company, students):
    """
    Score every student for one company, splitting the students into batches of
    config.STUDENTS_PER_SCORING_BATCH so no single prompt grows too large.

    Returns one (score, reasoning) per student, in the original student order.
    """
    batch = config.STUDENTS_PER_SCORING_BATCH
    n = len(students)

    # No batching: one call for everyone (also the path for small cohorts).
    if not batch or batch <= 0 or n <= batch:
        return _score_one_call(client, model_name, company, students)

    results = []
    total_chunks = (n + batch - 1) // batch
    for chunk_no, start in enumerate(range(0, n, batch), start=1):
        chunk = students.iloc[start : start + batch]
        print(f"    batch {chunk_no}/{total_chunks} ({len(chunk)} students)...")
        results.extend(_score_one_call(client, model_name, company, chunk))
    return results


def _apply_hard_filter(student, company, fit_score, reasoning):
    """
    Work authorization HARD FILTER, applied in code only.

    If the student needs sponsorship and the company does not sponsor, force
    the fit score to 0 with a fixed reasoning string.
    """
    if student["needs_sponsorship"] and not company["sponsors"]:
        return 0, "Hard filter: requires sponsorship, company does not sponsor."
    return fit_score, reasoning


SCORE_COLUMNS = ["student_name", "student_email", "company_name", "fit_score", "reasoning"]


def score_all(students, companies, rescore=False):
    """
    Produce the full score matrix as a DataFrame with columns:
    student_name, student_email, company_name, fit_score, reasoning.

    Resumable and budget safe. The cache at output/scores.csv is written after
    every company, and on startup any company already present in the cache is
    skipped (so a re-run never repeats a paid API call). Pass rescore=True to
    start over from scratch.
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    # Load whatever is already cached so we can resume a partial run.
    records = []
    done_companies = set()
    if not rescore and os.path.exists(config.SCORES_CSV):
        cached = pd.read_csv(config.SCORES_CSV, dtype=str).fillna("")
        cached["fit_score"] = cached["fit_score"].apply(_clamp)
        records = cached.to_dict("records")
        done_companies = set(cached["company_name"])

    todo = [c for _, c in companies.iterrows() if c["name"] not in done_companies]

    if not todo:
        print(f"Using cached scores from {config.SCORES_CSV} (all companies present).")
        print("Pass --rescore to force a fresh run.")
        return pd.DataFrame(records, columns=SCORE_COLUMNS)

    if done_companies:
        print(
            f"Resuming: {len(done_companies)} companies already cached, "
            f"{len(todo)} still to score."
        )

    print("Scoring with Gemini (batched per company; requests are paced)...")
    client, model_name = _get_client_and_model()
    print(f"Using model: {model_name}")

    for i, company in enumerate(todo):
        # Pacing between every API call is handled centrally in _respect_rate_limit.
        print(f"  scoring {len(students)} students for {company['name']}...")
        raw_scores = _score_one_company(client, model_name, company, students)
        for (_, student), (fit_score, reasoning) in zip(students.iterrows(), raw_scores):
            fit_score, reasoning = _apply_hard_filter(
                student, company, fit_score, reasoning
            )
            records.append(
                {
                    "student_name": student["name"],
                    "student_email": student["email"],
                    "company_name": company["name"],
                    "fit_score": fit_score,
                    "reasoning": reasoning,
                }
            )

        # Save progress after every company so a later failure loses nothing.
        pd.DataFrame(records, columns=SCORE_COLUMNS).to_csv(config.SCORES_CSV, index=False)
        print(f"    saved progress ({len(done_companies) + i + 1}/{len(companies)} companies cached)")

    print(f"Wrote cached scores to {config.SCORES_CSV}")
    return pd.DataFrame(records, columns=SCORE_COLUMNS)


if __name__ == "__main__":
    from parse_resumes import parse_resumes
    from load_data import load_students, load_companies

    rescore_flag = "--rescore" in sys.argv
    texts, _ = parse_resumes()
    studs, _, _ = load_students(resume_texts=texts)
    comps = load_companies()
    df = score_all(studs, comps, rescore=rescore_flag)
    print(df.head(20).to_string(index=False))
