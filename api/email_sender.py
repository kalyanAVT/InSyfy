"""Sends a generated report PDF as an email attachment via SMTP.

Uses smtplib directly rather than MCP: this backend has no MCP-aware
orchestrator for a Gmail MCP server to plug into (see the earlier
conversation about why MCP doesn't fit a standalone FastAPI backend).
Configure via SMTP_* env vars — a Gmail App Password works fine with the
defaults below.
"""

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(address: str) -> bool:
    return bool(address and _EMAIL_RE.match(address.strip()))


def send_report_email(to_email: str, subject: str, pdf_bytes: bytes, filename: str) -> tuple:
    """Send a PDF report as an email attachment.

    Returns (success: bool, message: str). Never raises — callers get a
    clear reason for failure (missing config, bad address, SMTP error)
    instead of a stack trace bubbling up through the API.
    """
    if not is_valid_email(to_email):
        return False, f"'{to_email}' doesn't look like a valid email address."

    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    from_address = os.getenv("SMTP_FROM_ADDRESS", username)

    if not host or not username or not password:
        return False, (
            "Email sending is not configured on the server. "
            "Set SMTP_HOST, SMTP_USERNAME, and SMTP_PASSWORD in .env."
        )

    try:
        msg = MIMEMultipart()
        msg["From"] = from_address
        msg["To"] = to_email
        msg["Subject"] = subject

        msg.attach(MIMEText(
            "Your InSyfy research report is attached as a PDF.\n\n"
            "This email was sent automatically — reply directly to the "
            "sender's inbox if you have questions.",
            "plain"
        ))

        part = MIMEBase("application", "pdf")
        part.set_payload(pdf_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as server:
                server.login(username, password)
                server.sendmail(from_address, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.starttls()
                server.login(username, password)
                server.sendmail(from_address, [to_email], msg.as_string())

        return True, f"Report sent to {to_email}."

    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed — check SMTP_USERNAME/SMTP_PASSWORD."
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"