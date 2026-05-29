"""Regression tests for severity normalisation in the IndexFindings lambda.

A finding the PoC gate rejects is emitted by bulwark with its severity field
overloaded as a status string (e.g. "INVALID - FALSE POSITIVE"). Such values
must normalise to None so they are neither indexed nor counted — otherwise the
API's Audit model rejects the resulting severity histogram with a 500.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handler import _normalise_severity  # noqa: E402


def test_titlecase_severity_uppercased():
    assert _normalise_severity("Critical") == "CRITICAL"
    assert _normalise_severity("high") == "HIGH"


def test_informational_maps_to_info():
    assert _normalise_severity("Informational") == "INFO"
    assert _normalise_severity("INFO") == "INFO"


def test_dismissed_finding_is_rejected():
    assert _normalise_severity("INVALID - FALSE POSITIVE") is None


def test_unknown_severity_is_rejected():
    assert _normalise_severity("") is None
    assert _normalise_severity("bogus") is None
