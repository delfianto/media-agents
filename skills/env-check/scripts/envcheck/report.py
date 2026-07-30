"""Pure report-shaping logic over a list of CheckResult -- no I/O, so this
stays unit-testable without mocking subprocess/shutil.
"""

from __future__ import annotations

from collections import defaultdict

from .checks import CheckResult


def group_by_category(results: list[CheckResult]) -> dict[str, list[CheckResult]]:
    grouped: dict[str, list[CheckResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)
    return dict(grouped)


def missing_required(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.required and not r.found]


def missing_optional(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.required and not r.found]


def format_report(results: list[CheckResult]) -> str:
    lines: list[str] = []
    for category, category_results in sorted(group_by_category(results).items()):
        lines.append(f"[{category}]")
        for result in category_results:
            if result.found:
                mark = "OK "
                suffix = f" ({result.detail})" if result.detail else ""
            else:
                mark = "!! " if result.required else ".. "
                suffix = ""
            lines.append(f"  {mark}{result.name}{suffix}")
            if not result.found and result.install_hint:
                lines.append(f"        -> {result.install_hint}")
        lines.append("")

    missing = missing_required(results)
    if missing:
        lines.append(f"{len(missing)} required prerequisite(s) missing -- see '!!' lines above.")
    else:
        lines.append("All required prerequisites found.")
    optional = missing_optional(results)
    if optional:
        lines.append(f"{len(optional)} optional prerequisite(s) not found -- see '..' lines above.")
    return "\n".join(lines)


def exit_code(results: list[CheckResult]) -> int:
    return 1 if missing_required(results) else 0
