from pathlib import Path

import pytest

from ragnaroq.config import Config
from ragnaroq.crosscheck import compare, critical_tokens
from ragnaroq.ocr.tesseract import parse_tsv
from ragnaroq.readers import read_bytes
from ragnaroq.semantic.describe import describe
from ragnaroq.semantic.overrides import Override
from ragnaroq.semantic.rules import MAX_CHARS_LIMITS, STRATEGIES, choose
from ragnaroq.structure import scripts_of
from ragnaroq.validators import run
from ragnaroq.validators.dates import expected_suffix


def rules(text):
    return [flag.rule for flag in run(text)]


@pytest.mark.parametrize(
    "text",
    [
        "with effect from the 1st day of April, 2026",
        "dated 05th March, 2024",
        "dated 5 March 2024",
        "on 22nd June 2025 and 23rd June 2025",
        "under section 80C of the Act",
        "Rs. 1,50,000 in March",
        "as on 31.03.2026",
    ],
)
def test_well_formed_dates_and_ordinary_text_raise_nothing(text):
    assert rules(text) == []


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("dated 31* March, 2025", "date.ordinal-suffix"),
        ('dated 05" March, 2024', "date.ordinal-suffix"),
        ("from 015 April 2024", "date.day-out-of-range"),
        ("on 22th June 2025", "date.ordinal-mismatch"),
        ("on 0sth March, 2024", "date.day-out-of-range"),
        ("dated 12th May, 20245", "date.year-implausible"),
    ],
)
def test_the_recorded_ocr_date_errors_are_flagged_and_never_rewritten(text, rule):
    assert rule in rules(text)


def test_ordinal_suffix_rule():
    assert [expected_suffix(d) for d in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 31)] == [
        "st", "nd", "rd", "th", "th", "th", "th", "st", "nd", "rd", "st",
    ]  # fmt: skip


def test_unmapped_characters_are_flagged():
    assert rules("Income (cid:12) Tax") == ["encoding.unmapped"]
    assert rules("Income � Tax") == ["encoding.unmapped"]
    assert rules("Income  Tax") == ["encoding.unmapped"]


def test_critical_tokens_keep_numbers_references_and_negations():
    tokens = critical_tokens(
        "Under section 10(23FE), w.e.f. 01.04.2026, not exceeding Rs. 1,50,000 (or 12.5%)."
    )
    assert set(tokens) == {"10(23fe)", "01.04.2026", "not", "1,50,000", "12.5%"}


def test_one_wrong_digit_breaks_agreement_even_when_text_is_nearly_identical():
    first = "The due date is extended to 31st July 2026 for all assessees."
    close = compare(first, "The due date is extended to 31st July 2028 for all assessees.")
    assert close.similarity > 0.95 and not close.agrees(0.90)
    assert close.only_first == ("2026",) and close.only_second == ("2028",)
    assert compare(first, "The due date is  extended to 31st July 2026 for all assessees").agrees(0.90)
    assert not compare("shall not apply", "shall apply").agrees(0.5)


def test_scripts():
    assert scripts_of("भारत का राजपत्र") == ["Deva"]
    assert scripts_of("The Gazette of India 2026") == ["Latn"]
    assert scripts_of("आयकर Income-tax") == ["Deva", "Latn"]
    assert scripts_of("31.03.2026") == []


def test_tesseract_rows_become_lines_in_order():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "4\t1\t1\t1\t1\t0\t10\t10\t200\t20\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t10\t80\t20\t96.5\tCircular",
        "5\t1\t1\t1\t1\t2\t100\t12\t40\t18\t91.5\tNo.",
        "5\t1\t1\t1\t2\t1\t10\t50\t60\t20\t88\t2026",
        "5\t1\t1\t1\t2\t2\t80\t50\t10\t20\t95\t ",
    ]
    lines = parse_tsv("\n".join([header, *rows]))
    assert [line.text for line in lines] == ["Circular No.", "2026"]
    assert lines[0].box == (10, 10, 140, 30) and lines[0].confidence == 94.0


@pytest.mark.parametrize(
    "text, expected",
    [
        # Codes mixing letters and digits, with a letter a reader can take for a digit.
        ("R28CO 28,000 R28C1 28,001", ["code.confusable"]),
        ("Cat. No. 11320B", ["code.confusable"]),
        ("PAN AABTS1181R of the trust", ["code.confusable"]),
        ("Addlines16and17", ["code.confusable"]),  # words run together by a reader
        # A bracket the line never pairs, inside such a code: "4a)" read for "4(a)".
        ("Step 4a). Enter in this step", ["code.unbalanced-bracket"]),
        # Not codes, or no risk: nothing is raised.
        ("(see line 12b) and line 13", []),
        ("Form 1040-ES and Form 1040-SR", []),
        ("call 1-800-THE-LOST today", []),
        ("a 10-year-old child", []),
        ("see IRS.gov/Form1040 for more", []),
        ("on the 31st day of March", []),
        ("R28C1 28,001 Form W-4", []),
    ],
)
def test_codes_mixing_letters_and_digits(text, expected):
    assert [rule for rule in rules(text) if rule.startswith("code.")] == expected


def test_misreading_checks_apply_only_to_text_read_from_an_image():
    assert run("Cat. No. 11320B", ocr=False) == []
    assert [flag.rule for flag in run("Cat. No. 11320B", ocr=True)] == ["code.confusable"]


