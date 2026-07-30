from __future__ import annotations

import pytest
from medialib.dotenv import load_dotenv_file, parse_dotenv


def test_parse_dotenv_basic():
    text = "KEY_A=value_a\nKEY_B = value_b \n"
    assert parse_dotenv(text) == {"KEY_A": "value_a", "KEY_B": "value_b"}


def test_parse_dotenv_ignores_comments_and_blank_lines():
    text = "\n# a comment\nKEY=value\n   \n# KEY2=commented_out\n"
    assert parse_dotenv(text) == {"KEY": "value"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('KEY="quoted value"', "quoted value"),
        ("KEY='quoted value'", "quoted value"),
        ("KEY=unquoted value", "unquoted value"),
        ("KEY=\"mismatched'", "\"mismatched'"),
    ],
)
def test_parse_dotenv_quote_stripping(raw, expected):
    assert parse_dotenv(raw)["KEY"] == expected


def test_parse_dotenv_value_containing_equals_sign():
    assert parse_dotenv("KEY=a=b=c")["KEY"] == "a=b=c"


def test_load_dotenv_file_missing_returns_empty(tmp_path):
    assert load_dotenv_file(tmp_path / "does-not-exist.env") == {}


def test_load_dotenv_file_reads_existing_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KEY=value\n", encoding="utf-8")
    assert load_dotenv_file(env_path) == {"KEY": "value"}
