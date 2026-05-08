"""Cosmetic post-processing for prep output documents.

- `_add_cover_letter_spacing(docx_path)` — fixes paragraph spacing, strips
  pandoc bookmark anchors that render as blue brackets in Google Docs.
- `_linkify_contact_info(md)` — converts bare email + LinkedIn URLs to
  Markdown hyperlinks before pandoc conversion so the docx has
  clickable links.

Extracted from `scripts/prep_application.py` in M3 (#537). Behavior
preserved verbatim — both functions are idempotent and silent on
failure (post-processing must never block prep).
"""

import re


def _add_cover_letter_spacing(docx_path: str) -> None:
    """Post-process cover letter .docx for clean formatting.

    Heading 1 is left untouched — the reference.docx theme renders it correctly
    (teal color, heading font) in Google Docs. Adjustments:
    1. Remove pandoc bookmark anchors (render as blue bracket in Google Docs)
    2. Space before the date line to separate from contact info
    3. 12pt space-after from date onward for readable paragraph gaps
    """
    try:
        from docx import Document
        from docx.shared import Pt

        doc = Document(docx_path)
        if not doc.paragraphs:
            return

        # 1. Strip bookmark anchors that pandoc adds to headings
        body = doc.element.body
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        for tag in ("bookmarkStart", "bookmarkEnd"):
            for bm in body.findall(f".//{{{ns}}}{tag}"):
                bm.getparent().remove(bm)

        # 2. Add space before the date line (paragraph [2]) to separate from contact
        if len(doc.paragraphs) > 2:
            doc.paragraphs[2].paragraph_format.space_before = Pt(12)

        # 3. Add 12pt space-after from date onward
        for para in doc.paragraphs[2:]:
            if para.text.strip():
                para.paragraph_format.space_after = Pt(12)

        doc.save(docx_path)
    except Exception:
        pass  # post-processing is cosmetic — never block prep


def _linkify_contact_info(md: str) -> str:
    """Ensure bare email addresses and LinkedIn URLs are Markdown hyperlinks.

    Runs on resume markdown before pandoc conversion so the .docx has clickable links.
    Skips anything already inside []() link syntax.
    """
    # Email: bare user@domain.tld → [user@domain.tld](mailto:user@domain.tld)
    md = re.sub(
        r"(?<!\[)(?<!\(mailto:)\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b(?!\])",
        r"[\1](mailto:\1)",
        md,
    )
    # LinkedIn: bare linkedin.com/in/handle → [linkedin.com/in/handle](https://linkedin.com/in/handle)
    md = re.sub(
        r"(?<!\[)(?<!\(https://)(linkedin\.com/in/[A-Za-z0-9_-]+)(?!\])",
        r"[\1](https://\1)",
        md,
    )
    return md
