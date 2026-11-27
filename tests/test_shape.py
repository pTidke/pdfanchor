"""Structure detection: numbered paragraphs, provisos, questions, definitions and records."""

from pathlib import Path

import pytest

from ragnaroq.anchors import PdfAnchor
from ragnaroq.config import Config
from ragnaroq.model import Document, Engine, Line, Page, Source
from ragnaroq.readers import read_bytes
from ragnaroq.semantic import paragraphs
from ragnaroq.semantic.shape import Definition, Shape, shape

GENERATED = Path(__file__).parent.parent / "references" / "chunking" / "generated"


def read(data, name):
    return read_bytes(data, name, Config(), engines=[], base=GENERATED).document


def shape_of(document):
    return shape(paragraphs(document), document)


def md(text):
    """A document of the blank-line-separated blocks (`# ` marks a heading). Markdown would turn
    `1.` into a list marker, so only text with a table goes through the Markdown reader."""
    if "|" in text:
        pytest.importorskip("markdown_it")
        return read(text.encode(), "a.md")
    lines = []
    for i, block in enumerate(text.strip().split("\n\n")):
        heading = block.startswith("# ")
        block = block[2:] if heading else block
        lines.append(
            Line(
                id=f"l{i}",
                page=1,
                order=i,
                raw=block,
                text=block,
                source="native",
                paragraph=i,
                role="heading" if heading else "body",
                level=1 if heading else 0,
                status="accepted",
                anchor=PdfAnchor(page=1, box=(0, 10 * i, 100, 10 * i + 9)),
            )
        )
    return Document(
        source=Source(sha256="0", bytes=1, filename="a.pdf", pages=1, format="pdf"),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[Page(number=1, kind="native", lines=lines)],
    )


def generated(name):
    return shape_of(read((GENERATED / name).read_bytes(), name))


def numbered(text):
    return [p.text for p in shape_of(md(text)).numbered]


# numbered paragraphs


def test_a_run_of_numbered_paragraphs_counts():
    assert numbered("1. One.\n\n2. Two.\n\n3. Three.\n") == ["1. One.", "2. Two.", "3. Three."]


def test_a_lone_numbered_line_in_prose_is_not_a_numbered_paragraph():
    assert numbered("Some prose here.\n\n1. A lone item.\n\nMore prose follows.\n") == []


def test_numbers_that_do_not_follow_on_are_not_a_run():
    assert numbered("1. First.\n\n5. Fifth.\n\n9. Ninth.\n") == []


def test_prose_parentheticals_do_not_make_a_run():
    text = "(mild) effects were seen.\n\n(civil) servants may apply.\n\nOthers wait.\n"
    assert numbered(text) == []


def test_an_initial_at_the_start_of_a_paragraph_does_not_make_a_run():
    assert numbered("C. Smith said so.\n\nThen the meeting ended.\n\nL. Jones agreed.\n") == []


def test_a_lone_letter_bracket_is_not_a_run():
    assert numbered("a) this is a stray point.\n\nProse goes on for a while.\n") == []


def test_a_false_start_does_not_spoil_the_run_that_follows():
    got = numbered("(mild) effects.\n\n(a) First.\n\n(b) Second.\n")
    assert got == ["(a) First.", "(b) Second."]


def test_non_numbered_paragraphs_may_sit_between_members_of_a_run():
    got = numbered("1. First.\n\nA note between.\n\n2. Second.\n")
    assert got == ["1. First.", "2. Second."]


@pytest.mark.parametrize(
    "first, second",
    [
        ("1.", "2."),
        ("1.1", "1.2"),
        ("(1)", "(2)"),
        ("1)", "2)"),
        ("(a)", "(b)"),
        ("(i)", "(ii)"),
        ("I.", "II."),
        ("Article 4", "Article 5"),
        ("Section 3.", "Section 4."),
        ("Clause 4", "Clause 5"),
    ],
)
def test_each_numbering_form_runs_in_its_own_sequence(first, second):
    got = numbered(f"{first} Alpha text.\n\n{second} Beta text.\n")
    assert len(got) == 2


def test_two_different_forms_are_not_one_run():
    assert numbered("1. First.\n\n(b) Second.\n\nArticle 9 Third.\n") == []


def test_a_roman_numeral_may_follow_letters_in_the_same_list():
    got = numbered("(h) Eighth.\n\n(i) Ninth.\n\n(j) Tenth.\n")
    assert len(got) == 3


def test_a_nested_list_leaves_the_outer_run_intact():
    got = numbered("1. One.\n\n(a) Sub a.\n\n(b) Sub b.\n\n2. Two.\n")
    assert got == ["1. One.", "(a) Sub a.", "(b) Sub b.", "2. Two."]


def test_headings_are_not_numbered_paragraphs():
    s = shape_of(md("# 1. Intro\n\n# 2. Body\n\nText.\n"))
    assert s.numbered == [] and s.headings == 2


# provisos and explanations


