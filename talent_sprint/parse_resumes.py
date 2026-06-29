"""
Resume parsing.

Extracts clean text from every resume in data/resumes/ and maps it to a
normalized student email. Any supported document format works (pdf, docx, doc,
rtf, pages, txt); the actual extraction lives in document_text.extract_text.
Resume to student linking is by BU Email, because the upload pipeline renames
each submitted file to the student's BU Email (for example "alex.chen@bu.edu.pdf"
or "alex.chen@bu.edu.docx"). We derive the email from the filename by stripping
the extension and lowercasing, so the format does not matter.
"""

import os
import re

from config import RESUMES_DIR
from document_text import extract_text, SUPPORTED_EXTENSIONS, _clean_text  # noqa: F401


def normalize_email(email):
    """Normalize an email the same way on both sides: lowercase and strip."""
    return (email or "").strip().lower()


# A pragmatic email shape check. We only need to tell a real BU style email
# filename apart from something like "student1_alex_chen". Good enough here.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(text):
    return bool(_EMAIL_RE.match(text))


def parse_resumes(resumes_dir=RESUMES_DIR):
    """
    Parse every supported resume file in resumes_dir.

    Returns a tuple:
      resume_texts: dict mapping normalized email -> cleaned resume text
      unmatched_resume_files: list of filenames whose name is not a valid email

    Any supported format is read (see document_text.SUPPORTED_EXTENSIONS); other
    files are ignored. Files whose name is not a valid email are logged and
    collected rather than crashing the run.
    """
    resume_texts = {}
    unmatched_resume_files = []

    if not os.path.isdir(resumes_dir):
        print(f"WARNING: resumes directory not found at {resumes_dir}")
        return resume_texts, unmatched_resume_files

    files = sorted(
        f for f in os.listdir(resumes_dir)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
    )

    for fname in files:
        stem = os.path.splitext(fname)[0]
        email = normalize_email(stem)

        if not _looks_like_email(email):
            # Defensive: test files may still be named like student1_alex_chen.
            print(
                f"NOTE: resume filename is not a valid email, skipping link: {fname}"
            )
            unmatched_resume_files.append(fname)
            continue

        path = os.path.join(resumes_dir, fname)
        resume_texts[email] = extract_text(path)

    return resume_texts, unmatched_resume_files


if __name__ == "__main__":
    texts, unmatched = parse_resumes()
    print(f"Parsed {len(texts)} resumes.")
    if unmatched:
        print(f"Unmatched resume files: {unmatched}")
