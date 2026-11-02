"""One parser per source format: the support knowledge base is not one kind of file.

shared/kb/ holds what a real support desk has lying around: product help
articles in Markdown, carrier notice pages saved as HTML, ops emails (.eml)
and an export of resolved tickets (CSV). Each parser turns one file into
records, every record a dict {"text", "metadata"}:

- text is what will be chunked and embedded: words a reader would see, no
  markup, no page chrome, no mail headers;
- metadata is what the FILE itself says (a title, an email's subject and
  date, a CSV row's id). Who may read a document, and whether it is still
  current, are NOT here: the text carries no permissions. Those come from
  the manifest, joined in kb_ingest.py, the way a SharePoint or Confluence
  connector asks the source system rather than the document.

Standard library only (html.parser, email, csv). PDFs and scans would need
a PDF library and OCR; this corpus has none on purpose.

Try one:  py kb_parsers.py ..\\..\\shared\\kb\\carrier\\parcelink-holiday-2026.html
"""
from __future__ import annotations

import csv
import io
import re
import sys
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


def _title_from_markdown(text: str) -> str | None:
    match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Markdown (manuals/, macros/)
# ---------------------------------------------------------------------------

def parse_markdown(text: str) -> list[dict]:
    """A Markdown article is already clean text: one record, the whole file.
    kb_ingest.py cuts it at `##`, the same structural split as the policy pack."""
    return [{"text": text.strip(), "metadata": {"title": _title_from_markdown(text)}}]


# ---------------------------------------------------------------------------
# HTML (carrier/)
# ---------------------------------------------------------------------------

# A saved web page is mostly chrome: the menu, the cookie banner, the footer
# links, the analytics scripts. Embedded with the article, "Track | Ship |
# Business | Log in" lands in every carrier chunk and pulls them all towards
# any query about tracking. So these elements are dropped whole.
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "header",
              "footer", "aside", "form", "button"}
# The same chrome marked by ARIA role instead of tag, e.g. a cookie banner
# that is a <div role="dialog">.
_SKIP_ROLES = {"dialog", "navigation", "banner", "contentinfo", "complementary"}
# Elements that never get a closing tag, so they never go on the stack.
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
              "meta", "source", "track", "wbr"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "table",
               "ul", "ol", "blockquote", "pre", "dl", "dt", "dd"}


class _TextExtractor(HTMLParser):
    """Walk the tags, keep the readable text, mark the headings.

    Headings come out as Markdown (`# ` for h1, `## ` for h2 and h3), so
    kb_ingest.py can split a page at its sections exactly like an article.
    Text inside <main> (or <article>) is kept apart: when a page has one, that
    is the content, and everything outside it is chrome we did not list.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # &middot; arrives as a character
        self._stack: list[tuple[str, bool]] = []  # (tag, starts a skipped subtree)
        self._skipping = 0  # how many open skipped elements we are inside
        self._main_depth = 0
        self.title: str | None = None
        self._in_title = False
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []

    def _emit(self, text: str) -> None:
        if self._skipping:
            return
        self.all_parts.append(text)
        if self._main_depth:
            self.main_parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _VOID_TAGS:
            if tag == "br":
                self._emit("\n")
            return
        skip = tag in _SKIP_TAGS or dict(attrs).get("role") in _SKIP_ROLES
        # A <header> or <footer> INSIDE the article holds its headline or
        # byline; only the page-level ones are chrome.
        if tag in {"header", "footer"} and self._main_depth:
            skip = False
        self._stack.append((tag, skip))
        if skip:
            self._skipping += 1
        if tag in {"main", "article"}:
            self._main_depth += 1
        if tag == "h1":
            self._emit("\n\n# ")
        elif tag in {"h2", "h3"}:
            self._emit("\n\n## ")
        elif tag == "li":
            self._emit("\n- ")
        elif tag == "tr":
            self._emit("\n")
        elif tag in {"td", "th"}:
            self._emit(" | ")
        elif tag in _BLOCK_TAGS:
            self._emit("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        # Pop up to the matching open tag. Real pages leave some tags open
        # (<p> without </p>); popping to the match closes those too.
        if not any(open_tag == tag for open_tag, _ in self._stack):
            return  # a stray closing tag: ignore it
        while self._stack:
            open_tag, skip = self._stack.pop()
            if skip:
                self._skipping -= 1
            if open_tag in {"main", "article"}:
                self._main_depth -= 1
            if open_tag == tag:
                break
        if tag in _BLOCK_TAGS or tag in {"h1", "h2", "h3"}:
            self._emit("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data.strip()
            return
        # Whitespace in HTML source is layout, not content: collapse it.
        self._emit(re.sub(r"\s+", " ", data))


def _tidy(text: str) -> str:
    """Trim spaces at line ends and squeeze blank lines to one."""
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> tuple[str, str | None]:
    """The readable text of an HTML page (headings as Markdown) and its <title>."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    parts = parser.main_parts or parser.all_parts
    return _tidy("".join(parts)), parser.title


