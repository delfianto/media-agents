from __future__ import annotations

import subprocess

from psammophis.medialib import gpu


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def test_list_gpu_indices_parses_real_nvidia_smi_output(monkeypatch):
    # Real `nvidia-smi --query-gpu=index --format=csv,noheader` output shape
    # on this machine: one bare digit per line, no header (noheader).
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "nvidia-smi"
        return _FakeCompleted(returncode=0, stdout="0\n1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.list_gpu_indices() == [0, 1]


def test_list_gpu_indices_returns_empty_when_nvidia_smi_missing(monkeypatch):
    # Real case on any machine with no NVIDIA driver installed at all.
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.list_gpu_indices() == []


def test_list_gpu_indices_returns_empty_on_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.list_gpu_indices() == []


def test_list_gpu_indices_returns_empty_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.list_gpu_indices() == []


def test_list_gpu_indices_ignores_blank_lines(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=0, stdout="0\n\n2\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.list_gpu_indices() == [0, 2]


def test_gpu_supports_av1_nvenc_true_on_zero_exit(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert "{gpu}" not in " ".join(cmd)  # format() already substituted the index
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.gpu_supports_av1_nvenc(0) is True


def test_gpu_supports_av1_nvenc_false_on_nonzero_exit(monkeypatch):
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.gpu_supports_av1_nvenc(1) is False


def test_gpu_supports_av1_nvenc_false_on_missing_ffmpeg(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.gpu_supports_av1_nvenc(0) is False


def test_gpu_supports_av1_nvenc_false_on_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert gpu.gpu_supports_av1_nvenc(0) is False


def test_detect_av1_nvenc_gpu_returns_first_capable_index(monkeypatch):
    monkeypatch.setattr(gpu, "list_gpu_indices", lambda: [0, 1])
    monkeypatch.setattr(gpu, "gpu_supports_av1_nvenc", lambda index: index == 1)
    assert gpu.detect_av1_nvenc_gpu() == 1


def test_detect_av1_nvenc_gpu_returns_none_when_no_gpu_capable(monkeypatch):
    monkeypatch.setattr(gpu, "list_gpu_indices", lambda: [0, 1])
    monkeypatch.setattr(gpu, "gpu_supports_av1_nvenc", lambda index: False)
    assert gpu.detect_av1_nvenc_gpu() is None


def test_detect_av1_nvenc_gpu_returns_none_when_no_gpus(monkeypatch):
    monkeypatch.setattr(gpu, "list_gpu_indices", lambda: [])
    assert gpu.detect_av1_nvenc_gpu() is None