@pytest.mark.parametrize(
    "text, flagged",
    [
        ("year in the entry space at the top of page | of Form 1040", True),
        ("a tax year other than January |", True),
        ("| Married filing jointly", True),
        ("Under penalties of perjury, | declare that | have examined", True),
        ("City State [| ZIP code", False),  # joined to a bracket: a box, judged elsewhere
        ("a|b or x||y", False),
        ("line 1 of Form 1040", False),
    ],
)
def test_a_lone_bar_in_text_read_from_an_image_is_flagged(text, flagged):
    assert ("glyph.lone-bar" in [flag.rule for flag in run(text, ocr=True)]) is flagged


@pytest.mark.parametrize(
    "text, flagged",
    [
        ("New Delhi, the Sth January, 2026", True),
        ("on or before the 3lst day of March, 2030", True),
        ("with effect from the lst April", True),
        ("dated 3Oth June, 2025", True),
        ("New Delhi, the 5th January, 2026", False),
        ("on or before the 31st day of March, 2030", False),
        ("the Ist amendment", False),  # no month: not a date
        ("Sth is not a word", False),
    ],
)
def test_a_letter_read_for_a_digit_in_a_day_is_flagged(text, flagged):
    assert ("date.confusable-day" in [flag.rule for flag in run(text, ocr=True)]) is flagged


def test_a_bar_in_a_text_layer_is_text():
    assert run("A | B", ocr=False) == []


def test_readings_a_letter_apart_agree_unless_letters_must_match():
    slip = compare("Hf you pay tax on time", "If you pay tax on time")
    assert slip.critical_equal and slip.similarity >= 0.9 and not slip.letters_equal
    assert slip.agrees(0.9) and not slip.agrees(0.9, same_letters=True)
    # Symbols and spacing are not letters: a checkbox read as "[_]" by one reader only.
    boxed = compare("Single [_] Married filing jointly", "Single Married filing jointly")
    assert boxed.letters_equal


GENERATED = Path(__file__).parent.parent / "references" / "chunking" / "generated"


def generated_meta(name):
    document = read_bytes(
        (GENERATED / name).read_bytes(), name, Config(), engines=[], base=GENERATED
    ).document
    return describe(document)


def with_structure(meta, **changes):
    return meta.model_copy(update={"structure": meta.structure.model_copy(update=changes)})


def test_each_generated_document_gets_its_strategy():
    got = {p.name: choose(generated_meta(p.name), Config()).strategy for p in sorted(GENERATED.iterdir())}
    assert got == {
        "notice.pdf": "numbered-paragraphs",
        "faq.docx": "question-answer",
        "manual.md": "sections",
        "manual.html": "sections",
        "contract.docx": "numbered-paragraphs",
        "deck.pptx": "single",  # 1,273 characters: rule 1 comes first
        "rates.xlsx": "table-rows",
        "records.json": "single",  # 940 characters
        "table.json": "single",  # 895 characters
        "essay.md": "sentences",
    }


def test_the_plan_entry_names_its_reasons_parameters_and_filters():
    plan = choose(generated_meta("notice.pdf"), Config())
    assert plan.chosen_by == "rules"
    assert plan.parameters == {
        "max_chars": 1200,
        "min_chars": 200,
        "parent": "section",
        "keep_with_parent": ["proviso", "explanation"],
    }
    assert plan.reasons[0].startswith("kind = numbered (numbered_paragraphs = ")
    assert plan.reasons[1].startswith("length.chars = ") and plan.reasons[1].endswith("> 1500")
    assert plan.filters == [f for f in ("dates", "periods", "references") if plan.filters.count(f)]


def test_a_short_document_is_single_whatever_its_kind():
    meta = generated_meta("notice.pdf")
    short = meta.model_copy(update={"length": meta.length.model_copy(update={"chars": 900})})
    plan = choose(short, Config())
    assert plan.strategy == "single" and plan.parameters["parent"] == "document"
    assert plan.reasons == ["length.chars = 900 < 1500"]
    big_table = with_structure(short, tables=1, largest_table_chars=1300)
    assert choose(big_table, Config()).strategy == "numbered-paragraphs"


def longer(meta, chars):
    return meta.model_copy(update={"length": meta.length.model_copy(update={"chars": chars})})


def test_a_data_file_that_is_one_table_is_cut_as_table_rows():
    meta = longer(generated_meta("table.json"), 5000)
    plan = choose(meta, Config())
    assert plan.strategy == "table-rows" and plan.parameters["parent"] == "table"
    assert any("tables = 1" in r for r in plan.reasons)
    assert choose(with_structure(meta, tables=2), Config()).strategy == "records"


def test_the_other_long_generated_documents_take_the_strategy_of_their_kind():
    assert choose(longer(generated_meta("deck.pptx"), 5000), Config()).strategy == "slides"
    assert choose(longer(generated_meta("records.json"), 5000), Config()).strategy == "records"


def test_an_override_replaces_the_strategy_and_the_limits():
    meta = generated_meta("notice.pdf")
    plan = choose(meta, Config(), Override("faq/*.pdf", "sentences", max_chars=800, min_chars=100))
    assert plan.strategy == "sentences" and plan.chosen_by == "override"
    assert plan.reasons == ["override: faq/*.pdf"]
    assert plan.parameters == {
        "max_chars": 800,
        "min_chars": 100,
        "parent": "document",
        "keep_with_parent": [],
    }
    plain = choose(meta, Config(chunk_chars=900), Override("x", "sections"))
    assert plain.parameters["max_chars"] == 900 and plain.parameters["min_chars"] == 200


def test_the_strategy_names_and_limits_are_the_specs():
    assert STRATEGIES == (
        "single",
        "question-answer",
        "numbered-paragraphs",
        "sections",
        "table-rows",
        "slides",
        "records",
        "sentences",
    )
    assert MAX_CHARS_LIMITS == (200, 4000)
