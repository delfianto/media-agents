from __future__ import annotations

from psammophis.envcheck.checks import CheckResult
from psammophis.envcheck.report import (
    exit_code,
    format_report,
    group_by_category,
    missing_optional,
    missing_required,
)


def _result(name, category, required, found, detail="", install_hint=""):
    return CheckResult(name, category, required, found, detail, install_hint)


def test_group_by_category_groups_and_preserves_order_within_group():
    results = [
        _result("ffmpeg", "shared", True, True),
        _result("mkvmerge", "media-library", True, True),
        _result("ffprobe", "shared", True, True),
    ]
    grouped = group_by_category(results)
    assert list(grouped.keys()) == ["shared", "media-library"]
    assert [r.name for r in grouped["shared"]] == ["ffmpeg", "ffprobe"]


def test_missing_required_only_returns_required_and_missing():
    results = [
        _result("ffmpeg", "shared", True, True),
        _result("nvencc", "transcode", False, False),
        _result("mkvmerge", "media-library", True, False),
    ]
    assert [r.name for r in missing_required(results)] == ["mkvmerge"]


def test_missing_optional_only_returns_optional_and_missing():
    results = [
        _result("ffmpeg", "shared", True, True),
        _result("nvencc", "transcode", False, False),
        _result("mkvmerge", "media-library", True, False),
    ]
    assert [r.name for r in missing_optional(results)] == ["nvencc"]


def test_exit_code_zero_when_nothing_required_missing():
    results = [
        _result("ffmpeg", "shared", True, True),
        _result("nvencc", "transcode", False, False),
    ]
    assert exit_code(results) == 0


def test_exit_code_one_when_a_required_check_is_missing():
    results = [_result("mkvmerge", "media-library", True, False)]
    assert exit_code(results) == 1


def test_format_report_marks_missing_required_and_includes_install_hint():
    results = [_result("mkvmerge", "media-library", True, False, install_hint="install mkvtoolnix")]
    report = format_report(results)
    assert "!!" in report
    assert "install mkvtoolnix" in report
    assert "1 required prerequisite(s) missing" in report


def test_format_report_marks_missing_optional_differently_from_required():
    results = [_result("nvencc", "transcode", False, False, install_hint="optional extra")]
    report = format_report(results)
    assert ".." in report
    assert "1 optional prerequisite(s) not found" in report
    assert "required prerequisite(s) missing" not in report


def test_format_report_all_found_says_so():
    results = [_result("ffmpeg", "shared", True, True, detail="ffmpeg version n8.1.2")]
    report = format_report(results)
    assert "All required prerequisites found." in report
    assert "ffmpeg version n8.1.2" in report
