"""Terraform drift detector: runs 'terraform plan' and reports what drifted."""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import smtplib
from notifier import ConfigError, EmailSettings, load_email_settings, send_email
from dotenv import load_dotenv
import io 

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("drift-detector")


class DriftStatus(Enum):
    """The three possible outcomes, matching Terraform's exit codes."""

    NO_DRIFT = 0
    ERROR = 1
    DRIFT = 2


@dataclass
class AttributeChange:
    """One setting that changed, e.g. tags.Owner went from 'a' to 'b'."""

    path: str
    before: Any
    after: Any


@dataclass
class DriftedResource:
    """One cloud resource that drifted, plus the list of its changes."""

    address: str
    changes: list[AttributeChange] = field(default_factory=list)


@dataclass
class DriftResult:
    """Holds the outcome of one drift check."""

    status: DriftStatus
    output: str = ""
    drifted: list[DriftedResource] = field(default_factory=list)


def run_command(command: list[str], cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess:
    """Run one command and capture its output."""
    logger.info("Running: %s", " ".join(command))
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def find_differences(before: Any, after: Any, path: str = "") -> list[AttributeChange]:
    """Compare two values and list every difference (digs into nested dicts)."""
    changes: list[AttributeChange] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else key
            changes.extend(find_differences(before.get(key), after.get(key), child_path))
    elif before != after:
        changes.append(AttributeChange(path, before, after))
    return changes


def extract_drift(plan_json: dict) -> list[DriftedResource]:
    """Read the 'resource_drift' section of the plan JSON."""
    drifted = []
    for item in plan_json.get("resource_drift", []):
        change = item.get("change", {})
        differences = find_differences(change.get("before"), change.get("after"))
        drifted.append(DriftedResource(item.get("address", "unknown"), differences))
    return drifted


def run_drift_check(infra_dir: Path, timeout_seconds: int = 600) -> DriftResult:
    """Run plan, then convert it to JSON and find what drifted."""
    with tempfile.TemporaryDirectory() as temp_dir:
        plan_file = Path(temp_dir) / "tfplan"
        plan_command = [
            "terraform",
            "plan",
            "-detailed-exitcode",
            "-input=false",
            "-lock=false",
            "-no-color",
            f"-out={plan_file}",
        ]
        try:
            plan = run_command(plan_command, infra_dir, timeout_seconds)

            if plan.returncode == 0:
                return DriftResult(DriftStatus.NO_DRIFT, plan.stdout)
            if plan.returncode != 2:
                return DriftResult(DriftStatus.ERROR, plan.stderr or plan.stdout)

            show = run_command(
                ["terraform", "show", "-json", str(plan_file)], infra_dir, timeout_seconds
            )
            if show.returncode != 0:
                return DriftResult(DriftStatus.ERROR, show.stderr)

            plan_json = json.loads(show.stdout)
        except FileNotFoundError:
            return DriftResult(DriftStatus.ERROR, "Terraform is not installed or not in PATH.")
        except subprocess.TimeoutExpired:
            return DriftResult(DriftStatus.ERROR, f"Terraform timed out after {timeout_seconds}s.")
        except json.JSONDecodeError:
            return DriftResult(DriftStatus.ERROR, "Could not read Terraform's JSON output.")

    return DriftResult(DriftStatus.DRIFT, plan.stdout, extract_drift(plan_json))


def report(result: DriftResult) -> None:
    """Print a human-friendly summary of the drift."""
    if not result.drifted:
        logger.warning(
            "Terraform wants changes, but nothing was changed outside Terraform. "
            "This usually means your .tf code has pending edits."
        )
        return
    for resource in result.drifted:
        logger.warning("Drifted resource: %s", resource.address)
        for change in resource.changes:
            name = change.path or "(the whole resource)"
            logger.warning("    %s: expected %r, found %r", name, change.before, change.after)

def build_email(result: DriftResult) -> tuple[str, str]:
    """Create the email subject and body. Lists names only, never values."""
    if result.status is DriftStatus.ERROR:
        subject = "[ERROR] Terraform drift detector failed to run"
        body = "The drift check could not complete. Check the detector logs for details."
        return subject, body

    if not result.drifted:
        subject = "[NOTICE] Terraform plan has pending changes"
        body = (
            "Terraform wants to make changes, but nothing was modified outside Terraform.\n"
            "Likely cause: the .tf code was edited and not yet applied."
        )
        return subject, body

    subject = f"[ALERT] Terraform drift detected in {len(result.drifted)} resource(s)"
    lines = ["Infrastructure no longer matches the Terraform code.", ""]
    for resource in result.drifted:
        lines.append(f"Resource: {resource.address}")
        for change in resource.changes:
            lines.append(f"  - changed setting: {change.path or '(whole resource)'}")
    lines += ["", "Values are hidden on purpose. See the detector logs for full details."]
    return subject, "\n".join(lines)


def send_alert(result: DriftResult, log_text: str | None = None) -> bool:
    """Try to email the result. Returns True if sent. Never crashes the detector."""
    try:
        settings: EmailSettings = load_email_settings()
    except ConfigError as error:
        logger.error("Email not configured: %s", error)
        return False

    subject, body = build_email(result)
    try:
        send_email(settings, subject, body, attachment=log_text)
    except smtplib.SMTPAuthenticationError:
        logger.error("Email login failed. Check SMTP_USERNAME and SMTP_PASSWORD.")
        return False
    except (smtplib.SMTPException, OSError) as error:
        logger.error("Could not send email: %s", error)
        return False
    return True


def start_log_capture() -> io.StringIO:
    """Also copy every log line into memory, so we can email it later."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    return buffer


def last_log_lines(buffer: io.StringIO, max_lines: int = 200) -> str:
    """Return only the most recent log lines."""
    lines = buffer.getvalue().splitlines()
    return "\n".join(lines[-max_lines:])





def parse_args() -> argparse.Namespace:
    """Read options typed by the user on the command line."""
    parser = argparse.ArgumentParser(description="Detect Terraform drift.")
    parser.add_argument(
        "--infra-dir",
        type=Path,
        default=Path("infra"),
        help="Folder containing the Terraform code (default: infra)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send an email alert when drift or an error is found",
    )

    parser.add_argument(
        "--attach-logs",
        action="store_true",
        help="Attach the latest detector logs to the alert email (contains values!)",
    )
    return parser.parse_args()

def main() -> int:
    """Entry point. Returns 0 (no drift), 1 (error) or 2 (drift)."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    args = parse_args()
    log_buffer = start_log_capture()
    if not args.infra_dir.is_dir():
        logger.error("Folder not found: %s", args.infra_dir)
        return DriftStatus.ERROR.value

    result = run_drift_check(args.infra_dir)

    if result.status is DriftStatus.NO_DRIFT:
        logger.info("No drift detected. Infrastructure matches code.")
    elif result.status is DriftStatus.DRIFT:
        logger.warning("DRIFT DETECTED in %d resource(s).", len(result.drifted))
        report(result)
    else:
        logger.error("Terraform failed:\n%s", result.output)

    if args.notify and result.status is not DriftStatus.NO_DRIFT:
        log_text = last_log_lines(log_buffer) if args.attach_logs else None
        send_alert(result, log_text)

    return result.status.value


if __name__ == "__main__":
    sys.exit(main())