def test_a_proviso_and_an_explanation_attach_to_the_paragraph_before():
    s = shape_of(
        md(
            "1. Every person shall file.\n\nProvided that a minor need not.\n\n"
            "Explanation.— A minor is under 18.\n\n2. Next.\n"
        )
    )
    assert len(s.numbered) == 2 and len(s.provisos) == 1 and len(s.explanations) == 1


@pytest.mark.parametrize("start", ["Provided that", "Provided further that", "Provided also that"])
def test_each_proviso_form(start):
    assert len(shape_of(md(f"1. A.\n\n{start} x.\n\n2. B.\n")).provisos) == 1


@pytest.mark.parametrize(
    "start", ["Explanation.", "Explanation.—", "Explanation 1.—", "Explanation:", "Explanation -"]
)
def test_each_explanation_form(start):
    assert len(shape_of(md(f"1. A.\n\n{start} x.\n\n2. B.\n")).explanations) == 1


def test_the_word_explanation_in_a_sentence_is_not_an_explanation():
    assert shape_of(md("Explanation of the rule is below.\n")).explanations == []


def test_a_proviso_after_a_paragraph_number_is_found():
    s = shape_of(md("1. Provided that a minor need not.\n\n2. Explanation.— A minor is under 18.\n"))
    assert len(s.provisos) == 1 and len(s.explanations) == 1 and len(s.numbered) == 2


# question-answer pairs


def pairs(text):
    return [(q.text, [a.text for a in answers]) for q, answers in shape_of(md(text)).qa]


def test_q_and_a_markers_make_pairs():
    got = pairs("Q. What is it?\n\nA. A thing.\n\nQ: Why?\n\nAns. Because.\n\nQ1. Who?\n\nAnswer: Me.\n")
    assert [q for q, _ in got] == ["Q. What is it?", "Q: Why?", "Q1. Who?"]
    assert got[1][1] == ["Ans. Because."]


def test_a_question_paragraph_may_start_with_the_word_question():
    assert len(pairs("Question 1: What is it?\n\nIt is a thing.\n")) == 1


def test_a_heading_that_ends_in_a_question_mark_is_a_question():
    got = pairs("# How do I join?\n\nAsk at the desk.\n\n# Where is it?\n\nUpstairs.\n")
    assert got == [("How do I join?", ["Ask at the desk."]), ("Where is it?", ["Upstairs."])]


def test_a_short_question_followed_by_a_statement_is_a_question():
    got = pairs("When is it open?\n\nIt opens at nine.\n")
    assert got == [("When is it open?", ["It opens at nine."])]


def test_a_rhetorical_question_in_prose_is_not_a_qa_pair_unless_an_answer_follows():
    assert pairs("Who would do such a thing?\n\nWhy would they?\n") == []
    assert pairs("Who would do such a thing?\n") == []
    assert len(pairs("Who would do such a thing?\n\nNobody knows.\n")) == 1


def test_a_long_question_mark_paragraph_is_not_a_question():
    long = "x " * 120 + "end?"
    assert pairs(f"{long}\n\nAnswer text here.\n") == []


def test_a_question_followed_only_by_another_question_has_no_pair():
    got = pairs("Q. First?\n\nQ. Second?\n\nA. Only this one.\n")
    assert [q for q, _ in got] == ["Q. Second?"]


def test_an_answer_runs_until_the_next_question():
    got = pairs("Q. Why?\n\nA. One.\n\nTwo more sentences.\n\nQ. How?\n\nA. Easy.\n")
    assert got[0][1] == ["A. One.", "Two more sentences."]


def test_an_answer_that_ends_in_a_question_mark_is_still_an_answer():
    got = pairs("Q. Why?\n\nA. Why not?\n")
    assert got == [("Q. Why?", ["A. Why not?"])]


def test_a_heading_that_is_not_a_question_ends_an_answer():
    got = pairs("Q. Why?\n\nA. Because.\n\n# Other\n\nUnrelated prose.\n")
    assert got == [("Q. Why?", ["A. Because."])]


# definitions


def terms(text):
    return [d.term for d in shape_of(md(text)).definitions]


def test_definitions_record_the_term_as_written():
    assert terms('"Assessee" means a person.\n\nWord means x.\n') == ["Assessee", "Word"]


@pytest.mark.parametrize(
    "text, term",
    [
        ('"Fee" means the sum.', "Fee"),
        ("“Fee” means the sum.", "Fee"),
        ('"Fee" shall mean the sum.', "Fee"),
        ('"Fee" includes interest.', "Fee"),
        ("Fee means the sum.", "Fee"),
        ("Fee: means the sum.", "Fee"),
        ('In this Act, "assessee" means a person.', "assessee"),
        ("(a) “Premises” means the building.", "Premises"),
        ("Start Date means 1 April.", "Start Date"),
    ],
)
def test_each_definition_form(text, term):
    assert terms(text + "\n") == [term]


