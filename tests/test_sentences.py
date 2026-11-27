import time

from ragnaroq.chunking.sentences import Cut, pack, sentences


def parts(text, breaks=()):
    return [text[a:b].strip() for a, b in sentences(text, breaks)]


def test_abbreviations_initials_and_numbers_do_not_end_a_sentence():
    text = "Under Sec. 10(46). the limit is Rs. 1.5 lakh, e.g. for Mr. A. Kumar. Next one."
    assert parts(text) == [
        "Under Sec. 10(46). the limit is Rs. 1.5 lakh, e.g. for Mr. A. Kumar.",
        "Next one.",
    ]


def test_a_lower_case_start_is_not_a_new_sentence_but_a_danda_is():
    text = "see p. 4. then more। अगला वाक्य॥ end"
    assert parts(text) == ["see p. 4. then more।", "अगला वाक्य॥", "end"]


def test_ranges_are_contiguous_and_cover_the_text():
    text = '  One. Two?  Three; (Four) "Five"  '
    ranges = sentences(text)
    assert ranges[0][0] == 0 and ranges[-1][1] == len(text)
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False))
    assert sentences("") == []
    assert sentences("no end here") == [(0, 11)]


def test_closing_quote_belongs_to_the_sentence_and_months_are_abbreviations():
    assert parts("He said 'go.' Then left. In Sept. 2020 it ended.") == [
        "He said 'go.'",
        "Then left.",
        "In Sept. 2020 it ended.",
    ]
    assert parts("Wait! Really? Yes; 5 is next.") == ["Wait!", "Really?", "Yes;", "5 is next."]


def test_breaks_start_a_sentence():
    text = "item one item two"
    assert parts(text, [0, 9]) == ["item one", "item two"]


def test_overlap_exact():
    text = "Aa aa. Bb bb. Cc cc. Dd dd."
    ranges = sentences(text)
    cuts = pack(text, ranges, 14)
    assert [text[c.start : c.end].strip() for c in cuts] == [
        "Aa aa. Bb bb.",
        "Bb bb. Cc cc.",
        "Cc cc. Dd dd.",
    ]
    cuts = pack(text, ranges, 14, overlap=False)
    assert [text[c.start : c.end].strip() for c in cuts] == ["Aa aa. Bb bb.", "Cc cc. Dd dd."]
    assert cuts[0].end == cuts[1].start


def test_no_overlap_when_it_would_leave_no_room():
    text = "Aaaaaaaaa. Bbbbbbbbb. Cccccccccc."
    cuts = pack(text, sentences(text), 21)
    assert [text[c.start : c.end].strip() for c in cuts] == ["Aaaaaaaaa. Bbbbbbbbb.", "Cccccccccc."]


def test_a_word_longer_than_max_chars_is_cut_at_the_limit_and_marked_long():
    cuts = pack("x" * 5000, [(0, 5000)], 1200)
    assert [(c.end - c.start, c.long) for c in cuts] == [(1200, True)] * 4 + [(200, True)]


def test_long_sentence_is_cut_at_a_space():
    text = "word " * 50 + "end."
    cuts = pack(text, sentences(text), 60)
    assert all(c.long for c in cuts)
    assert all(len(text[c.start : c.end].rstrip()) <= 60 for c in cuts)
    assert cuts[0].start == 0 and cuts[-1].end == len(text)
    assert all(a.end == b.start for a, b in zip(cuts, cuts[1:], strict=False))
    assert all(text[c.start : c.end].strip().split(" ")[0] in ("word", "end.") for c in cuts)


def test_a_protected_sentence_may_overrun_by_half():
    text = "Short one. " + "R" * 25 + " tail."
    ranges = sentences(text)
    assert len(ranges) == 2
    plain = pack(text, ranges, 40, overlap=False)
    assert len(plain) == 2
    kept = pack(text, ranges, 40, protected=frozenset({1}), overlap=False)
    assert [c.long for c in kept] == [False]
    too_long = pack(text, ranges, 20, protected=frozenset({1}), overlap=False)
    assert any(c.long for c in too_long)
    assert Cut(0, 1, False).long is False


def test_sentences_and_pack_are_linear():
    text = "A b. " * 100_000
    started = time.perf_counter()
    pack(text, sentences(text), 1200)
    assert time.perf_counter() - started < 3


def test_pack_repeats_one_sentence_and_never_cuts_one():
    text = "Aaaa aaa. Bbbb bbb. Cccc ccc. Dddd ddd."
    cuts = pack(text, sentences(text), 20)
    got = [text[c.start : c.end].strip() for c in cuts]
    assert got == ["Aaaa aaa. Bbbb bbb.", "Bbbb bbb. Cccc ccc.", "Cccc ccc. Dddd ddd."]
    assert all(len(g) <= 20 and not c.long for g, c in zip(got, cuts, strict=True))


def test_long_runs_stay_linear_without_sentence_ends_or_spaces():
    for text in ("x" * 300_000, " " * 300_000, "a." * 150_000):
        started = time.perf_counter()
        pack(text, sentences(text), 1200)
        assert time.perf_counter() - started < 3
