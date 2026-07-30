from organize.plan import Plan, execute_plan


def test_overwrite_backs_up_existing_destination(tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "Movies" / "movie.mkv"
    destination.parent.mkdir()
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    result = execute_plan(
        Plan("movie", source, 1, destination),
        overwrite=True,
    )
    assert result.status == "moved"
    assert destination.read_bytes() == b"new"
    assert result.backup is not None
    assert result.backup.read_bytes() == b"old"


def test_existing_destination_is_refused_by_default(tmp_path):
    source = tmp_path / "incoming.mkv"
    destination = tmp_path / "movie.mkv"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")
    result = execute_plan(Plan("movie", source, 1, destination))
    assert result.status == "error"
    assert source.exists()
    assert destination.read_bytes() == b"old"
