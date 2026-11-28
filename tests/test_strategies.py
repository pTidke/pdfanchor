"""What the chunking strategies cut a document into: `single`, `sentences`, `numbered-paragraphs`,
`sections` and `question-answer` (spec sections 3 and 5)."""

import uuid
from pathlib import Path

import pytest

from ragnaroq import Config
from ragnaroq.anchors import PdfAnchor
from ragnaroq.chunking.pieces import Piece, StrategyContext
from ragnaroq.chunking.sentences import sentences
from ragnaroq.chunking.strategies import STRATEGY_FUNCTIONS, pieces
from ragnaroq.export.chunks import LIST_KEYS, METADATA_KEYS, NAMESPACE, to_chunks
from ragnaroq.model import Document, Engine, Line, Page, Source
from ragnaroq.readers import ReaderRefused, read_bytes
from ragnaroq.semantic.describe import describe
from ragnaroq.semantic.overrides import Override
from ragnaroq.semantic.paragraphs import numbering, paragraphs
from ragnaroq.semantic.rules import CHUNK_LIST_LIMIT, STRATEGIES, choose
from ragnaroq.semantic.shape import shape

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "references" / "demo"
GENERATED = ROOT / "references" / "chunking" / "generated"


def read(folder, name):
    return read_bytes((folder / name).read_bytes(), name, Config(), engines=[], base=folder).document


def md(text, name="notes.md"):
    return read_bytes(text.encode(), name, Config(), engines=[]).document


def plan(document, strategy, max_chars=None, min_chars=None):
    return choose(describe(document), Config(), Override("*", strategy, max_chars, min_chars))


def chunks_of(document, strategy=None, **limits):
    choice = plan(document, strategy, **limits) if strategy else None
    return to_chunks(document, Config(), path=document.source.filename, choice=choice)


def chunks_of_md(text, strategy=None, **limits):
    return chunks_of(md(text), strategy, **limits)


def all_chunks(folder):
    found = []
    for path in sorted(folder.iterdir()):
        if path.name.endswith(".jsonl") or path.suffix in (".png", ".toml"):
            continue
        try:
            found += to_chunks(read(folder, path.name), Config(), path=path.name)
        except ReaderRefused:
            assert path.name == "bomb.xml"
    return found


def split_sentences(text):
    return [text[a:b].strip() for a, b in sentences(text)]


def pdf_document(texts, name="a.pdf"):
    """One PDF page, one paragraph per text, each paragraph one line."""
    lines = [
        Line(
            id=f"l{i}",
            page=1,
            order=i,
            raw=text,
            text=text,
            source="native",
            paragraph=i,
            status="accepted",
            scripts=["Latn"],
            anchor=PdfAnchor(page=1, box=(0, 10 * i, 100, 10 * i + 9)),
        )
        for i, text in enumerate(texts)
    ]
    return Document(
        source=Source(sha256="0", bytes=1, filename=name, pages=1, format="pdf"),
        engine=Engine(name="t", version="0", policy_version="0.6", config_sha256="0", components={}),
        created_at="2026-01-01T00:00:00Z",
        pages=[Page(number=1, kind="native", lines=lines)],
    )


# --- the registry --------------------------------------------------------------------------------------


def test_single_and_sentences_are_registered_and_a_strategy_not_yet_built_falls_back_to_sentences():
    assert {"single", "sentences"} <= set(STRATEGY_FUNCTIONS) <= set(STRATEGIES)
    document = read(GENERATED, "essay.md")
    for name in STRATEGIES:
        (first, *_) = chunks_of(document, name)
        assert first["metadata"]["strategy"] == (name if name in STRATEGY_FUNCTIONS else "sentences")


def test_an_unknown_strategy_is_refused():
    document = md("Some text.")
    choice = plan(document, "single").model_copy(update={"strategy": "paragraphs"})
    with pytest.raises(ValueError, match="paragraphs"):
        to_chunks(document, Config(), choice=choice)


def test_pieces_reads_its_limits_from_the_plan():
    document = read(GENERATED, "essay.md")
    paras = paragraphs(document)
    meta = describe(document)
    choice = plan(document, "sentences", max_chars=400, min_chars=100)
    context = StrategyContext(document, paras, shape(paras, document), meta, choice, Config())
    assert (context.max_chars, context.min_chars) == (400, 100)
    made = pieces(context)
    assert made and all(isinstance(piece, Piece) for piece in made)
    assert all(len(piece.text) <= 400 for piece in made)
    assert {piece.parent for piece in made} == {"document"}