@pytest.mark.parametrize(
    "text",
    [
        "This means that we left.",
        "It includes tax.",
        "Nothing here means harm.",
        "The new fee means a lot.",
        "Thus the rate means 5%.",
        "The word means x.",
        "That means we stop.",
        "So it means more.",
        "the fee means a lot.",
        "A very long subject phrase means this.",
    ],
)
def test_ordinary_prose_with_means_is_not_a_definition(text):
    assert terms(text + "\n") == []


def test_a_definition_records_where_it_sits_in_its_paragraph():
    s = shape_of(md('Intro words. "Fee" means the sum. Then more words.\n'))
    (d,) = s.definitions
    assert isinstance(d, Definition)
    assert d.para.text[d.start : d.end] == '"Fee" means the sum.'


def test_a_two_column_term_definition_table_gives_one_definition_per_row():
    table = "| Term | Definition |\n| --- | --- |\n| Assessee | A person. |\n| Year | Twelve months. |\n"
    s = shape_of(md(table))
    assert [d.term for d in s.definitions] == ["Assessee", "Year"]
    assert s.tables == 1


def test_a_word_meaning_header_is_case_insensitive():
    table = "| WORD | meaning |\n| --- | --- |\n| Cat | An animal. |\n"
    assert [d.term for d in shape_of(md(table)).definitions] == ["Cat"]


def test_other_tables_give_no_definitions():
    table = "| Term | Fee | Note |\n| --- | --- | --- |\n| A | 1 | x |\n"
    assert shape_of(md(table)).definitions == []
    table = "| Name | Value |\n| --- | --- |\n| A | 1 |\n"
    assert shape_of(md(table)).definitions == []


# records


def test_a_json_array_of_flat_same_key_objects_is_a_table_not_records():
    s = shape_of(read(b'[{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]', "a.json"))
    assert s.records == [] and s.tables == 1


def test_a_json_array_of_other_objects_gives_one_record_each():
    s = shape_of(read(b'[{"a": {"x": "1"}}, {"a": {"x": "2"}}, {"b": "3"}]', "a.json"))
    assert s.records == ["/0", "/1", "/2"]


def test_a_json_object_with_no_array_gives_a_record_per_value():
    s = shape_of(read(b'{"one": {"x": "1"}, "two": {"x": "2"}}', "a.json"))
    assert s.records == ["/one", "/two"]


def test_xml_children_that_repeat_one_name_are_records():
    s = shape_of(read(b"<root><item><n>a</n></item><item><n>b</n></item></root>", "a.xml"))
    assert s.records == ["/root/item[1]", "/root/item[2]"]


def test_xml_children_with_different_names_are_not_records():
    s = shape_of(read(b"<root><a><n>a</n></a><b><n>b</n></b></root>", "a.xml"))
    assert s.records == []


def test_a_document_that_is_not_data_has_no_records():
    assert shape_of(md("Plain text.\n")).records == []


# counts


def test_the_counts_for_a_small_markdown_document():
    s = shape_of(md("# Top\n\nBody text.\n\n## Sub\n\nMore.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"))
    assert isinstance(s, Shape)
    assert (s.headings, s.heading_depth, s.tables) == (2, 2, 1)
    assert s.body_chars == len("Body text.") + len("More.")
    assert s.chars == s.body_chars + s.table_chars + len("Top") + len("Sub")
    assert s.table_chars > 0 and s.slides == 0 and s.figures == 0


def test_a_document_with_no_headings_has_depth_zero():
    assert shape_of(md("Just text.\n")).heading_depth == 0


# the generated tuning documents


def test_the_notice_has_eight_numbered_paragraphs_two_provisos_and_an_explanation():
    s = generated("notice.pdf")
    assert (len(s.numbered), len(s.provisos), len(s.explanations)) == (8, 2, 1)


def test_the_faq_has_twelve_pairs():
    assert len(generated("faq.docx").qa) == 12


def test_the_contract_has_six_definitions():
    assert [d.term for d in generated("contract.docx").definitions] == [
        "Client",
        "Contractor",
        "Premises",
        "Services",
        "Fee",
        "Term",
    ]


def test_the_records_file_has_records_and_no_table():
    s = generated("records.json")
    assert s.records == ["/equipment", "/operators"] and s.tables == 0


def test_the_table_file_is_a_table_not_records():
    s = generated("table.json")
    assert s.records == [] and s.tables == 1


def test_the_deck_counts_slides():
    assert generated("deck.pptx").slides == 6


def test_the_spreadsheet_is_mostly_table():
    s = generated("rates.xlsx")
    assert s.tables >= 1 and s.table_chars > 0


# hostile text stays fast


@pytest.mark.parametrize(
    "text",
    [
        '"' * 50_000,
        "Explanation" + " " * 50_000,
        "Provided " * 20_000,
        ". " * 30_000 + "x means y",
        "a " * 50_000 + "means",
        "(" * 50_000,
    ],
)
def test_hostile_text_is_read_in_linear_time(text):
    import time

    begun = time.perf_counter()
    shape_of(md(text))
    assert time.perf_counter() - begun < 3
