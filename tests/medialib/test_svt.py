import pytest

from psammophis.medialib import svt


class _VersionFunction:
    restype = None

    def __call__(self):
        return b"v4.1.0-test"


class _Library:
    svt_av1_get_version = _VersionFunction()


class _HdrLibrary(_Library):
    svt_hdr_get_version = _VersionFunction()


@pytest.fixture(autouse=True)
def _clear_detection_cache():
    svt.detect_svt_implementation.cache_clear()
    yield
    svt.detect_svt_implementation.cache_clear()


def _detect(monkeypatch, library):
    monkeypatch.setattr(svt, "_ffmpeg_svt_library", lambda: "libSvtAv1Enc.so.4")
    monkeypatch.setattr(svt.ctypes, "CDLL", lambda name: library)
    return svt.detect_svt_implementation()


def test_detects_mainline_from_upstream_abi(monkeypatch):
    implementation = _detect(monkeypatch, _Library())
    assert implementation.flavor == "mainline"
    assert implementation.version == "v4.1.0-test"


def test_detects_hdr_fork_from_its_extra_abi_symbol(monkeypatch):
    implementation = _detect(monkeypatch, _HdrLibrary())
    assert implementation.flavor == "svt-av1-hdr"
    assert implementation.label == "svt-av1-hdr v4.1.0-test"


def test_hdr_fork_params_select_pq_curve_without_affecting_mainline():
    fork = svt.SvtImplementation("svt-av1-hdr", "v4.1.0", "test")
    mainline = svt.SvtImplementation("mainline", "v4.1.0", "test")
    assert svt.implementation_params(fork, "smpte2084")["variance-boost-curve"] == "3"
    assert svt.implementation_params(fork, "bt709")["variance-boost-curve"] == "0"
    assert svt.implementation_params(mainline, "smpte2084") == {}
