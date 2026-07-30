from __future__ import annotations

from av1transcode import config

# parse_dotenv itself is tested once, centrally, in lib/test_dotenv.py --
# these tests only cover load_config's own behavior (required/optional keys,
# defaults, env-vs-file precedence).


def _write_env(tmp_path, contents):
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")
    return env_path


def test_load_config_defaults_with_no_env_file(tmp_path):
    cfg = config.load_config(tmp_path / "does-not-exist.env")
    assert cfg.output_dir is None
    assert cfg.audio_lang == "eng"
    assert cfg.subtitle_lang == "eng"
    assert cfg.max_bitrate_fraction == config.MAX_BITRATE_FRACTION_OF_SOURCE


def test_load_config_reads_values_from_dotenv(tmp_path):
    env_path = _write_env(
        tmp_path,
        "AV1TRANSCODE_OUTPUT_DIR=/converted\n"
        "AV1TRANSCODE_AUDIO_LANG=jpn\n"
        "AV1TRANSCODE_SUBTITLE_LANG=all\n"
        "AV1TRANSCODE_MAX_BITRATE_FRACTION=0.5\n",
    )
    cfg = config.load_config(env_path)
    assert str(cfg.output_dir) == "/converted"
    assert cfg.audio_lang == "jpn"
    assert cfg.subtitle_lang == "all"
    assert cfg.max_bitrate_fraction == 0.5


def test_real_environment_overrides_dotenv_file(tmp_path, monkeypatch):
    env_path = _write_env(tmp_path, "AV1TRANSCODE_AUDIO_LANG=jpn\n")
    monkeypatch.setenv("AV1TRANSCODE_AUDIO_LANG", "spa")
    cfg = config.load_config(env_path)
    assert cfg.audio_lang == "spa"
