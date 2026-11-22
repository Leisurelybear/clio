"""Regression tests: inline comments are not part of .env values."""

from __future__ import annotations

import pytest

from clio.config.loader import parse_dotenv


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("KEY=abc  # note", "abc"),
        ("KEY=abc\t# note", "abc"),
        ('KEY="abc" # note', "abc"),
        ("KEY='a b' # note", "a b"),
        ("KEY=abc#def", "abc#def"),
        ('KEY="a # b"', "a # b"),
        ('KEY="x\\"y" # note', 'x"y'),
        ("KEY=  # only a comment", ""),
        ('KEY="unterminated # note', '"unterminated # note'),
    ],
)
def test_inline_comment_is_dropped(line, expected):
    assert parse_dotenv(line + "\n") == {"KEY": expected}