# --- single -------------------------------------------------------------------------------------------

SMALL = "# Rates\n\nInterest applies to late payments.\n\n| Section | Rate |\n|---|---|\n| 80C | 5% |\n\nPay on time."


def test_single_is_the_whole_document_tables_included_in_one_chunk():
    document = md(SMALL)
    assert describe(document).length.chars < 1500 and plan(document, "single").strategy == "single"
    (chunk,) = to_chunks(document, Config())  # the rules choose single for a short document
    meta = chunk["metadata"]
    assert meta["strategy"] == "single" and meta["kind"] == "text" and meta["part_of"] == ""
    assert chunk["text"] == (
        "Rates\n\nInterest applies to late payments.\n\n| Section | Rate |\n| --- | --- |\n| 80C | 5% |"
        "\n\nPay on time."
    )
    assert meta["cite"] == meta["parent_cite"] == "notes.md, lines 1–1–lines 9–9"
    assert meta["parent_id"] == str(uuid.uuid5(NAMESPACE, f"{meta['file_id']}|parent|document"))
    assert meta["heading_path"] == "" and meta["heading"] == ""


def test_single_of_a_lone_table_is_a_table_chunk():
    document = read_bytes(b"Section,Amount\n80C,150000\n", "r.csv", Config()).document
    (chunk,) = to_chunks(document, Config())
    assert chunk["metadata"]["kind"] == "table" and chunk["metadata"]["strategy"] == "single"
    assert chunk["text"] == "| Section | Amount |\n| --- | --- |\n| 80C | 150000 |"


# --- sentences ----------------------------------------------------------------------------------------


def test_paragraphs_join_up_to_max_chars_and_a_heading_starts_a_new_chunk():
    text = (
        "# A\n\n## B\n\n"
        + "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(6))
        + "\n\n# C\n\nLast."
    )
    chunks = chunks_of_md(text, "sentences", max_chars=500, min_chars=0)
    assert all(c["metadata"]["chars"] <= 500 for c in chunks)
    assert [c["text"].count("Paragraph") for c in chunks[:-1]] == [2, 2, 2]
    assert all(c["text"].count("\n\n") == 1 for c in chunks[:-1])
    assert [(c["metadata"]["heading"], c["metadata"]["heading_path"]) for c in chunks] == [
        ("B", "A › B"),
        ("B", "A › B"),
        ("B", "A › B"),
        ("C", "C"),
    ]
    assert chunks[-1]["text"] == "Last." and chunks[-1]["embedding_text"] == "A > C\nLast."
    assert {c["metadata"]["parent_id"] for c in chunks} == {chunks[0]["metadata"]["parent_id"]}
    assert all(c["metadata"]["strategy"] == "sentences" and c["metadata"]["part_of"] == "" for c in chunks)


def assert_split_with_one_repeated(pieces_of_one_paragraph, paragraph, max_chars):
    """Each piece holds whole sentences of the paragraph, in order, at most max_chars; the next piece
    starts with the last sentence of the one before (unless that and its own first would not fit)."""
    whole = split_sentences(paragraph)
    m = len(pieces_of_one_paragraph)
    assert m > 1
    covered = []
    for n, chunk in enumerate(pieces_of_one_paragraph, 1):
        assert chunk["metadata"]["part_of"] == f"{n}/{m}"
        assert chunk["metadata"]["chars"] == len(chunk["text"]) <= max_chars
        own = split_sentences(chunk["text"])
        start = whole.index(own[0])
        assert whole[start : start + len(own)] == own, "a piece holds whole sentences, in order"
        covered.append((start, start + len(own)))
    repeated = 0
    for (_, b), (c, _) in zip(covered, covered[1:], strict=False):
        if c == b - 1:
            repeated += 1
        else:
            assert c == b and len(whole[b - 1]) + 1 + len(whole[c]) > max_chars
    assert repeated and covered[0][0] == 0 and covered[-1][1] == len(whole)


