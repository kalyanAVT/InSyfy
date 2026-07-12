"""Converts a report's markdown into a downloadable PDF.

Deliberately built on xhtml2pdf + markdown2 rather than weasyprint:
both are pure-Python with no system-level dependencies (weasyprint needs
Pango/Cairo/GTK installed separately, which is a real pain on Windows dev
machines). Generation is fully offline — no external fonts or network
calls, just local computation.
"""

import re
import markdown2
from xhtml2pdf import pisa
import io


# Literal hex values, not CSS variables — xhtml2pdf supports a subset of
# CSS 2.1 (it renders via reportlab) and does not support var(). Matches
# the same teal palette used in the Gradio report-markdown styling.
_PDF_CSS = """
<style>
    @page { size: A4; margin: 2cm; }
    body {
        font-family: Helvetica, Arial, sans-serif;
        color: #172b29;
        font-size: 10.5pt;
        line-height: 1.5;
    }
    h1 {
        color: #172b29;
        font-size: 22pt;
        border-bottom: 2px solid #2a7672;
        padding-bottom: 8px;
    }
    h2 {
        color: #1c534f;
        font-size: 15pt;
        margin-top: 20px;
        border-left: 3px solid #2a7672;
        padding-left: 8px;
    }
    h3 {
        color: #172b29;
        font-size: 12.5pt;
        margin-top: 14px;
    }
    em { color: #4d6260; }
    code {
        background: #eef4f3;
        color: #1c534f;
        padding: 1px 4px;
        font-family: Courier, monospace;
    }
    a { color: #1c534f; }
    hr { border: none; border-top: 1px solid #c7d6d3; margin: 16px 0; }
    ul, ol { margin-left: 4px; }
    li { margin-bottom: 4px; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #c7d6d3; padding: 6px 8px; font-size: 9.5pt; }
</style>
"""


def _strip_unsupported_markdown(text: str) -> str:
    """Remove markdown constructs xhtml2pdf can't render (raw <a id> anchors
    used for in-app footnote jump-links) so they don't show up as visible
    HTML artifacts in the PDF."""
    return re.sub(r'<a id="[^"]*"></a>', '', text)


def markdown_to_pdf_bytes(report_markdown: str, question: str = "") -> bytes:
    """Convert a report's markdown text into PDF bytes.

    Raises RuntimeError with xhtml2pdf's error output if conversion fails,
    rather than silently returning empty/corrupt bytes.
    """
    cleaned = _strip_unsupported_markdown(report_markdown)
    html_body = markdown2.markdown(
        cleaned,
        extras=["tables", "fenced-code-blocks", "cuddled-lists"]
    )

    full_html = f"<html><head>{_PDF_CSS}</head><body>{html_body}</body></html>"

    buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(full_html), dest=buffer)

    if result.err:
        raise RuntimeError(f"PDF generation failed with {result.err} error(s)")

    return buffer.getvalue()