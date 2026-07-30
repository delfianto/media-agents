import json
import subprocess

from mkvedit.command import Edits
from mkvedit.runner import apply


def _json():
    return json.dumps(
        {
            "tracks": [
                {
                    "id": 0,
                    "type": "video",
                    "properties": {"uid": 100, "default_track": True},
                }
            ],
            "attachments": [],
        }
    )


class FakeRun:
    def __init__(self, edit_returncode=0, invalid_after=False):
        self.edit_returncode = edit_returncode
        self.invalid_after = invalid_after
        self.calls = []
        self.inspections = 0

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == "mkvmerge":
            self.inspections += 1
            stdout = "invalid" if self.invalid_after and self.inspections > 1 else _json()
            return subprocess.CompletedProcess(args, 0, stdout, "")
        return subprocess.CompletedProcess(args, self.edit_returncode, "", "edit failed")


def test_dry_run_does_not_create_backup(tmp_path):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"original")
    fake = FakeRun()
    result = apply(path, Edits(title="New"), yes=False, run=fake)
    assert result.status == "planned"
    assert not (tmp_path / "movie.mkv.mkvedit.bak").exists()
    assert len(fake.calls) == 1


def test_success_creates_backup(tmp_path):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"original")
    result = apply(path, Edits(title="New"), yes=True, run=FakeRun())
    assert result.status == "edited"
    assert result.backup is not None
    assert result.backup.read_bytes() == b"original"


def test_validation_failure_restores_original(tmp_path):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"original")

    class MutatingRun(FakeRun):
        def __call__(self, args, **kwargs):
            result = super().__call__(args, **kwargs)
            if args[0] == "mkvpropedit":
                path.write_bytes(b"damaged")
            return result

    result = apply(
        path,
        Edits(title="New"),
        yes=True,
        run=MutatingRun(invalid_after=True),
    )
    assert result.status == "error"
    assert "restored" in result.detail
    assert path.read_bytes() == b"original"