def test_a_long_paragraph_is_split_between_sentences_with_one_repeated():
    essay = read(GENERATED, "essay.md")
    paras = [p.text for p in paragraphs(essay) if p.kind == "body"]
    chunks = chunks_of(essay, "sentences", max_chars=400, min_chars=0)
    assert all(c["metadata"]["chars"] <= 400 for c in chunks)
    split = 0
    for paragraph in paras:
        held = [c for c in chunks if c["metadata"]["part_of"] and c["text"] in paragraph]
        if len(paragraph) > 400:
            assert_split_with_one_repeated(held, paragraph, 400)
            split += 1
    assert split >= 5

    notice = read(GENERATED, "notice.pdf")
    long = next(p.text for p in paragraphs(notice) if len(p.text) > 1200)
    held = [c for c in chunks_of(notice, "sentences") if c["metadata"]["part_of"]]
    assert_split_with_one_repeated(held, long, 1200)
    assert all(c["text"] in long for c in held)


def test_a_sentence_holding_a_reference_is_not_split_up_to_half_over():
    body = "The rule applies to every pitch holder in the market and to " + "each of their assistants " * 14
    plain = chunks_of(pdf_document([body + "this year."]), "sentences", max_chars=300)
    cited = chunks_of(pdf_document([body + "under section 10(46)."]), "sentences", max_chars=300)
    assert len(plain) == 2 and all("chunk.long-sentence" in c["metadata"]["rules"] for c in plain)
    (chunk,) = cited
    assert 300 < chunk["metadata"]["chars"] <= 450 and "chunk.long-sentence" not in chunk["metadata"]["rules"]
    assert chunk["metadata"]["references"] == ["section 10(46)"]


def test_a_word_longer_than_max_chars_is_cut_and_flagged():
    chunks = chunks_of(pdf_document(["x" * 700]), "sentences", max_chars=300)
    assert [c["metadata"]["chars"] for c in chunks] == [300, 300, 100]
    assert all(c["metadata"]["rules"] == "chunk.long-sentence" for c in chunks)
    assert [c["metadata"]["part_of"] for c in chunks] == ["1/3", "2/3", "3/3"]


def test_a_piece_under_min_chars_joins_its_neighbour_under_the_same_parent():
    chunks = chunks_of_md("Short line.\n\n" + "A long sentence about rates. " * 30)
    assert len(chunks) == 1 or all(c["metadata"]["chars"] >= 200 for c in chunks[:-1])

    # the last piece of a split paragraph is small; it joins the paragraph after it when the two fit
    first = "The first sentence of the long paragraph is here. " * 8 + "A tail sentence ends it."
    after = "The next paragraph says one more thing about the fees for the market."
    split = chunks_of_md(first + "\n\n" + after, "sentences", max_chars=400, min_chars=200)
    assert [c["metadata"]["part_of"] for c in split] == ["1/2", "2/2"]
    assert split[-1]["text"].endswith("A tail sentence ends it.\n\n" + after)
    assert all(c["metadata"]["chars"] <= 400 for c in split)
    alone = chunks_of_md(first + "\n\n" + after, "sentences", max_chars=400, min_chars=0)
    assert [c["text"] for c in alone][-1] == after and len(alone) == 3

    # a small piece stays when no neighbour under the same heading fits it
    stays = chunks_of_md("# A\n\nTiny.\n\n# B\n\n" + "Word " * 60, "sentences", max_chars=300)
    assert [c["text"] for c in stays][0] == "Tiny."


def assert_sizes(chunks, max_chars=1200, min_chars=200):
    """No chunk over max_chars; none under min_chars but the document's last."""
    sizes = [c["metadata"]["chars"] for c in chunks]
    assert all(size <= max_chars for size in sizes), sizes
    assert all(size >= min_chars for size in sizes[:-1]), sizes


def assert_whole_sentences(chunks, text):
    """Every piece is made of the text's sentences, whole: no word or sentence is cut."""
    whole = [s for paragraph in text.split("\n\n") for s in split_sentences(paragraph)]
    for chunk in chunks:
        for paragraph in chunk["text"].split("\n\n"):
            assert all(s in whole for s in split_sentences(paragraph)), paragraph


def rate_sentences(count):
    return " ".join(
        f"Stall {i} pays the rate set for the pitches in row {i} of the market." for i in range(count)
    )


