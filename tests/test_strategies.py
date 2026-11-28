"""What the chunking strategies cut a document into: `single` and `sentences` (spec sections 3 and 5)."""

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
from ragnaroq.semantic.paragraphs import paragraphs
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
