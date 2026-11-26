"""The third-reader rule: a vlm: reading settles a disputed line only on an exact match."""

from conftest import BAD_DATE

from ragnaroq import policy
from ragnaroq.config import Policy
from ragnaroq.crosscheck import shares_words
from ragnaroq.model import Box, Document, Engine, Line, Page, Reading, Resolution, Source

DUE, MISREAD = "The due date is 31st July 2026.", "The due date is 31st July 2028."


def scanned_document(*rows):
    lines = []
    for i, (first, second, third) in enumerate(rows):
        readings = [Reading(source="ocr:one", text=first), Reading(source="ocr:two", text=second)]
        if third is not None:
            readings.append(Reading(source="vlm:paddleocr-vl", text=third))
        lines.append(
            Line(
                id=f"p1-l{i + 1}",
                page=1,
                order=i,
                box=Box(x0=72, top=100 + 20 * i, x1=500, bottom=112 + 20 * i),
                raw=first,
                text=first,
                source="ocr:one",
                readings=readings,
            )
        )
    return Document(
        source=Source(sha256="0", bytes=0, filename="t.pdf", pages=1),
        engine=Engine(name="t", version="0", policy_version="0", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[Page(number=1, width=612, height=792, kind="scanned", lines=lines)],
    )


def decided(*rows):
    document = scanned_document(*rows)
    policy.decide(document, Policy())
    return list(document.lines())


def test_agreeing_with_the_first_reader_settles_the_line():
    (line,) = decided((DUE, MISREAD, DUE))
    assert line.status == "accepted" and line.flags == [] and line.text == DUE
    assert line.resolution == Resolution(
        rule="crosscheck.third-reader", agreed=["ocr:one", "vlm:paddleocr-vl"], text_from="ocr:one"
    )


def test_agreeing_with_the_second_reader_takes_its_text_and_keeps_the_raw_reading():
    (line,) = decided((MISREAD, DUE, DUE))
    assert line.status == "accepted" and line.text == DUE and line.raw == MISREAD
    assert line.resolution.text_from == "ocr:two"


def test_layout_marks_are_not_text():
    (line,) = decided((DUE, MISREAD, "☐ " + DUE + " |"))
    assert line.status == "accepted"


def test_no_exact_match_keeps_the_line_for_a_person():
    (line,) = decided((DUE, MISREAD, "The due date is 31 July 2026."))
    assert line.status == "flagged" and line.resolution is None
    assert {f.rule for f in line.flags} == {"crosscheck.disagreement", "crosscheck.third-reader.no-match"}


def test_an_agreeing_reading_must_pass_the_validators():
    (line,) = decided(("The earlier order dated 31 March, 2025 is withdrawn.", BAD_DATE, BAD_DATE))
    flag = next(f for f in line.flags if f.rule == "crosscheck.third-reader.validators")
    assert BAD_DATE in flag.message and "date.ordinal-suffix" in flag.message


def test_a_reading_a_validator_flags_before_normalisation_is_still_refused():
    # Decomposed, the code ends in a plain "O" (a letter readers take for 0) plus an accent,
    # and the code rule flags it; composed, "Ö" is not on that list. Either form refuses.
    decomposed, composed = "Form R28CO\u0308 is due.", "Form R28C\u00d6 is due."
    (line,) = decided(("Form R28C0 is due.", decomposed, composed))
    assert line.status == "flagged" and line.resolution is None
    assert "crosscheck.third-reader.validators" in {f.rule for f in line.flags}


def test_the_validators_see_the_text_the_line_would_carry():
    # Decomposed, the odd suffix is six characters and the date rule does not see a date;
    # composed (NFC, as `text` would hold it) it is three, and the rule flags it.
    odd = "The due date is 31s\u0302s\u0302s\u0302 July 2026."
    (line,) = decided((MISREAD, odd, odd))
    assert line.status == "flagged" and line.resolution is None
    flag = next(f for f in line.flags if f.rule == "crosscheck.third-reader.validators")
    assert "date.ordinal-suffix on ocr:two" in flag.message


def test_a_reader_that_saw_more_text_holds_the_line_back():
    seen = "Designee Designee's Phone Personal identification"
    more = "Designee Designee's name Phone no. Personal identification number (PIN)"
    (line,) = decided((seen, more, seen))
    flag = next(f for f in line.flags if f.rule == "crosscheck.third-reader.omission")
    assert seen in flag.message


def test_an_underscore_inside_a_word_holds_the_line_back():
    rows = ("See 2026-02 IRB#NOT-2026-3.", "See 2026-02_IRBANOT 2026-3.", "See 2026-02 IRB#NOT-2026-3.")
    (line,) = decided(rows)
    flag = next(f for f in line.flags if f.rule == "crosscheck.third-reader.underscore")
    assert rows[2] in flag.message and "ocr:two" in flag.message


def test_text_that_belongs_to_the_next_line_holds_the_line_back():
    longer = "Personal Address name on your social security card? If not, to ensure you get"
    lines = decided(
        ("Enter Address Does your name match the", "Enter Address Does your name match the", None),
        ("Personal name on your social security", longer, longer),
        ("card? If not, to ensure you get", "card? If not, to ensure you get", None),
    )
    flag = next(f for f in lines[1].flags if f.rule == "crosscheck.third-reader.neighbour")
    assert longer in flag.message


def test_text_from_the_line_below_in_the_same_column_holds_the_line_back():
    longer = "Personal Address name on your social security card? If not, to ensure you get"
    rows = [  # two columns, read across: the line below in the same column is two lines on
        (72, 100, "Enter Address Does your name match the", None),
        (320, 100, "Spouse details are on the next page", None),
        (72, 120, "Personal name on your social security", longer),
        (320, 120, "Filing status checkboxes appear here", None),
        (72, 140, "card? If not, to ensure you get", None),
    ]
    document = scanned_document(*[(text, longer if third else text, third) for _, _, text, third in rows])
    for line, (x0, top, _, _) in zip(document.lines(), rows, strict=True):
        line.box = Box(x0=x0, top=top, x1=x0 + 220, bottom=top + 12)
    policy.decide(document, Policy())
    line = document.pages[0].lines[2]
    flag = next(f for f in line.flags if f.rule == "crosscheck.third-reader.neighbour")
    assert longer in flag.message and line.resolution is None


def test_an_empty_reading_never_settles_a_line():
    (line,) = decided(("...", "", "☐"))
    assert line.status == "flagged" and line.resolution is None


def test_a_native_page_is_never_settled_by_the_third_reader():
    document = scanned_document((DUE, MISREAD, DUE))
    page = document.pages[0]
    page.kind = "native"
    line = page.lines[0]
    line.source = "native"
    line.readings = [r for r in line.readings if r.source != "ocr:one"]  # a text layer has no OCR reading
    policy.decide(document, Policy())
    assert line.status == "flagged" and rules(line) == {"crosscheck.disagreement"}
    assert line.resolution is None and line.text == DUE


def test_a_line_with_another_flag_is_left_to_a_person():
    (line,) = decided((BAD_DATE, "The earlier order dated 31 March, 2025 is withdrawn.", BAD_DATE))
    assert line.resolution is None
    assert not any(f.rule.startswith("crosscheck.third-reader") for f in line.flags)


def test_the_third_reading_is_not_compared_like_the_others():
    (line,) = decided((DUE, DUE, "something else entirely"))
    assert line.status == "accepted" and line.flags == [] and line.resolution is None


def test_deciding_again_starts_from_the_first_reader():
    document = scanned_document((MISREAD, DUE, DUE))
    policy.decide(document, Policy())
    first = document.model_dump()
    policy.decide(document, Policy())
    assert document.model_dump() == first
    line = next(document.lines())
    line.readings = [r for r in line.readings if not r.source.startswith("vlm:")]
    policy.decide(document, Policy())
    assert line.text == MISREAD and line.status == "flagged" and line.resolution is None


def rules(line):
    return {f.rule for f in line.flags}


def test_a_mark_inside_a_word_is_content():
    (line,) = decided(
        ("Pay tax of Rs. ,000 by July", "Pay tax of Rs. 5,000 by July", "Pay tax of Rs. ■,000 by July")
    )
    assert line.status == "flagged" and line.resolution is None
    assert "crosscheck.third-reader.no-match" in rules(line)


def test_a_superscript_is_not_the_digit_it_looks_like():
    rows = [
        ("Deduction under section 801 is allowed.", "Deduction under section 80 is allowed.",
         "Deduction under section 80¹ is allowed."),
        ("Total income 12,5001", "Total income 12,500", "Total income 12,500¹"),
    ]
    for row in rows:
        (line,) = decided(row)
        assert line.status == "flagged" and line.resolution is None
        assert "crosscheck.third-reader.no-match" in rules(line)


def test_a_negation_only_one_reader_saw_holds_the_line_back():
    rows = ("You are required to file.", "You are not required to file.", "You are required to file.")
    (line,) = decided(rows)
    assert "crosscheck.third-reader.omission" in rules(line)


def test_a_mark_where_a_reader_saw_a_number_holds_the_line_back():
    (line,) = decided(
        (
            "Enter the amount from line of the form.",
            "Enter the amount from line 1 of the form.",
            "Enter the amount from line | of the form.",
        )
    )
    assert "crosscheck.third-reader.omission" in rules(line)


def test_a_fill_rule_is_not_an_underscore_in_a_word():
    (line,) = decided(("Name ____________ Date of birth", "Name Date of birth", "Name Date of birth"))
    assert line.status == "accepted" and line.resolution.text_from == "ocr:one"


def test_shares_words():
    assert shares_words("a card? If not, to b", "card? If not, to ensure", 4)
    assert not shares_words("card? If not", "card? If not, to", 4)