def test_a_short_piece_before_a_split_paragraph_joins_its_first_piece():
    """R17 (a): the short paragraph takes the room the first piece of the next one leaves."""
    text = "Short line.\n\n" + "A long sentence about rates. " * 60
    chunks = chunks_of_md(text, "sentences")
    assert_sizes(chunks)
    assert_whole_sentences(chunks, text)
    assert chunks[0]["text"].startswith("Short line.\n\nA long sentence about rates. A long")
    assert [c["metadata"]["part_of"] for c in chunks] == ["1/2", "2/2"]
    assert split_sentences(chunks[1]["text"])[0] == split_sentences(chunks[0]["text"].split("\n\n")[1])[-1]

    # a paragraph that would fill a chunk alone is split too, when no piece is left small
    fills = rate_sentences(17) + " The last rule is the same for every row of the whole market."
    assert len("Short line.\n\n" + fills) > 1200 >= len(fills)
    text = "Short line.\n\n" + fills
    chunks = chunks_of_md(text, "sentences")
    assert len(chunks) == 2 and chunks[0]["text"].startswith("Short line.\n\nStall 0 ")
    assert_sizes(chunks)
    assert_whole_sentences(chunks, text)


def test_the_last_piece_of_a_split_paragraph_takes_sentences_from_the_one_before():
    """R17 (b): the boundary moves back by whole sentences; the overlap follows it."""
    paragraph = rate_sentences(18)
    greedy = chunks_of_md(paragraph, "sentences", min_chars=0)
    assert [c["metadata"]["chars"] < 200 for c in greedy] == [False, True]  # what packing alone gives
    chunks = chunks_of_md(paragraph, "sentences")
    assert_sizes(chunks)
    assert_split_with_one_repeated(chunks, paragraph, 1200)
    assert all(c["metadata"]["chars"] >= 200 for c in chunks)


def test_a_small_piece_stays_when_no_rebalancing_fits_it():
    """R17 (c): a tiny paragraph between two one-sentence paragraphs that each fill a chunk."""
    filling = "The " + "long " * 238 + "end."
    assert len(filling) == 1198  # with "Tiny." it would be 1,205
    text = f"{filling}\n\nTiny.\n\n{filling}"
    chunks = chunks_of_md(text, "sentences")
    assert [c["text"] for c in chunks] == [filling, "Tiny.", filling]
    assert all(c["metadata"]["chars"] <= 1200 for c in chunks)
    assert all("chunk.long-sentence" not in c["metadata"]["rules"] for c in chunks)


def test_tables_and_images_are_their_own_chunks_in_sentences():
    text = "Before the table. " * 5 + "\n\n| Section | Rate |\n|---|---|\n| 80C | 5% |\n\n" + "After it. " * 5
    chunks = chunks_of_md(text, "sentences", min_chars=0)
    assert [c["metadata"]["kind"] for c in chunks] == ["text", "table", "text"]
    assert chunks[1]["text"] == "| Section | Rate |\n| --- | --- |\n| 80C | 5% |"
    assert len({c["metadata"]["parent_id"] for c in chunks}) == 1


# --- every document -------------------------------------------------------------------------------------


def test_every_reference_document_gives_valid_pieces_under_its_plan():
    for chunk in all_chunks(DEMO) + all_chunks(GENERATED):
        meta = chunk["metadata"]
        assert meta["strategy"] in STRATEGY_FUNCTIONS
        uuid.UUID(meta["parent_id"])
        assert meta["parent_cite"].startswith(meta["path"])
        assert meta["part_of"] == "" or meta["part_of"].count("/") == 1
        assert list(meta) == list(METADATA_KEYS)
        assert all(len(meta[key]) <= CHUNK_LIST_LIMIT for key in LIST_KEYS)


def test_a_heading_left_out_by_include_is_not_in_the_heading_path():
    document = md("# Kept\n\n## Blocked\n\n" + "Body text under both headings. " * 10)
    blocked = next(line for line in document.lines() if line.text == "Blocked")
    blocked.status = "blocked"
    (chunk,) = to_chunks(document, Config(), choice=plan(document, "sentences"))
    assert (chunk["metadata"]["heading"], chunk["metadata"]["heading_path"]) == ("Kept", "Kept")
    assert "Blocked" not in chunk["embedding_text"]


# --- numbered-paragraphs, sections and question-answer: what every chunk keeps ---------------------------

THREE = ("numbered-paragraphs", "sections", "question-answer")
STRUCTURED = ("notice.pdf", "faq.docx", "manual.md", "manual.html", "contract.docx")


def test_numbered_paragraphs_sections_and_question_answer_are_registered():
    assert set(THREE) <= set(STRATEGY_FUNCTIONS)
    for name, strategy in [
        ("notice.pdf", "numbered-paragraphs"),
        ("contract.docx", "numbered-paragraphs"),
        ("faq.docx", "question-answer"),
        ("manual.md", "sections"),
        ("manual.html", "sections"),
    ]:
        chunks = chunks_of(read(GENERATED, name))  # the rules choose the strategy
        assert {c["metadata"]["strategy"] for c in chunks} == {strategy}, name


