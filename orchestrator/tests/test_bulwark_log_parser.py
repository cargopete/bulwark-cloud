"""Unit tests for BulwarkLogParser."""
from __future__ import annotations

from unittest.mock import MagicMock

from bulwark_cloud_orchestrator.bulwark_log_parser import BulwarkLogParser, _extract_tags


def test_extract_tags_basic():
    line = "[pass:2] [status:done] [duration_s:996] [findings:14]"
    tags = _extract_tags(line)
    assert tags == {"pass": "2", "status": "done", "duration_s": "996", "findings": "14"}


def test_extract_tags_empty():
    assert _extract_tags("some unstructured log line") == {}


def test_parser_running_calls_dynamo():
    dynamo = MagicMock()
    parser = BulwarkLogParser(dynamo)
    parser.handle_line("[pass:1] [status:running]")
    dynamo.update_pass.assert_called_once_with(1, status="RUNNING")


def test_parser_completed_with_tokens():
    dynamo = MagicMock()
    parser = BulwarkLogParser(dynamo)
    parser.handle_line("[pass:2] [status:done] [duration_s:996] [findings:14] [tokens_in:124032] [tokens_out:18441]")
    dynamo.update_pass.assert_called_once_with(
        2,
        status="COMPLETED",
        duration_seconds=996,
        findings_emitted=14,
        anthropic_tokens={"input": 124032, "output": 18441},
    )


def test_parser_ignores_non_pass_lines():
    dynamo = MagicMock()
    parser = BulwarkLogParser(dynamo)
    parser.handle_line("Building packages/horizon...")
    dynamo.update_pass.assert_not_called()