def parse_html(html: str) -> list[dict]:
    """A saved web page: one record, the article's text without the chrome."""
    text, page_title = html_to_text(html)
    # The <h1> names the notice; <title> often adds the site name after a "|".
    title = _title_from_markdown(text) or page_title
    return [{"text": text, "metadata": {"title": title}}]


# ---------------------------------------------------------------------------
# Email (ops/)
# ---------------------------------------------------------------------------

def parse_email(raw: bytes) -> list[dict]:
    """An .eml file: one record, the subject and the body.

    The headers above the blank line (Received, Message-ID, MIME-Version)
    describe the delivery, not the content, so only four survive, as
    metadata. policy.default decodes encoded subjects and quoted-printable
    bodies, and get_body picks the text part of a multipart message.
    """
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_part = message.get_body(preferencelist=("plain", "html"))
    body = body_part.get_content() if body_part is not None else ""
    if body_part is not None and body_part.get_content_type() == "text/html":
        body, _ = html_to_text(body)
    subject = str(message.get("Subject", "")).strip()
    sent = message.get("Date")
    try:
        sent_iso = parsedate_to_datetime(str(sent)).isoformat() if sent else None
    except (TypeError, ValueError):  # a malformed Date header is not fatal
        sent_iso = None
    return [{
        # The subject goes first in the text too: it is the email's headline
        # and often the only place the product is named in full.
        "text": f"{subject}\n\n{_tidy(body)}" if subject else _tidy(body),
        "metadata": {
            "title": subject or None,
            "from": str(message.get("From", "")) or None,
            "to": str(message.get("To", "")) or None,
            "sent_at": sent_iso,
        },
    }]


# ---------------------------------------------------------------------------
# CSV (past-tickets.csv)
# ---------------------------------------------------------------------------

def parse_csv(text: str) -> list[dict]:
    """A table export: one record per row, never one per file.

    Each row is a whole resolved ticket. Chunking the file by size would cut
    rows in half and glue strangers together; one row, one chunk keeps each
    resolution next to its own problem. The column names go into the text
    ("resolution notes: ...") so the embedding knows which field said what.
    """
    reader = csv.DictReader(io.StringIO(text))
    id_column = reader.fieldnames[0] if reader.fieldnames else None
    records = []
    for row_number, row in enumerate(reader, start=1):
        lines = [
            f"{column.replace('_', ' ')}: {value.strip()}"
            for column, value in row.items()
            if column and value and value.strip()
        ]
        if not lines:
            continue  # a blank line at the end of the export
        records.append({
            "text": "\n".join(lines),
            "metadata": {
                "row": row_number,
                "row_id": (row.get(id_column) or "").strip() or None,
            },
        })
    return records


# ---------------------------------------------------------------------------
# Dispatch by extension
# ---------------------------------------------------------------------------

# The source_type stored on every chunk, by file extension.
SOURCE_TYPES = {".md": "markdown", ".html": "html", ".htm": "html", ".eml": "email",
                ".csv": "csv"}


def parse_file(path: Path) -> list[dict]:
    """Parse one knowledge-base file with the parser for its extension."""
    source_type = SOURCE_TYPES.get(path.suffix.lower())
    if source_type is None:
        raise ValueError(f"{path.name}: no parser for {path.suffix!r} files")
    if source_type == "email":
        return parse_email(path.read_bytes())  # email reads bytes: it decodes per part
    # utf-8-sig: a file saved by Excel or Notepad may start with a byte-order
    # mark, which would otherwise become part of the first column's name.
    text = path.read_text(encoding="utf-8-sig")
    return {"markdown": parse_markdown, "html": parse_html, "csv": parse_csv}[source_type](text)


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        for record in parse_file(Path(arg)):
            print(f"--- {record['metadata']}\n{record['text'][:1200]}\n")