def at_sentence_bounds(block, paras):
    """True when `block` is a run of whole sentences of one paragraph (or a whole paragraph)."""
    for para in paras:
        ranges = sentences(para.text, para.breaks)
        starts = {start for start, _ in ranges} | {0}
        ends = {start + len(para.text[start:stop].rstrip()) for start, stop in ranges} | {len(para.text)}
        at = para.text.find(block)
        while at >= 0:
            if at in starts and at + len(block) in ends:
                return True
            at = para.text.find(block, at + 1)
    return False


def assert_clean(chunks, document, max_chars):
    """No chunk over max_chars, but a long sentence's parts and a sentence that holds a reference (up
    to half over); and no word or sentence cut: each block of a text chunk is whole sentences of one
    paragraph, after the number a piece of a split paragraph repeats."""
    paras = paragraphs(document)
    for chunk in chunks:
        meta = chunk["metadata"]
        long = "chunk.long-sentence" in meta["rules"]
        assert meta["chars"] == len(chunk["text"])
        assert (
            meta["chars"] <= max_chars
            or long
            or (meta["chars"] <= max_chars * 1.5 and meta["references"] != [""])
        ), (meta["chars"], chunk["text"][:80])
        if meta["kind"] != "text" or long:
            continue
        for i, block in enumerate(chunk["text"].split("\n\n")):
            token = numbering(block) if i == 0 else None
            assert at_sentence_bounds(block, paras) or (
                token and at_sentence_bounds(block[len(token) :].lstrip(), paras)
            ), block[:80]


@pytest.mark.parametrize("max_chars", [1200, 400])
@pytest.mark.parametrize("strategy", THREE)
def test_the_three_strategies_cut_no_word_or_sentence_and_keep_to_max_chars(strategy, max_chars):
    for name in STRUCTURED:
        document = read(GENERATED, name)
        chunks = chunks_of(document, strategy, max_chars=max_chars)
        assert chunks, name
        assert_clean(chunks, document, max_chars)
        assert all(c["metadata"]["strategy"] == strategy for c in chunks)
        for chunk in chunks:
            part_of = chunk["metadata"]["part_of"]
            if part_of:
                n, m = map(int, part_of.split("/"))
                assert 1 <= n <= m and m > 1


# --- numbered-paragraphs ----------------------------------------------------------------------------------


def test_a_proviso_and_an_explanation_are_in_their_paragraphs_chunk():
    notice = read(GENERATED, "notice.pdf")
    found = shape(paragraphs(notice), notice)
    notes = found.provisos + found.explanations
    assert len(found.provisos) == 2 and len(found.explanations) == 1
    chunks = chunks_of(notice)
    for note in notes:
        (holder,) = [c for c in chunks if note.text in c["text"]]  # a paragraph under max_chars stays whole
        assert holder["text"].startswith(numbering(note.text) or "")
    assert [numbering(c["text"]) for c in chunks] == ["1.", "2.", "3.", "4.", "5.", "5.", "6.", "7.", "8."]

    # a proviso and an explanation in paragraphs of their own stay with the numbered paragraph above them
    texts = [
        "1. Every stall holder must keep the pitch clean during the whole of the market day.",
        "Provided that a stall holder who sells only flowers may leave the pitch until the evening.",
        "Explanation.—A pitch is the space marked out for one stall on the market square.",
        "2. The market officer may inspect any stall at any time while the market is open.",
        "3. A permit that is withdrawn may not be applied for again in the same trading period.",
    ]
    document = pdf_document(texts)
    chunks = chunks_of(document, "numbered-paragraphs", min_chars=0)
    assert [c["text"] for c in chunks] == ["\n\n".join(texts[:3]), texts[3], texts[4]]
    split = chunks_of(document, "numbered-paragraphs", max_chars=200, min_chars=0)
    held = [c for c in split if "Provided that" in c["text"] or "Explanation" in c["text"]]
    assert held and all(c["text"].startswith("1. ") for c in held)
    assert all(c["metadata"]["part_of"] for c in held)
    assert_clean(split, document, 200)


