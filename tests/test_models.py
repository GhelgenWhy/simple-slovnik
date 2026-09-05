"""Tests for the Word dataclass (Task 2.1)."""
from slovnik.models import Word


def test_word_creation_defaults():
    w = Word(cs="kočka")
    assert w.cs == "kočka"
    assert w.uk == "" and w.ru == "" and w.pos == "" and w.note == ""
    assert w.id is None
    assert w.created_at == "" and w.updated_at == ""


def test_word_creation_full():
    w = Word(id=7, cs="pes", uk="собака", ru="собака", pos="noun",
             note="test", created_at="2024-01-01", updated_at="2024-01-02")
    assert w.id == 7
    assert w.note == "test"


def test_word_equality_by_value():
    # dataclass auto-generates __eq__
    a = Word(cs="a", uk="b")
    b = Word(cs="a", uk="b")
    assert a == b
