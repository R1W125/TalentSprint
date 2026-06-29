"""
Company document parsing.

Each company's roles and job descriptions now live in a PDF in
data/company_docs/ (the companies CSV "supporting documents" column is empty).
This module extracts clean text from each PDF and maps it to a normalized
company key, the same way parse_resumes.py handles student resumes.

Linking docs to companies is by company name. The PDF is named after the
company (for example "IBM.pdf", "Red Hat.pdf"). Real exports are messy, so the
key is normalized with the same _norm_company_key used everywhere else (drops
parentheses, lowercases, collapses spaces), a trailing " (1)" style duplicate
suffix is stripped, and a small alias map in config handles known spelling
differences (for example the doc "Klaviyo" vs the form's "Klavio").

Any supported document format works (pdf, docx, doc, rtf, pages, txt); the
actual extraction lives in document_text.extract_text.
"""

import os
import re

import config
from load_data import _norm_company_key
from document_text import extract_text, SUPPORTED_EXTENSIONS


def _doc_key(filename):
    """
    Turn a document filename into a normalized company key.

    Strips the extension and any trailing " (1)" duplicate marker, applies the
    config alias map, then normalizes the same way company names are normalized.
    """
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)  # drop a trailing "(1)" style suffix
    key = _norm_company_key(stem)
    # Alias map keys/values are normalized so the lookup is order independent.
    aliases = {
        _norm_company_key(k): _norm_company_key(v)
        for k, v in config.COMPANY_NAME_ALIASES.items()
    }
    return aliases.get(key, key)


def parse_company_docs(docs_dir=None):
    """
    Parse every supported document in docs_dir.

    Returns a tuple:
      doc_texts: dict mapping normalized company key -> cleaned document text
      unmatched_doc_files: list of filenames whose name is not usable as a key

    Any supported format is read (see document_text.SUPPORTED_EXTENSIONS); other
    files are ignored. A file that yields an empty key is logged and collected
    rather than crashing.
    """
    docs_dir = docs_dir or config.COMPANY_DOCS_DIR
    doc_texts = {}
    unmatched_doc_files = []

    if not os.path.isdir(docs_dir):
        print(f"WARNING: company documents directory not found at {docs_dir}")
        return doc_texts, unmatched_doc_files

    files = sorted(
        f for f in os.listdir(docs_dir)
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTENSIONS
    )

    for fname in files:
        key = _doc_key(fname)
        if not key:
            print(f"NOTE: company document filename is not usable, skipping: {fname}")
            unmatched_doc_files.append(fname)
            continue

        doc_texts[key] = extract_text(os.path.join(docs_dir, fname))

    return doc_texts, unmatched_doc_files


if __name__ == "__main__":
    texts, unmatched = parse_company_docs()
    print(f"Parsed {len(texts)} company document(s).")
    for k in sorted(texts):
        print(f"  {k:25} {len(texts[k])} chars")
    if unmatched:
        print(f"Unmatched document files: {unmatched}")