def test_every_piece_of_a_split_numbered_paragraph_starts_with_its_number():
    notice = read(GENERATED, "notice.pdf")
    long = next(p for p in paragraphs(notice) if len(p.text) > 2000)
    for max_chars in (1200, 400):
        pieces_of_5 = [
            c
            for c in chunks_of(notice, "numbered-paragraphs", max_chars=max_chars)
            if c["text"].startswith("5. ")
        ]
        m = len(pieces_of_5)
        assert m > 1
        assert [c["metadata"]["part_of"] for c in pieces_of_5] == [f"{n}/{m}" for n in range(1, m + 1)]
        assert pieces_of_5[0]["text"] == long.text[: len(pieces_of_5[0]["text"])]
        for chunk in pieces_of_5[1:]:
            body = chunk["text"][len("5. ") :]
            assert body in long.text and not long.text.startswith(body)
            assert chunk["metadata"]["chars"] <= max_chars
        assert_clean(pieces_of_5, notice, max_chars)

    # any number form: (a) and its pieces
    first = "(a) " + rate_sentences(12)
    document = pdf_document(
        [first, "(b) The second item is short but it is long enough to stand alone here."]
    )
    chunks = chunks_of(document, "numbered-paragraphs", max_chars=400, min_chars=0)
    pieces_of_a = [c for c in chunks if c["metadata"]["part_of"]]
    assert len(pieces_of_a) > 2 and all(c["text"].startswith("(a) ") for c in pieces_of_a)
    assert chunks[-1]["text"].startswith("(b) ") and not chunks[-1]["metadata"]["part_of"]
    assert all(c["metadata"]["chars"] <= 400 for c in chunks)


def test_numbered_paragraphs_have_their_section_as_parent_and_short_ones_join_the_next():
    contract = read(GENERATED, "contract.docx")
    chunks = chunks_of(contract)
    by_section = {}
    for chunk in chunks:
        by_section.setdefault(chunk["metadata"]["heading_path"], []).append(chunk)
    services = by_section["Cleaning Services Agreement › 2. The Services"]
    (both,) = services  # 2.1 and 2.2 are each under min_chars: they join, in their section only
    assert both["text"].startswith("2.1 ") and "\n\n2.2 " in both["text"]
    assert not any("2.2 " in c["text"] and "3.1 " in c["text"] for c in chunks)
    parents = [c["metadata"]["parent_id"] for c in chunks]
    assert len(set(parents)) == len(by_section) == 5  # one parent per section
    assert all(len({c["metadata"]["parent_id"] for c in group}) == 1 for group in by_section.values())
    assert all(c["metadata"]["parent_cite"].startswith("contract.docx, ") for c in chunks)

    # the text before a section's first numbered paragraph is packed as `sentences` packs it
    definitions = "Cleaning Services Agreement › 1. Definitions"
    for max_chars in (1200, 400):
        texts = {
            strategy: [
                c["text"]
                for c in chunks_of(contract, strategy, max_chars=max_chars)
                if c["metadata"]["heading_path"] == definitions
            ]
            for strategy in ("numbered-paragraphs", "sentences")
        }
        assert texts["numbered-paragraphs"] == texts["sentences"] and texts["sentences"]

    # the text before the first numbered paragraph is packed like sentences, under the same parent
    notice = read(GENERATED, "notice.pdf")
    texts = [
        "The Council gives this notice under its market powers.",
        *[p.text for p in paragraphs(notice)][1:],
    ]
    chunks = chunks_of(pdf_document(texts), "numbered-paragraphs")
    assert chunks[0]["text"].startswith(texts[0] + "\n\n1. ")  # a short preamble joins the next one
    assert len({c["metadata"]["parent_id"] for c in chunks}) == 1


def test_a_numbered_paragraphs_parent_is_its_section():
    notice = read(GENERATED, "notice.pdf")
    chunks = chunks_of(notice)
    heading = next(line for line in notice.lines() if line.text == "Notice of Temporary Market Stall Permits")
    file_id = chunks[0]["metadata"]["file_id"]
    assert {c["metadata"]["parent_id"] for c in chunks} == {
        str(uuid.uuid5(NAMESPACE, f"{file_id}|parent|section:{heading.id}"))
    }
    assert {c["metadata"]["heading_path"] for c in chunks} == {"Notice of Temporary Market Stall Permits"}
    pages = sorted({line.page for line in notice.lines()})
    assert {c["metadata"]["parent_cite"] for c in chunks} == {
        f"notice.pdf, p. {pages[0]}" if len(pages) == 1 else f"notice.pdf, pp. {pages[0]}–{pages[-1]}"
    }


