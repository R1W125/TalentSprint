# Talent Sprint Matching Pipeline: How It Works

This document explains every file in the project, what it does, and how the
files work together. It is written to be read top to bottom. Read the "Big
picture" section first, then the per-file sections.

---

## 1. Big picture: what the program does

The Talent Sprint is like a career fair, except every student is assigned to
exactly one company for a 10 minute interview, and every company gets up to a
fixed number of students (10 by default). The program decides who goes where.

It does this in five steps, run in order by `main.py`:

```
   resume files + company docs + 2 CSV files
          |
   (1) parse_resumes.py / parse_company_docs.py
          |             read each resume and company document into plain text
          |             (any format: pdf, docx, doc, rtf, pages, txt)
          |
   (2) load_data.py       turn the CSVs into clean tables, attach resumes and
          |                job descriptions, filter ineligible students
          |
   (3) score.py           ask Gemini AI to rate each student 0-100 per company
          |                then apply the work authorization rule in code
          |
   (4) match.py           combine AI scores with student preferences, run the
          |                matching algorithm, then schedule interview times
          |
   (5) main.py            write matches.csv + waitlist.csv + schedule.csv,
                          print a summary
```

Two ideas drive the whole thing:

1. **Fit score (from AI):** how well a student matches a company's needs.
2. **Preference score (from the student):** how much the student wanted that
   company, based on their ranked choices.

These are blended into one **combined score** that the matching algorithm uses:

```
combined = 0.7 * fit_score + 0.3 * preference_score
```

The weights (0.7 and 0.3) live in `config.py` so they are easy to change.

---

## 2. The files, one by one

### `config.py`  (the control panel)

This file holds every setting in one place. Nothing here "does" anything on its
own; the other files read values from it. Keeping settings here means you can
retune the system without touching the logic.

What it defines:

- **Paths** to the data folder, the output folder, and the `.env` file. They are
  built relative to the file's own location, so the program runs correctly no
  matter which folder you launch it from.
- **Matching weights and capacity:** `FIT_WEIGHT = 0.7`, `PREFERENCE_WEIGHT =
  0.3`, `SLOTS_PER_COMPANY = 10`.
- **`PRIORITY_COMPANIES`:** an optional list of companies that get first pick
  (explained under `match.py`). An empty list `[]` means all companies are
  treated equally.
- **`ALLOWED_GRADUATION_YEARS`:** an optional list of graduation years that are
  allowed to take part (the event is for juniors and seniors only). Any student
  whose graduation year is not in this list is dropped before scoring and
  matching. An empty list `[]` disables the filter and includes everyone.
  Example for a 2026 event: `[2026, 2027, 2028]`. Explained under `load_data.py`.
- **`EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP`:** when True (the default), students
  who answered "No" to the work authorization question are treated as ineligible
  and dropped before scoring, just like the graduation filter. When False, they
  are kept and only blocked from non-sponsoring companies at match time (the
  older per-pair hard filter). Explained under `load_data.py` and `match.py`.
- **`PREFERENCE_SCORE_MAP`:** converts a student's rank into points. Rank 1 =
  100, rank 2 = 85, and so on down to rank 6 = 35. A company a student did not
  rank gets `UNRANKED_PREFERENCE_SCORE = 10`. Higher rank means more points,
  which pushes the combined score up.
- **`CRITERIA_WEIGHTS`:** the percentages the AI is told to use when scoring
  (technical skills 35%, experience 15%, project relevance 35%, soft skills
  15%). These appear only inside the text we send to the AI.
- **Gemini settings:** the default model name, the rate limit settings (how long
  to wait between AI calls, how many times to retry), and
  `STUDENTS_PER_SCORING_BATCH` (how many students go in one scoring prompt).
- **Column mappings (`STUDENT_COLS`, `COMPANY_COLS`):** the most important
  practical part. Google Forms produces very long, messy column headers. These
  dictionaries map a short internal name (like `bu_email`) to the exact header
  string in the CSV (like `"BU Email"`). If a form question is reworded later,
  you only edit it here and nothing else breaks.

Key detail: the companies CSV from Google had a broken first header (it showed a
company name instead of "Company Name") and no Timestamp column. The mappings
and `load_data.py` handle that quirk so the rest of the code sees clean data.

---

### `document_text.py`  (turn any document into text)

Job: one shared helper, `extract_text(path)`, that reads a document of any
supported format and returns clean text. Both parsers below route every file
through it, so resumes and company documents are handled identically.

