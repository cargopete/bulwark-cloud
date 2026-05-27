"""Parse Bulwark's structured stdout progress lines.

Bulwark emits lines like:
    [pass:1] [status:done] [duration_s:62]
    [pass:2] [status:running] [agent:red]
    [pass:2] [status:done] [duration_s:996] [findings:14] [tokens_in:124032] [tokens_out:18441]

The parser extracts these tags and updates DynamoDB pass sub-records.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from .dynamodb_writer import DynamoWriter

log = structlog.get_logger()

# Matches key:value tags within square brackets
_TAG_RE = re.compile(r"\[(\w+):([^\]]+)\]")


def _extract_tags(line: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _TAG_RE.finditer(line)}


class BulwarkLogParser:
    def __init__(self, dynamo: DynamoWriter) -> None:
        self._dynamo = dynamo

    def handle_line(self, line: str) -> None:
        if "[pass:" not in line:
            return

        tags = _extract_tags(line)
        pass_number_str = tags.get("pass")
        if not pass_number_str:
            return

        try:
            pass_number = int(pass_number_str)
        except ValueError:
            return

        status = tags.get("status", "running")

        try:
            if status == "running":
                self._dynamo.update_pass(pass_number, status="RUNNING")
            elif status in ("done", "completed"):
                self._dynamo.update_pass(
                    pass_number,
                    status="COMPLETED",
                    duration_seconds=int(tags["duration_s"]) if "duration_s" in tags else None,
                    findings_emitted=int(tags["findings"]) if "findings" in tags else None,
                    anthropic_tokens=(
                        {
                            "input": int(tags["tokens_in"]),
                            "output": int(tags["tokens_out"]),
                        }
                        if "tokens_in" in tags
                        else None
                    ),
                )
            elif status == "failed":
                self._dynamo.update_pass(pass_number, status="FAILED")
        except Exception as exc:
            # Non-fatal: log and continue streaming
            log.warning("pass_update_failed", pass_number=pass_number, error=str(exc))