# --- question-answer ----------------------------------------------------------------------------------------


def test_a_question_is_never_apart_from_its_answer():
    faq = read(GENERATED, "faq.docx")
    questions = [q.text for q, _ in shape(paragraphs(faq), faq).qa]
    assert len(questions) == 12
    for max_chars in (1200, 400):
        chunks = chunks_of(faq, "question-answer", max_chars=max_chars)
        for chunk in chunks:
            assert chunk["text"].startswith("Q.")
            (question,) = [q for q in questions if q in chunk["text"]]  # a pair never joins another pair
            assert chunk["text"].startswith(
                question + "\n\nA. " if not chunk["metadata"]["part_of"] else question
            )
            assert chunk["text"].split("\n\n")[1]  # and never stands without its answer
        assert len({c["metadata"]["parent_id"] for c in chunks}) == 12
        assert_clean(chunks, faq, max_chars)

    # a long answer is split between sentences, each piece repeating the question
    renew = [c for c in chunks_of(faq) if c["text"].startswith("Q. Can I renew my loans?\n\n")]
    assert [c["metadata"]["part_of"] for c in renew] == ["1/2", "2/2"]
    assert renew[0]["text"].startswith("Q. Can I renew my loans?\n\nA. Most items can be renewed")
    assert len({c["metadata"]["parent_id"] for c in renew}) == 1


def test_small_pairs_stay_apart_and_text_outside_pairs_is_packed_like_sentences():
    intro = "The library answers the questions it is asked most often below. " * 4
    text = (
        intro
        + "\n\nQ. Is it free?\n\nA. Yes.\n\nQ. Is it open on Sunday?\n\nA. No.\n\nQ. Can I park?\n\nA. Yes, at the back."
    )
    document = md(text)
    chunks = chunks_of(document, "question-answer")
    assert [c["text"] for c in chunks] == [
        intro.strip(),
        "Q. Is it free?\n\nA. Yes.",
        "Q. Is it open on Sunday?\n\nA. No.",
        "Q. Can I park?\n\nA. Yes, at the back.",
    ]
    parents = [c["metadata"]["parent_id"] for c in chunks]
    file_id = chunks[0]["metadata"]["file_id"]
    assert parents[0] == str(uuid.uuid5(NAMESPACE, f"{file_id}|parent|document"))
    assert len(set(parents)) == 4


# --- sections -----------------------------------------------------------------------------------------------


def test_sections_carry_their_heading_path():
    for name in ("manual.md", "manual.html"):
        manual = read(GENERATED, name)
        headings = {p.text: p.heading_path for p in paragraphs(manual) if p.kind == "heading"}
        whole = chunks_of(manual)  # each part of the manual fits in one chunk
        assert [(c["metadata"]["heading_path"], c["text"].split("\n\n")[0]) for c in whole] == [
            ("Installing Pennant", "Installing Pennant"),
            ("Making backups", "Making backups"),
            ("Getting files back", "Getting files back"),
        ]
        assert "\n\nFirst start\n\n" not in whole[0]["text"] and whole[0]["text"].endswith("\n\nFirst start")
        small = chunks_of(manual, "sections", max_chars=400)
        for chunk in small:
            first = chunk["text"].split("\n\n")[0]
            path = tuple(chunk["metadata"]["heading_path"].split(" › "))
            assert first in headings and headings[first][: len(path)] == path  # it reads on its own
        schedule = next(c for c in small if c["text"].startswith("Setting a schedule\n\n"))
        assert schedule["metadata"]["heading_path"] == "Making backups › Setting a schedule"
        assert schedule["metadata"]["heading"] == "Setting a schedule"
        assert schedule["embedding_text"].startswith(f"{manual.title} > Setting a schedule\n")
        assert [c["text"] for c in chunks_of(read(GENERATED, "manual.md"), "sections", max_chars=400)] == [
            c["text"] for c in small
        ]


def test_a_long_section_is_split_between_paragraphs_then_sentences_each_piece_under_its_heading():
    body = "\n\n".join(rate_sentences(4) for _ in range(5)) + "\n\n" + rate_sentences(30)
    text = (
        "# Rates\n\n"
        + body
        + "\n\n# Fees\n\nThe fees are set each year by the council and published in spring."
    )
    chunks = chunks_of_md(text, "sections")
    rates = [c for c in chunks if c["metadata"]["heading_path"] == "Rates"]
    m = len(rates)
    assert m > 2 and all(c["text"].startswith("Rates\n\nStall ") for c in rates)
    assert [c["metadata"]["part_of"] for c in rates] == [f"{n}/{m}" for n in range(1, m + 1)]
    assert len({c["metadata"]["parent_id"] for c in rates}) == 1
    assert_clean(chunks, md(text), 1200)
    assert chunks[-1]["text"] == "Fees\n\nThe fees are set each year by the council and published in spring."