- **Supported formats:** `pdf` (pdfplumber), `docx` (python-docx, including
  table cells), `rtf` (striprtf), `txt` (plain read), `doc` (legacy Word, read
  via a system tool: macOS `textutil`, or LibreOffice / antiword / catdoc), and
  `pages` (Apple Pages, read from the PDF preview embedded in the bundle).
- **No OCR:** a scanned, image only PDF has no text layer, so it yields empty
  text. That is a deliberate limitation.
- **Light cleaning only:** collapse repeated spaces and blank lines, trim the
  ends. It does not try to restructure the document.
- **Never crashes:** an unsupported extension or an unreadable file logs a note
  and returns an empty string, matching the rest of the pipeline's style.

### `parse_resumes.py`  (resumes to text)

Job: build a dictionary mapping each student's email to their resume text.

- It looks in `data/resumes/` and reads every file whose extension is supported,
  calling `extract_text` on each. The format does not matter.
- **Linking resumes to students:** the upload system renames each resume to the
  student's BU email, for example `alex.chen@bu.edu.pdf` (or `.docx`, etc). The
  program takes the filename, removes the extension, lowercases it, and treats
  that as the student's email. This is why **BU Email is the single ID used
  everywhere**, and why the file format is irrelevant.
- **Defensive behavior:** a filename that is not a valid email is logged and put
  in `unmatched_resume_files`; a corrupt or unreadable file just becomes empty
  text and the run continues.

Output: a dictionary mapping `email -> resume_text`, plus the list of unmatched
files. Emails are always lowercased and stripped on both sides (filename and
CSV) so they always match.

### `parse_company_docs.py`  (company job descriptions to text)

Job: the same idea for companies. Each company's roles and job descriptions live
in a document in `data/company_docs/` (the companies CSV "supporting documents"
column is now empty). This builds a dictionary mapping each company to its job
description text, which `load_data.py` attaches as `jd_text`.

