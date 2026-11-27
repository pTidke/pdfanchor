import time

import pytest

from ragnaroq.semantic.find import Match, amounts, dates, file_mentions, periods, references


@pytest.mark.parametrize(
    "text, value",
    [
        ("on 5th January, 2026", "2026-01-05"),
        ("January 5, 2026", "2026-01-05"),
        ("on 1 Sept 2026", "2026-09-01"),
        ("2026-01-05", "2026-01-05"),
        ("13/01/2026", "2026-01-13"),
        ("01/13/2026", "2026-01-13"),
        ("05/05/2026", "2026-05-05"),
        ("05/01/2026", ""),
        ("31 February 2026", ""),
        ("31/02/2026", ""),
        ("2026-02-30", ""),
    ],
)
def test_dates(text, value):
    assert [m.value for m in dates(text)] == [value]


def test_dates_ground_to_text_and_are_sorted():
    text = "Signed 5th January, 2026 and again 2026-03-01."
    found = dates(text)
    assert [m.text for m in found] == ["5th January, 2026", "2026-03-01"]
    assert all(text[m.start : m.end] == m.text for m in found)
    assert [m.start for m in found] == sorted(m.start for m in found)


def test_dates_ignore_non_dates():
    assert dates("pages 12/2026 and 1.2.3 and version 2026-27") == []


@pytest.mark.parametrize(
    "text, value",
    [
        ("for 2026-27", "2026/2027"),
        ("for 2026–2027", "2026/2027"),
        ("FY 2025-26", "2025-04-01/2026-03-31"),
        ("AY 2026-27", "2026-04-01/2027-03-31"),
        ("the financial year 2025-26", "2025-04-01/2026-03-31"),
        ("assessment year 2026-27", "2026-04-01/2027-03-31"),
        ("Q3 2026", "2026-Q3"),
        ("H1 2026", "2026-H1"),
        ("calendar year 2026", "2026/2026"),
    ],
)
def test_periods(text, value):
    assert [m.value for m in periods(text)] == [value]


@pytest.mark.parametrize(
    "text", ["pages 12-27", "see 2026-01-05", "ref 2026-28", "codes 1850-51", "pages 2026/27 only"]
)
def test_periods_ignore_non_years(text):
    assert periods(text) == []


def test_period_text_covers_the_whole_form():
    (m,) = periods("in FY 2025-26 we")
    assert m.text == "FY 2025-26"


def test_amounts_read_indian_grouping_and_scale_words():
    assert [m.value for m in amounts("₹1,50,000 and Rs. 2 lakh and 5%")] == [
        {"amount": 150000, "currency": "INR"},
        {"amount": 200000, "currency": "INR"},
        {"percent": 5},
    ]


@pytest.mark.parametrize(
    "text, value",
    [
        ("$150,000", {"amount": 150000, "currency": "USD"}),
        ("USD 1.5 million", {"amount": 1500000, "currency": "USD"}),
        ("€2 billion", {"amount": 2000000000, "currency": "EUR"}),
        ("£12.50", {"amount": 12.5, "currency": "GBP"}),
        ("INR 3 crore", {"amount": 30000000, "currency": "INR"}),
        ("100 GBP", {"amount": 100, "currency": "GBP"}),
        ("12.5 per cent", {"percent": 12.5}),
        ("7 percent", {"percent": 7}),
        ("Rs.1,500", {"amount": 1500, "currency": "INR"}),
    ],
)
def test_amounts(text, value):
    assert [m.value for m in amounts(text)] == [value]


def test_amounts_need_a_currency_or_percent():
    assert amounts("there are 150 items and 2 lakh people") == []


def test_amount_text_and_offsets():
    text = "paid Rs. 2 lakh today"
    (m,) = amounts(text)
    assert m.text == "Rs. 2 lakh" and text[m.start : m.end] == m.text


def test_references():
    assert [m.value for m in references("under section 10(46) and Notification No. 1/2026")] == [
        "section 10(46)",
        "notification 1/2026",
    ]


@pytest.mark.parametrize(
    "text, value",
    [
        ("s. 3", "section 3"),
        ("Section 21A", "section 21A"),
        ("Article 21", "article 21"),
        ("Rule 3", "rule 3"),
        ("clause (b)", "clause (b)"),
        ("Regulation 4", "regulation 4"),
        ("paragraph 2", "paragraph 2"),
        ("Chapter IV", "chapter IV"),
        ("Part  II", "part II"),
        ("SCHEDULE II", "schedule II"),
        ("Circular 7/2026", "circular 7/2026"),
        ("Order No. 12/2025", "order 12/2025"),
        ("the Income-tax Act, 1961", "income-tax act, 1961"),
        ("Under the Prevention of Money-laundering Act 2002", "prevention of money-laundering act, 2002"),
        ("see https://example.com/a/b.html.", "https://example.com/a/b.html"),
        ("see www.example.org/x, then", "www.example.org/x"),
    ],
)
def test_reference_values(text, value):
    assert [m.value for m in references(text)] == [value]


def test_references_ignore_prose():
    assert references("this part of the rule is a section of town") == []


def test_url_swallows_inner_reference():
    found = references("http://x.org/section-3 and rule 4")
    assert [m.value for m in found] == ["http://x.org/section-3", "rule 4"]


def test_file_mentions():
    found = file_mentions("see report.pdf and docs/notes/a-b.md, not file.exe or x.pdfx")
    assert [(m.text, m.value) for m in found] == [("report.pdf", ""), ("docs/notes/a-b.md", "")]


def test_finders_return_no_overlaps_sorted():
    text = "FY 2025-26: Rs. 5 lakh at 12% under section 3 on 2026-01-05, see a.pdf"
    for finder in (dates, periods, amounts, references, file_mentions):
        found = finder(text)
        assert found == sorted(found, key=lambda m: m.start)
        assert all(a.end <= b.start for a, b in zip(found, found[1:], strict=False))


def test_match_is_frozen():
    with pytest.raises(AttributeError):
        Match("a", "", 0, 1).text = "b"


def test_finders_are_linear_on_hostile_text():
    started = time.perf_counter()
    for finder in (dates, periods, amounts, references, file_mentions):
        finder("1" * 200_000 + "/" + "a." * 100_000)
    assert time.perf_counter() - started < 2


_SEPARATOR_HOSTILE = {
    "digit-spaces": "1" + " " * 20_000 + "x",
    "jan-5-spaces": "jan 5" + " " * 20_000 + "x",
    "5-jan-spaces": "5 jan" + " " * 20_000 + "x",
    "fy-spaces": "FY" + " " * 20_000 + "x",
    "section-repeated": "section " * 20_000,
    "rs-commas": "Rs. " + "1," * 50_000,
    "slashes": "1/" * 50_000,
    "year-dashes": "2026-" * 20_000,
}


@pytest.mark.parametrize("name", list(_SEPARATOR_HOSTILE))
def test_finders_are_linear_on_separator_runs(name):
    text = _SEPARATOR_HOSTILE[name]
    started = time.perf_counter()
    for finder in (dates, periods, amounts, references, file_mentions):
        finder(text)
    assert time.perf_counter() - started < 2
