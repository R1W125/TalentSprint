"""
Resume PDF parsing.

Extracts clean text from every PDF in data/resumes/ and maps it to a
normalized student email. Resume to student linking is by BU Email, because
the upload pipeline renames each submitted PDF to the student's BU Email
(for example "alex.chen@bu.edu.pdf"). We derive the email from the filename
by stripping the extension and lowercasing.

Cleaning is intentionally light. We collapse runs of whitespace and blank
lines and strip the ends. We do not try to restructure the resume.
"""

import os
import re

import pdfplumber

from config import RESUMES_DIR


def normalize_email(email):
    """Normalize an email the same way on both sides: lowercase and strip."""
    return (email or "").strip().lower()


# A pragmatic email shape check. We only need to tell a real BU style email
# filename apart from something like "student1_alex_chen". Good enough here.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(text):
    return bool(_EMAIL_RE.match(text))


def _clean_text(raw):
    """Light cleanup only: collapse whitespace and blank lines, then strip."""
    if not raw:
        return ""
    # Normalize line endings.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse spaces and tabs (but keep newlines so layout stays readable).
    text = re.sub(r"[ \t]+", " ", text)
    # Trim trailing spaces on each line.
    text = re.sub(r" *\n", "\n", text)
    # Collapse 3 or more blank lines down to a single blank line.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_resumes(resumes_dir=RESUMES_DIR):
    """
    Parse every PDF in resumes_dir.

    Returns a tuple:
      resume_texts: dict mapping normalized email -> cleaned resume text
      unmatched_resume_files: list of filenames whose name is not a valid email

    Files whose name is not a valid email are logged and collected rather than
    crashing the run.
    """
    resume_texts = {}
    unmatched_resume_files = []

    if not os.path.isdir(resumes_dir):
        print(f"WARNING: resumes directory not found at {resumes_dir}")
        return resume_texts, unmatched_resume_files

    pdf_files = sorted(
        f for f in os.listdir(resumes_dir) if f.lower().endswith(".pdf")
    )

    for fname in pdf_files:
        stem = os.path.splitext(fname)[0]
        email = normalize_email(stem)

        if not _looks_like_email(email):
            # Defensive: test PDFs may still be named like student1_alex_chen.
            print(
                f"NOTE: resume filename is not a valid email, skipping link: {fname}"
            )
            unmatched_resume_files.append(fname)
            continue

        path = os.path.join(resumes_dir, fname)
        try:
            pages = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    pages.append(page.extract_text() or "")
            resume_texts[email] = _clean_text("\n".join(pages))
        except Exception as exc:  # noqa: BLE001 - we never want one bad PDF to stop the run
            print(f"WARNING: failed to parse {fname}: {exc}")
            resume_texts[email] = ""

    return resume_texts, unmatched_resume_files


if __name__ == "__main__":
    texts, unmatched = parse_resumes()
    print(f"Parsed {len(texts)} resumes.")
    if unmatched:
        print(f"Unmatched resume files: {unmatched}")
