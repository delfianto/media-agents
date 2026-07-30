import pytest
from subtitle import config


def test_defaults_and_languages(tmp_path):
    path = tmp_path / ".env"
    path.write_text("SUBTITLE_LANGUAGES=en, id ,ja\n", encoding="utf-8")
    loaded = config.load_config(path)
    assert loaded.languages == ("en", "id", "ja")
    assert loaded.api_key is None


def test_invalid_server_raises(tmp_path):
    path = tmp_path / ".env"
    path.write_text("SUBTITLE_SERVER=emby\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config(path)
