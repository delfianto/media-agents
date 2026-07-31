"""Launcher contract tests for root run.sh.

These exercise the shell bootstrap only (env, MEDIALIB_ROOT, argument
forwarding, exit codes) by stubbing `uv` so no full application import is
required for every case.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SH = REPO_ROOT / "run.sh"


def _write_stub_uv(bin_dir: Path, script: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(script)
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    path_prepend: Path,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    full_env.update(env)
    full_env["PATH"] = f"{path_prepend}:{full_env.get('PATH', '')}"
    # Drop real MEDIALIB_ROOT unless the test set one.
    if "MEDIALIB_ROOT" not in env:
        full_env.pop("MEDIALIB_ROOT", None)
    return subprocess.run(
        [str(RUN_SH), *args],
        cwd=cwd,
        env=full_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_forwards_args_and_project_root(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'argv:'
            printf ' %q' "$@"
            printf '\\n'
            printf 'cwd=%s\\n' "$PWD"
            """
        ),
    )
    result = _run(
        ["env-check", "--help"],
        cwd=tmp_path,
        env={},
        path_prepend=bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert f"--project {REPO_ROOT}" in result.stdout
    assert "psammophis" in result.stdout
    assert "env-check" in result.stdout
    assert "--help" in result.stdout
    assert f"cwd={tmp_path}" in result.stdout


def test_preserves_preexisting_medialib_root(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'MEDIALIB_ROOT=%s\\n' "${MEDIALIB_ROOT-}"
            """
        ),
    )
    result = _run(
        ["--version"],
        cwd=tmp_path,
        env={"MEDIALIB_ROOT": "/already/set"},
        path_prepend=bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "MEDIALIB_ROOT=/already/set" in result.stdout


def test_sets_medialib_root_when_agents_symlink(tmp_path):
    media = tmp_path / "media library"
    media.mkdir()
    agents = media / ".agents"
    agents.symlink_to(REPO_ROOT)
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'MEDIALIB_ROOT=%s\\n' "${MEDIALIB_ROOT-}"
            printf 'project='
            # print --project value
            while (( $# )); do
              if [[ $1 == --project ]]; then
                printf '%s\\n' "$2"
                break
              fi
              shift
            done
            """
        ),
    )
    result = _run(
        ["env-check"],
        cwd=media,
        env={},
        path_prepend=bin_dir,
    )
    # Invoke through the symlink path, not REPO_ROOT/run.sh directly.
    full_env = os.environ.copy()
    full_env["PATH"] = f"{bin_dir}:{full_env.get('PATH', '')}"
    full_env.pop("MEDIALIB_ROOT", None)
    result = subprocess.run(
        [str(agents / "run.sh"), "env-check"],
        cwd=media,
        env=full_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"MEDIALIB_ROOT={media}" in result.stdout
    # Logical project path should be the .agents symlink path.
    assert str(agents) in result.stdout


def test_propagates_exit_code(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            exit 42
            """
        ),
    )
    result = _run(["analyze"], cwd=tmp_path, env={}, path_prepend=bin_dir)
    assert result.returncode == 42


def test_handles_args_with_spaces(tmp_path):
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf '%s\\n' "$@"
            """
        ),
    )
    result = _run(
        ["analyze", "--path", "Some Movie (2020)"],
        cwd=tmp_path,
        env={},
        path_prepend=bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "Some Movie (2020)" in result.stdout


def test_envrc_is_auto_exported_without_changing_caller_cwd(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    checkout.mkdir()
    launcher = checkout / "run.sh"
    shutil.copy2(RUN_SH, launcher)
    (checkout / ".envrc").write_text("FROM_ENVRC=available\ncd /\n")
    caller = tmp_path / "caller"
    caller.mkdir()
    bin_dir = tmp_path / "bin"
    _write_stub_uv(
        bin_dir,
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf 'FROM_ENVRC=%s\n' "${FROM_ENVRC-}"
            printf 'cwd=%s\n' "$PWD"
            printf 'project=%s\n' "$3"
            """
        ),
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env.pop("MEDIALIB_ROOT", None)
    result = subprocess.run(
        [str(launcher), "env-check"],
        cwd=caller,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "FROM_ENVRC=available" in result.stdout
    assert f"cwd={caller}" in result.stdout
    assert f"project={checkout}" in result.stdout
