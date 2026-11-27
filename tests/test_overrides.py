import pytest

from ragnaroq.semantic.overrides import Override, OverrideError, load, load_file, match


def test_patterns_and_strategies_load_in_file_order():
    text = '[chunking]\n"faq/*.pdf" = "question-answer"\n"notes/*.md" = { strategy = "sentences", max_chars = 800, min_chars = 100 }\n'
    assert load(text) == [
        Override("faq/*.pdf", "question-answer"),
        Override("notes/*.md", "sentences", 800, 100),
    ]


def test_an_unknown_strategy_names_its_line():
    with pytest.raises(OverrideError, match="line 3: unknown strategy 'faqs'"):
        load('[chunking]\n"a/*.md" = "sentences"\n"faq/*.pdf" = "faqs"\n')


def test_keys_and_limits_are_checked():
    for bad in (
        '{ strategy = "sentences", rows = 2 }',
        '{ strategy = "sentences", max_chars = 100 }',
        '{ strategy = "sentences", max_chars = 800, min_chars = 500 }',
        '{ strategy = "sentences", max_chars = "big" }',
        "{ max_chars = 800 }",
        "3",
    ):
        with pytest.raises(OverrideError, match="line 2"):
            load(f'[chunking]\n"x/*.md" = {bad}\n')


def test_min_chars_may_be_zero_and_half_of_max_chars():
    got = load(
        '[chunking]\n"x" = { strategy = "single", max_chars = 800, min_chars = 400 }\n"y" = { strategy = "single", min_chars = 0 }\n'
    )
    assert [o.min_chars for o in got] == [400, 0]


def test_invalid_toml_stops_with_its_line():
    with pytest.raises(OverrideError, match="line 2"):
        load('[chunking]\n"x" = = 1\n')


def test_a_file_without_a_chunking_table_has_no_overrides():
    assert load("[other]\nx = 1\n") == []


def test_a_missing_file_has_no_overrides(tmp_path):
    assert load_file(tmp_path / "ragnaroq.toml") == []
    (tmp_path / "ragnaroq.toml").write_text('[chunking]\n"a" = "single"\n')
    assert load_file(tmp_path / "ragnaroq.toml") == [Override("a", "single")]


def test_the_first_matching_pattern_wins():
    found = load('[chunking]\n"faq/*.pdf" = "question-answer"\n"*.pdf" = "sentences"\n')
    assert match(found, "faq/a.pdf").strategy == "question-answer"
    assert match(found, "other/a.pdf").strategy == "sentences"
    assert match(found, "a.md") is None
    assert match(found, "FAQ/a.pdf").strategy == "sentences"


def test_the_key_is_stable_json_of_the_fields():
    assert (
        Override("a", "single", 800).key()
        == '{"max_chars": 800, "min_chars": null, "pattern": "a", "strategy": "single"}'
    )
