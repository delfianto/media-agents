from __future__ import annotations

from mediaorganizer import oshash


def test_all_zero_file_hash_equals_size(tmp_path):
    # Every uint64 in an all-zero chunk is 0, so both checksums are 0 and
    # the hash reduces to exactly the file size -- verifiable by hand
    # without re-deriving the module's own arithmetic.
    size = oshash.MIN_FILE_SIZE + 12345
    path = tmp_path / "zeros.bin"
    path.write_bytes(b"\x00" * size)
    assert oshash.compute(path) == f"{size:016x}"


def test_overflow_wraps_correctly(tmp_path):
    # A file of all 0xFF bytes forces every uint64 value to its maximum,
    # guaranteeing the running sum overflows 64 bits well before the chunk
    # ends -- the expected result is derived independently below (not by
    # calling into oshash's own _checksum_chunk), so this actually checks
    # the wraparound behavior rather than just re-running the same code.
    data = b"\xff" * oshash.MIN_FILE_SIZE
    path = tmp_path / "allff.bin"
    path.write_bytes(data)

    values_per_chunk = oshash.CHUNK_SIZE // 8
    max_uint64 = (1 << 64) - 1
    chunk_checksum = (values_per_chunk * max_uint64) % (1 << 64)
    expected = (len(data) + 2 * chunk_checksum) % (1 << 64)

    assert oshash.compute(path) == f"{expected:016x}"


def test_below_minimum_size_returns_none(tmp_path):
    path = tmp_path / "tiny.bin"
    path.write_bytes(b"\x00" * (oshash.MIN_FILE_SIZE - 1))
    assert oshash.compute(path) is None


def test_exactly_minimum_size_is_hashable(tmp_path):
    path = tmp_path / "exact.bin"
    path.write_bytes(b"\x00" * oshash.MIN_FILE_SIZE)
    assert oshash.compute(path) == f"{oshash.MIN_FILE_SIZE:016x}"


def test_result_is_always_16_lowercase_hex_chars(tmp_path):
    path = tmp_path / "small_hash.bin"
    # A file whose size alone (with all-zero content) produces a hash value
    # small enough that naive hex formatting without zero-padding would
    # produce fewer than 16 characters -- the exact bug the spec warns
    # about ("101eae5380a4769" instead of "0101eae5380a4769").
    path.write_bytes(b"\x00" * oshash.MIN_FILE_SIZE)
    result = oshash.compute(path)
    assert result is not None
    assert len(result) == 16
    assert result == result.lower()
    int(result, 16)  # must be valid hex
