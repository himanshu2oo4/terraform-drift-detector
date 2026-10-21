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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
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


def parse_args() -> argparse.Namespace:
    """Read options typed by the user on the command line."""
    parser = argparse.ArgumentParser(description="Detect Terraform drift.")
    parser.add_argument(
        "--infra-dir",
        type=Path,
        default=Path("infra"),
        help="Folder containing the Terraform code (default: infra)",
    )
    return parser.parse_args()


def main() -> int:
    """Entry point. Returns 0 (no drift), 1 (error) or 2 (drift)."""
    args = parse_args()

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

    return result.status.value


if __name__ == "__main__":
    sys.exit(main())