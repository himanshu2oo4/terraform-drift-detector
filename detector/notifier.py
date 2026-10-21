"""Sends alert emails over SMTP. Settings come from environment variables."""

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

logger = logging.getLogger("drift-detector")


class ConfigError(Exception):
    """Raised when required email settings are missing or invalid."""


@dataclass
class EmailSettings:
    """Everything needed to connect to a mail server and address a letter."""

    host: str
    port: int
    username: str
    password: str
    sender: str
    recipients: list[str]


def load_email_settings() -> EmailSettings:
    """Read settings from environment variables, failing clearly if any are missing."""
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ConfigError("Missing environment variables: " + ", ".join(missing))

    try:
        port = int(os.environ["SMTP_PORT"])
    except ValueError:
        raise ConfigError("SMTP_PORT must be a number, for example 587.") from None

    recipients = [item.strip() for item in os.environ["EMAIL_TO"].split(",") if item.strip()]

    return EmailSettings(
        host=os.environ["SMTP_HOST"],
        port=port,
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        sender=os.environ["EMAIL_FROM"],
        recipients=recipients,
    )


def send_email(
    settings: EmailSettings,
    subject: str,
    body: str,
    attachment: str | None = None,
    timeout_seconds: int = 30,
) -> None:
    """Build one email and send it over an encrypted connection."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content(body)
    if attachment:
        message.add_attachment(
            attachment.encode("utf-8"),
            maintype="text",
            subtype="plain",
            filename="detector.log",
        )

    context = ssl.create_default_context()
    with smtplib.SMTP(settings.host, settings.port, timeout=timeout_seconds) as server:
        server.starttls(context=context)
        server.login(settings.username, settings.password)
        server.send_message(message)

    logger.info("Alert email sent to %d recipient(s).", len(settings.recipients))