def test_a_heading_with_nothing_under_it_joins_the_next_section():
    """Review Focus 4: a run of headings, and a section under min_chars, join the next section under
    the same parent; the heading stays in the heading path."""
    fourth = "The fourth section says what a stall holder must do before the market opens. " * 4
    fifth = rate_sentences(14)
    text = f"# Part II\n\n## Section 4\n\n{fourth}\n\n## Section 5\n\n{fifth}"
    chunks = chunks_of_md(text, "sections")
    assert [c["metadata"]["heading_path"] for c in chunks] == ["Part II › Section 4", "Part II › Section 5"]
    assert chunks[0]["text"] == f"Part II\n\nSection 4\n\n{fourth.strip()}"
    assert all(c["metadata"]["chars"] >= 200 for c in chunks)

    # the heading still leads into the next section when that one is split
    long = chunks_of_md(f"# Part II\n\n## Section 4\n\n{rate_sentences(30)}", "sections")
    assert long[0]["text"].startswith("Part II\n\nSection 4\n\nStall 0 ")
    assert all(c["text"].startswith(("Part II\n\nSection 4\n\n", "Section 4\n\n")) for c in long)
    assert {c["metadata"]["heading_path"] for c in long} == {"Part II › Section 4"}
    assert len(long) > 1 and all(c["metadata"]["chars"] <= 1200 for c in long)

    # a short section joins the next one under the same parent
    joined = chunks_of_md(
        f"# A\n\n## A1\n\nShort.\n\n## A2\n\n{fourth}\n\n## A3\n\n{fifth}\n\n# B\n\n{fourth}", "sections"
    )
    assert joined[0]["text"].startswith("A\n\nA1\n\nShort.\n\nA2\n\nThe fourth section")
    assert joined[0]["metadata"]["heading_path"] == "A"
    assert all(c["metadata"]["chars"] >= 200 for c in joined)

    # a title with nothing under it, before the chapters at its level, leads into the first of them,
    # even one that fits a chunk alone but not with the title (R17 a: it is split to make room)
    chapter = rate_sentences(16) + " Every stall in the market pays its rate on the first day of each month"
    chapter += " and keeps the receipt it is given at the desk."
    assert len(f"Chapter 1\n\n{chapter}") <= 1200 < len(f"Handbook\n\nChapter 1\n\n{chapter}")
    titled = chunks_of_md(f"# Handbook\n\n# Chapter 1\n\n{chapter}\n\n# Chapter 2\n\n{fourth}", "sections")
    assert titled[0]["text"].startswith("Handbook\n\nChapter 1\n\nStall 0 ")
    assert [c["metadata"]["heading_path"] for c in titled] == ["Chapter 1", "Chapter 1", "Chapter 2"]
    assert all(200 <= c["metadata"]["chars"] <= 1200 for c in titled)

    # manual.md's "First start" has nothing under it and no section after it in its part: no tiny chunk
    manual = read(GENERATED, "manual.md")
    small = chunks_of(manual, "sections", max_chars=400)
    (holder,) = [c for c in small if "First start" in c["text"]]
    assert holder["text"] != "First start" and holder["text"].endswith("\n\nFirst start")
    assert holder["metadata"]["heading_path"].startswith("Installing Pennant")

    # an empty last subsection after a split one goes at the end of its last piece, which keeps its
    # parent and its part_of
    text = f"# Part 1\n\n{fourth}\n\n## Rates\n\n{rate_sentences(25)}\n\n## Empty\n\n# Part 2\n\n{fourth}"
    chunks = chunks_of_md(text, "sections")
    rates = [c for c in chunks if c["metadata"]["heading_path"] == "Part 1 › Rates"]
    assert [c["metadata"]["part_of"] for c in rates] == ["1/2", "2/2"]
    assert rates[-1]["text"].endswith("\n\nEmpty") and len({c["metadata"]["parent_id"] for c in rates}) == 1
    assert all(c["metadata"]["chars"] >= 200 for c in chunks)
