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


def test_min_chars_is_checked_against_the_overrides_own_max_chars():
    ok = load('[chunking]\n"x" = { strategy = "single", max_chars = 800, min_chars = 400 }\n')
    assert ok[0].min_chars == 400
    with pytest.raises(OverrideError, match="line 2: min_chars"):
        load('[chunking]\n"x" = { strategy = "single", max_chars = 800, min_chars = 401 }\n')


def test_min_chars_without_max_chars_is_checked_against_chunk_chars():
    assert load('[chunking]\n"x" = { strategy = "single", min_chars = 600 }\n')[0].min_chars == 600
    with pytest.raises(OverrideError, match="line 2: min_chars"):
        load('[chunking]\n"x" = { strategy = "single", min_chars = 601 }\n')
    with pytest.raises(OverrideError, match="line 2: min_chars"):
        load('[chunking]\n"x" = { strategy = "single", min_chars = 1500 }\n')
    assert load('[chunking]\n"x" = { strategy = "single", min_chars = 1500 }\n', chunk_chars=3000)
    with pytest.raises(OverrideError, match="line 2"):
        load('[chunking]\n"x" = { strategy = "single", min_chars = 301 }\n', chunk_chars=600)


def test_load_file_passes_chunk_chars(tmp_path):
    (tmp_path / "ragnaroq.toml").write_text('[chunking]\n"a" = { strategy = "single", min_chars = 1500 }\n')
    with pytest.raises(OverrideError, match="line 2"):
        load_file(tmp_path / "ragnaroq.toml")
    assert load_file(tmp_path / "ragnaroq.toml", chunk_chars=4000)


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ('[chunking]\n"é/*.md" = "bad"\n', 2),
        ("[chunking]\n'a/*.md' = \"single\"\n'é' = \"bad\"\n", 3),
        ('# note\n\n[chunking]\n"a" = "single"\nnotes = "bad"\n', 5),
        ('[chunking]\n"a" = "single"\na.b = "bad"\n', 3),
        ('[chunking]\n"a" = "single"\n"é".b = "bad"\n', 3),
        ('[other]\n"x" = 1\n[chunking]\n"a" = "single"\n[chunking."pat/*.md"]\nstrategy = "bad"\n', 5),
        ('[chunking]\n"a" = "single"\n[chunking.bare]\nstrategy = "bad"\n', 3),
        ('[other]\n"x" = "bad"\n[chunking]\n"x" = "bad"\n', 4),
    ],
)
def test_the_line_of_a_bad_key_is_found_in_every_shape(text, line):
    with pytest.raises(OverrideError, match=f"^line {line}:"):
        load(text)