- Reads every supported file in `data/company_docs/` via `extract_text`.
- **Linking docs to companies** is by company name, using the same normalization
  as everywhere (drops parentheses, lowercases), plus it strips a trailing
  "(1)" style duplicate suffix and applies a small alias map in config (for
  example the document "Klaviyo" maps to the form's spelling "Klavio").
- A company with no matching document falls back to the CSV column and is named
  in a printed warning.

---

### `load_data.py`  (CSVs to clean tables)

Job: read the two CSV files and produce two clean tables (pandas DataFrames),
one for students and one for companies, and attach each student's resume text.

For **students** it builds columns: name, email (BU Email, the unique ID),
`needs_sponsorship` (True if they answered "No" to "are you authorized to
work"), preferred work type, years of experience, environment, their project
paragraph, their "what I am looking for" paragraph, the resume text, and a
dictionary of their ranked company choices like `{"IBM": 3, "Draper": 1}`.

For **companies** it builds columns: name, contact, roles, required skills,
preferred experience level, job description text, ideal candidate notes, and
`sponsors` (True if they answered "Yes" to sponsoring work authorization).

A few details worth understanding:

1. **The join key is BU Email.** Google also auto-collects an "Email Address"
   field, but we deliberately ignore that one and join on the BU Email the
   student typed, because that is what the resume files are named after.

2. **Fuzzy company name matching (`_norm_company_key`).** The student form lists
   a company as "PJMF" but the company form calls it "PJMF (Patrick J. McGovern
   Foundation)". To make these match, the code normalizes names by lowercasing,
   removing anything in parentheses, and collapsing spaces. So "PJMF" and "PJMF
   (Patrick J. McGovern Foundation)" both reduce to "pjmf" and are treated as
   the same company. This same normalization is reused by the matching and
   priority code.

3. **`preference_score(student, company)` and `preference_rank(...)`.** These
   look up how a student ranked a company (using the fuzzy matching above) and
   return either the points from the preference map or the unranked default of
   10. The matcher calls these.

4. **`filter_by_graduation(students)` (juniors and seniors only).** The event is
   not open to everyone, so this function drops any student whose graduation
   year is not in `config.ALLOWED_GRADUATION_YEARS`. A helper, `_parse_grad_year`,
   pulls the 4 digit year out of whatever the "Graduation Date" cell contains
   (so "2027", "May 2027", and "05/2027" all read as 2027). If the allow list is
   empty the filter is off and everyone stays; if it is on, students with a
   missing or unreadable date are also dropped and reported, since we cannot
   confirm they are eligible. `main.py` runs this right after loading, before
   scoring, so an ineligible student never costs an API call and never reaches
   the match.

5. **`filter_by_sponsorship(students)` (work authorization).** Controlled by
   `config.EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP`. When on (the default), students
   who need sponsorship (answered "No" to work authorization) are dropped before
   scoring, exactly like the graduation filter, so they are never sent to the AI
   or matched. When off, nobody is dropped here and sponsorship is handled later,
   per company pair, by the hard filter in `match.py`. `main.py` runs this right
   after the graduation filter; the two run in sequence and each excluded student
   is attributed to the first filter that removed them, so the counts never
   double count someone caught by both.

6. **Duplicate submissions are collapsed (`_dedupe_keep_latest`).** If a student
   fills the form out more than once (say to correct an answer), the CSV has two
   rows with the same BU Email. Left alone, that student would be scored twice,
   counted twice, and matched twice. So both loaders deduplicate as the very
   first step. Students are deduplicated by BU Email keeping the **latest**
   submission by Timestamp, so a correction wins over the original. Companies are
   deduplicated by company name; that export has no Timestamp column, so file
   order stands in for recency (the later row wins). Blank keys are never merged,
   and any collapse is reported so it is visible rather than silent.

Finally, `validate_and_report(...)` prints sanity checks: how many students and
companies loaded, any company in a "Choices [...]" column that has no matching
company in the companies file (and the reverse), and which students are missing
a resume. This is how you catch data problems before trusting the results.

---

### `score.py`  (AI fit scoring + the work authorization rule + caching)

Job: produce a fit score from 0 to 100 for every student/company pair. This is
the only file that talks to the Gemini AI.

How the scoring works:

- **Scored per company, in student batches.** For each company the code asks the
  AI to score the students and return a JSON list with one score per student.
  Doing it per company (instead of one call per student/company pair) keeps the
  number of calls small and lets the AI compare students against each other.
  Within a company the students are split into batches of
  `STUDENTS_PER_SCORING_BATCH` (default 30, set in `config.py`). Batching matters
  at the real event scale: putting 150 to 200 full resumes into a single prompt
  would risk the context limit, dilute scoring quality, and make one malformed
  reply force everyone to be re-scored. Small batches keep each prompt focused.
  With a cohort smaller than the batch size, it is just one call per company.
- **Students are numbered, not named, in the answer.** The prompt asks the AI to
  return `{"student": 1, "score": 78, ...}` rather than using names, to reduce
  the chance the AI is biased by a name.
- **The prompt is ordered for caching.** The instructions and the student block
  come first; the company specific block comes last. For a given batch of
  students that leading section is identical no matter which company is being
  scored, so it forms a stable prefix that Gemini's prefix caching can reuse
  across all companies, instead of re-reading every resume once per company.
- **The prompt tells the AI how to score:** the criteria weights, a 0-100
  guideline scale, and instructions to reward concrete projects over buzzwords
  and not to penalize missing information.

Robustness (because AI output is not always clean):

- It strips any Markdown code fences (` ``` `) before reading the JSON.
- If the JSON fails to parse, or the number of scores does not match the number
  of students in the batch, it retries that batch up to 2 more times, then fails
  loudly with a clear message rather than guessing.
- Every score is clamped into the 0 to 100 range.

**The work authorization rule (this part is in code, not in the prompt).** There
are two ways this can run, set by `config.EXCLUDE_STUDENTS_NEEDING_SPONSORSHIP`:

- **Default (flag on): exclude before scoring.** Students who need sponsorship
  are dropped by `filter_by_sponsorship` in `load_data.py` before this file ever
  runs, so they are simply not scored. Cleanest and cheapest.
- **Flag off: the per-pair hard filter.** Those students are kept, and after the
  AI returns its scores this file checks each pair: if the student needs
  sponsorship and the company does not sponsor, it overwrites that fit score with
  0 and records the reason "Hard filter: requires sponsorship, company does not
  sponsor." The matcher then also treats these pairs as forbidden so they can
  never be assigned (see `match.py`); zeroing the fit score is only the visible,
  audit-friendly half.

Either way the rule lives in Python, not the prompt, so it is a guaranteed rule
and not something the AI might apply inconsistently.

**Caching and resuming (this protects your API budget):**

- All results are written to `output/scores.csv` with columns: student name,
  student email, company name, fit score, reason.
- The file is saved after **every company**, not just at the end. So if the run
  crashes at company 15 of 17, the first 14 are already saved.
- On the next run, any company already in the cache is skipped. Re-running never
  repeats a paid AI call. To force a fresh run, pass `--rescore`.

**Rate limiting:** the Gemini free tier allows only about 15 requests per
minute. Because batching means several calls per company, the code paces *every*
API call centrally so any two calls are at least a few seconds apart, and if it
still hits a "429 quota" error it reads the suggested wait time and pauses
instead of failing.

---

### `match.py`  (combine scores, then run the matching algorithm)

Job: turn all the scores and preferences into a final assignment.

Step 1: **Build the combined score** for every student/company pair:

```
combined = 0.7 * fit_score + 0.3 * preference_score
```

So a student ranks high for a company when both the AI likes the fit and the
student wanted that company.

While building these scores the code also collects the set of **forbidden
pairs**: any (student, company) where the student needs sponsorship and the
company does not sponsor. This is the enforced half of the work authorization
hard filter. The matcher removes forbidden pairs from every company's wish list,
so such a pair can never be assigned. (Zeroing the fit score alone was not
enough, because the preference component could still pull a forbidden pair into
a match when seats are scarce.)

Step 2: **Run company-proposing Gale-Shapley with capacity.** This is the
classic stable matching algorithm. In plain terms:

1. Each company makes a wish list of its eligible students (forbidden pairs
   excluded), sorted by combined score (best first).
2. Companies "propose" to their top students.
3. A student can hold only one offer at a time. If two companies want the same
   student, the student keeps whichever offer has the higher combined score and
   rejects the other.
4. A rejected company moves down its list and proposes to its next choice.
5. This repeats until every company has either filled its slots (10 by default)
   or run out of students to ask.

Why this algorithm: the result is **stable**. That means there is no
student/company pair who would both rather be with each other than with who they
ended up with. Stability is the standard fairness guarantee for this kind of
"two sided" matching, and Gale-Shapley provably produces it. (This is the same
family of algorithm used for matching medical residents to hospitals.)

Step 3: **The waitlist.** Any student who holds no offer at the end is put on
the waitlist, sorted by their single best combined score across the companies
they are eligible for, so the strongest near-misses are listed first. Forbidden
companies are skipped here too; a student for whom every company is forbidden is
listed with "none eligible".

**Priority (`run_priority_match`, the optional two round mode).** Normal
Gale-Shapley treats all companies as equal, and the order does not affect the
result. To let some companies genuinely get first pick, the code can run the
matcher in two rounds:

- Round 1: only the companies in `PRIORITY_COMPANIES` (currently IBM and Enlaye)
  match, against the full student pool. They lock in their picks first.
- Round 2: every other company matches, but only over the students who were not
  taken in round 1.

If `PRIORITY_COMPANIES` is empty, this collapses back to a single normal round,
so the feature is fully opt in and the default behavior is unchanged.

---

### `schedule.py`  (assign interview time slots)

Job: run after matching, to give each matched student an actual interview time.
Students pick the times they are available on the form (for example "3:00 pm,
3:15 pm, ..."), parsed into each student's record by `load_data.py`.

The key insight: since each student is matched to exactly one company, each has
exactly one interview, so a student can never have a time conflict across
companies. The only conflict is **within** a company: two of its students
wanting the same slot. A company runs one interview per slot, so the number of
distinct time slots is the real ceiling on its interviews.

The rule is **greedy by score**: for each company, sort its matched students by
fit score (highest first); each takes the earliest slot they marked available
that is not already taken at that company. So a contested slot goes to the higher
scored student. A student who cannot get any available slot is **unscheduled**
and moved to the waitlist with the reason "no available interview slot".

Output: each matched student gains a `time_slot`, written to `matches.csv` and to
a clean per-company timetable, `schedule.csv`.

---

### `main.py`  (the conductor)

Job: run the five steps in order and report the results. It contains no matching
logic itself; it just calls the other files and prints a summary.

In order, it:

1. Calls `parse_resumes()` and `parse_company_docs()` to read all the documents.
2. Calls `load_students()` and `load_companies()` (attaching job descriptions),
   applies the eligibility filters `filter_by_graduation()` then
   `filter_by_sponsorship()` (dropping ineligible students before any cost is
   incurred), then `validate_and_report()`.
3. Calls `score_all()` (uses the cache, or calls the AI with `--rescore`).
4. Calls `run_priority_match()` to produce the matches and waitlist, then
   `schedule_interviews()` to assign each matched student a time slot.
5. Writes `matches.csv`, `waitlist.csv`, and `schedule.csv`, then prints the
   summary report.

The **summary report** opens with a STUDENT FUNNEL that shows the count at each
stage: registered (loaded), excluded by graduation year, excluded by work
authorization, eligible (scored and matched), matched, and waitlisted, so you
can see exactly where students dropped out. The two exclusion counts are
disjoint (a student caught by both is counted once, under whichever filter ran
first), so the numbers always add up. It then shows: per company how many seats
were filled and the average fit of its matched students; how many students got
their 1st, 2nd, 3rd, lower, or an unranked choice; the full list of students
excluded by graduation year and by work authorization; any students flagged for
missing resumes (these are still included, not excluded); and a note for any
company that did not fill all its seats.

Output files it produces in `output/`:

- **`matches.csv`:** the final answer. One row per matched student with their
  company, assigned time slot, fit score, the rank they gave that company, the
  combined score, and the AI's one line reason.
- **`waitlist.csv`:** unmatched students with their best near-miss company,
  scores, and a reason (capacity, or "no available interview slot").
- **`schedule.csv`:** the timetable, one row per interview, sorted by company
  then time slot.
- **`scores.csv`:** the full AI score sheet (also the cache).

---

## 3. Supporting files

- **`.env`:** holds your secret `GEMINI_API_KEY` and the `GEMINI_MODEL` name.
  Kept out of the code so the key is not hard coded.
- **`requirements.txt`:** the list of Python libraries to install
  (`pandas`, `pdfplumber`, `python-dotenv`, `google-genai`, `python-docx`,
  `striprtf`). Legacy `.doc` files use a system tool, not a Python package.
- **`data/`:** inputs. `students.csv`, `companies.csv`, the `resumes/` folder,
  and the `company_docs/` folder (both accept any supported document format).
- **`output/`:** the generated CSV files (matches, waitlist, schedule, scores).

### Test files (in the `Tests/` folder, outside `talent_sprint/`)

These reuse the cached scores and make **no AI calls**. They exist to prove the
matching logic behaves correctly on small, easy to read setups.

- **`test_waitlist_2companies.py`:** 2 companies, 5 seats each, all 16 students.
  That is 10 seats for 16 students, so 6 must be waitlisted. It checks the
  matcher and the waitlist ordering.
- **`test_priority_rounds_4companies.py`:** 4 companies, 3 seats each, with IBM
  and Bloomberg given priority. It calls the same `run_priority_match` the real
  pipeline uses, so it verifies the two round priority behavior end to end.

---

## 4. How the pieces fit together (the data trail)

Follow one student, Alex Chen, through the system:

1. `parse_resumes.py` reads `alexchen@bu.edu.pdf` into text and stores it under
   `alexchen@bu.edu`.
2. `load_data.py` reads the student CSV row for `alexchen@bu.edu`, attaches that
   resume text, and records Alex's ranked choices, sponsorship status, and
   graduation year. The eligibility filters run here: if the graduation filter is
   on and Alex's year is not allowed, or if Alex needs sponsorship and the work
   authorization exclusion is on, Alex is dropped and the trail stops.
3. `score.py` includes Alex in each company's scoring batch and gets back a fit
   score per company.
4. `match.py` computes Alex's combined score for each company (if the work
   authorization exclusion is off, any non-sponsoring companies are dropped
   here), and the Gale-Shapley algorithm assigns Alex to a single company (or
   the waitlist).
5. `main.py` writes Alex's row into `matches.csv` and includes Alex in the
   summary counts.

Everything is tied together by one identifier, the **BU Email**, which is why so
much care is taken to normalize it consistently across the resume filenames and
the CSV.

---

## 5. The three or four sentences to say out loud

"The pipeline reads each resume PDF and the two Google Form CSVs, keyed on the
student's BU email. It first applies eligibility filters in code, by graduation
year and by work authorization, so only eligible students are scored at all. For
every company it sends the eligible students to the Gemini AI in batches to score
them 0 to 100 on fit. It blends each fit score with the student's stated
preference into a combined score, and runs the company-proposing Gale-Shapley
algorithm with a capacity per company to produce a stable assignment, plus a
waitlist. The summary reports a funnel showing how many students were registered,
excluded at each stage, eligible, matched, and waitlisted. Scores are cached so
re-runs cost nothing, and an optional priority mode lets chosen companies pick
first by matching in an earlier round."
