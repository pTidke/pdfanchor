"""The HTML and Markdown readers: element paths, line ranges, hidden text, images that never
leave the file's folder, and the limits that stop a hostile page."""

import base64
import socket
import struct
import time
import zlib
from functools import partial
from pathlib import Path
from urllib.parse import quote_from_bytes

import pytest

pytest.importorskip("lxml")
pytest.importorskip("markdown_it")

import lxml.html  # noqa: E402

from ragnaroq import evaluate, policy, readers  # noqa: E402
from ragnaroq.anchors import HtmlAnchor, MdAnchor  # noqa: E402
from ragnaroq.config import Config  # noqa: E402
from ragnaroq.readers import ReaderRefused, read_bytes  # noqa: E402

DEMO = Path(__file__).parent.parent / "references" / "demo"
HIDDEN = "Not shown on the page; a reader of the page never sees it."
EQUATION = (
    "An equation is read as its plain symbols; fractions, powers and layout are lost; compare with the page."
)


def png() -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def read_html(src, name="t.html", config=None, **kw):
    data = src.encode("utf-8") if isinstance(src, str) else src
    return read_bytes(data, name, config or Config(), **kw)


def body(markup, **kw):
    page = f"<!DOCTYPE html><html><head><title>T</title></head><body>{markup}</body></html>"
    return read_html(page, **kw)


def read_md(src, name="t.md", config=None, **kw):
    data = src.encode("utf-8") if isinstance(src, str) else src
    return read_bytes(data, name, config or Config(), **kw)


def lines(result):
    document = result.document
    return [
        (line.anchor.path if line.anchor.type == "html" else line.anchor.lines, line.text)
        for line in document.lines()
    ]


def texts(result):
    return [line.text for line in result.document.lines()]


def rules(flags):
    return [flag.rule for flag in flags]


def page_flags(result):
    return rules(result.document.pages[0].flags)


def line_flags(result, text):
    return rules(next(line for line in result.document.lines() if line.text == text).flags)


def figures(result):
    return result.document.pages[0].figures


def own(figure):
    """A figure's flags from the reader; `image.no-description` is the policy's own verdict."""
    return [rule for rule in rules(figure.flags) if rule != "image.no-description"]


# --- the plan's tests ----------------------------------------------------------------------------


def test_the_demo_page_scores_by_anchor():
    report = evaluate.run_exact(DEMO, partial(readers.read_bytes, base=DEMO))
    scored = {f.name: f for f in report.files}
    assert scored["notice.html"].error is None
    assert scored["notice.html"].score.ok, scored["notice.html"].score
    assert scored["notice.html"].score.right == 8
    result = read_bytes((DEMO / "notice.html").read_bytes(), "notice.html", Config(), base=DEMO)
    logo, remote = figures(result)
    assert own(logo) == [] and logo.description == "Logo" and logo.id == "p1-f1"
    assert result.images[logo.id] == (DEMO / "logo.png").read_bytes()
    assert own(remote) == ["image.remote-not-fetched"] and remote.path == "https://example.org/x.png"
    assert [t.id for t in result.document.pages[0].tables] == ["p1-t1"]


def test_the_demo_readme_scores_by_anchor():
    report = evaluate.run_exact(DEMO, partial(readers.read_bytes, base=DEMO))
    scored = {f.name: f for f in report.files}
    assert scored["readme.md"].error is None
    assert scored["readme.md"].score.ok, scored["readme.md"].score
    assert scored["readme.md"].score.right == 9
    assert not scored["readme.md"].score.extra
    result = read_bytes((DEMO / "readme.md").read_bytes(), "readme.md", Config(), base=DEMO)
    (diagram,) = figures(result)
    assert diagram.anchor == MdAnchor(lines=(15, 15)) and own(diagram) == []
    assert result.images[diagram.id] == (DEMO / "diagram.png").read_bytes()


def test_scripts_styles_and_templates_are_ignored():
    markup = (
        "<p>kept</p><script>var a = 'not text';</script><style>p { color: red }</style>"
        "<template><p>in template</p></template><noscript><p>no script</p></noscript>"
        "<!-- a comment --><?php echo 1 ?><p>also kept</p>"
        "<div>before<script>x()</script>after</div>"
    )
    result = body(markup)
    assert lines(result) == [
        ("/html/body/p[1]", "kept"),
        ("/html/body/p[2]", "also kept"),
        ("/html/body/div", "beforeafter"),
    ]


def test_a_remote_image_is_kept_unfetched_with_a_flag(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(socket, "socket", refuse)
    markup = (
        '<img src="https://example.org/x.png" alt="One"><img src="http://example.org/y.png">'
        '<img src="//cdn.example.org/z.png" alt="Three"><img src="HTTPS://example.org/Q.png">'
    )
    result = body(markup)
    assert [f.path for f in figures(result)] == [
        "https://example.org/x.png",
        "http://example.org/y.png",
        "//cdn.example.org/z.png",
        "HTTPS://example.org/Q.png",
    ]
    assert all(own(f) == ["image.remote-not-fetched"] for f in figures(result))
    assert result.images == {}
    assert [f.description_source for f in figures(result)] == ["alt", "none", "alt", "none"]
    md = read_md("![One](https://example.org/x.png)\n\n![Two](//cdn.example.org/z.png)\n")
    assert [f.path for f in figures(md)] == ["https://example.org/x.png", "//cdn.example.org/z.png"]
    assert all(own(f) == ["image.remote-not-fetched"] for f in figures(md))
    assert md.images == {}


def test_a_local_image_is_read_from_beside_the_file(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "logo.png").write_bytes(png())
    (tmp_path / "sub" / "my pic.png").write_bytes(png() + b"x")
    result = body(
        '<img alt="Logo" src="logo.png"><img src="sub/my%20pic.png?v=2#frag"><img src="./sub/../logo.png">',
        base=tmp_path,
    )
    one, two, three = figures(result)
    assert (one.id, two.id, three.id) == ("p1-f1", "p1-f2", "p1-f3")
    assert result.images == {"p1-f1": png(), "p1-f2": png() + b"x", "p1-f3": png()}
    assert [own(f) for f in (one, two, three)] == [[], [], []]
    assert [f.path for f in (one, two, three)] == ["", "", ""]  # only remote images carry a path
    assert [f.anchor for f in (one, two, three)] == [
        HtmlAnchor(path="/html/body/img[1]"),
        HtmlAnchor(path="/html/body/img[2]"),
        HtmlAnchor(path="/html/body/img[3]"),
    ]
    md = read_md("![Logo](logo.png)\n\n![](sub/my%20pic.png)\n", base=tmp_path)
    assert [own(f) for f in figures(md)] == [[], []]
    assert list(md.images.values()) == [png(), png() + b"x"]


def test_an_image_path_leaving_the_folder_is_refused(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(png())
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "inner.png").write_bytes(png())
    (site / "link.png").symlink_to(outside)
    (site / "linkdir").symlink_to(tmp_path / "elsewhere")
    (site / "inside.png").write_bytes(png())
    sources = [
        "../../etc/passwd.png",
        "../secret.png",
        "link.png",
        "linkdir/inner.png",
        str(outside),
        str(site / "inside.png"),  # absolute, even though it is in the folder
        "/etc/passwd",
        "%2e%2e/secret.png",
        "file:///etc/passwd",
        "FILE:secret.png",
        "ftp://example.org/x.png",
        "C:\\Windows\\x.png",
        "\\\\host\\share\\x.png",
    ]
    result = body("".join(f'<img src="{src}">' for src in sources), base=site)
    assert [own(f) for f in figures(result)] == [["image.outside-folder"]] * len(sources)
    assert result.images == {}
    md = read_md(
        "\n\n".join(f"![x]({src.replace(' ', '%20').replace(chr(92), '%5C')})" for src in sources), base=site
    )
    assert [own(f) for f in figures(md)] == [["image.outside-folder"]] * len(sources)
    assert md.images == {}


def test_a_data_uri_image_is_decoded_within_the_size_limit():
    encoded = base64.b64encode(png()).decode()
    padded = base64.b64encode(png() + b"x").decode()
    assert padded.endswith("=")  # so that the padding can be left off below
    percent = quote_from_bytes(png(), safe="")
    result = body(
        f'<img alt="a" src="data:image/png;base64,{encoded}">'
        f'<img src="data:image/png,{percent}">'
        f'<img src="data:image/png;base64,{padded.rstrip("=")}">'  # padding left off, as browsers accept
        f'<img src="data:image/png;base64,{encoded}" hidden>'
    )
    first, second, third, fourth = figures(result)
    assert [result.images[f.id] for f in (first, second, third, fourth)] == [
        png(),
        png(),
        png() + b"x",
        png(),
    ]
    assert own(first) == [] and own(second) == [] and own(third) == []
    assert first.path == ""  # nothing to cite beyond the figure itself
    assert own(fourth) == ["html.hidden"]
    small = Config(max_image_bytes=len(encoded) - 1)
    result = body(f'<img src="data:image/png;base64,{encoded}">', config=small)
    assert own(figures(result)[0]) == ["image.too-large"] and result.images == {}
    result = body(f'<img src="data:image/png;base64,{encoded}">', config=Config(max_image_bytes=len(encoded)))
    assert own(figures(result)[0]) == [] and result.images == {"p1-f1": png()}
    bad = body('<img src="data:image/png;base64,@@@@!"><img src="data:image/png;base64,"><img src="data:x">')
    assert [own(f) for f in figures(bad)] == [["image.unreadable"]] * 3
    assert bad.images == {}
    md = read_md(f"![a](data:image/png;base64,{encoded})\n")
    assert md.images == {"p1-f1": png()} and own(figures(md)[0]) == []


def test_markdown_lines_are_anchored_to_their_source_lines():
    result = read_md("# Title\n\nfirst line\nsecond line\n\nlast\n")
    assert [(line.anchor, line.text, line.role) for line in result.document.lines()] == [
        (MdAnchor(lines=(1, 1)), "Title", "heading"),
        (MdAnchor(lines=(3, 4)), "first line second line", "body"),
        (MdAnchor(lines=(6, 6)), "last", "body"),
    ]
    two = read_md("intro\n\nfirst\nsecond\n")
    assert two.document.pages[0].lines[1].anchor == MdAnchor(lines=(3, 4))


# --- R18 (a): text outside the listed blocks -----------------------------------------------------


def test_a_text_outside_the_listed_blocks_is_read():
    markup = (
        "loose"
        "<div>in div</div>"
        "<figure><img src=missing.png><figcaption>Cap</figcaption></figure>"
        "<details>Detail text<summary>More</summary></details>"
        "<address>Me</address>"
        "<p>A <b>bold <i>it</i></b> and <a href=x>link</a><span> s</span></p>"
    )
    result = body(markup)
    assert lines(result) == [
        ("/html/body", "loose"),
        ("/html/body/div", "in div"),
        ("/html/body/figure/figcaption", "Cap"),
        ("/html/body/details", "Detail text"),
        ("/html/body/details/summary", "More"),
        ("/html/body/address", "Me"),
        ("/html/body/p", "A bold it and link s"),
    ]
    (figure,) = figures(result)
    assert (figure.description, figure.description_source) == ("Cap", "caption")
    assert figure.anchor == HtmlAnchor(path="/html/body/figure/img")


def test_a_own_text_excludes_nested_blocks_which_follow_as_lines_of_their_own():
    result = body(
        "<ul><li>one<ul><li>two</li><li>three</li></ul></li><li>four</li></ul>"
        "<div>before<p>mid</p>after</div>"
        "<span>lead<div>wrapped</div>trail</span>"
    )
    assert lines(result) == [
        ("/html/body/ul/li[1]", "one"),
        ("/html/body/ul/li[1]/ul/li[1]", "two"),
        ("/html/body/ul/li[1]/ul/li[2]", "three"),
        ("/html/body/ul/li[2]", "four"),
        ("/html/body/div", "before"),
        ("/html/body/div/p", "mid"),
        ("/html/body/div", "after"),
        ("/html/body", "lead"),
        ("/html/body/span/div", "wrapped"),
        ("/html/body", "trail"),
    ]
    assert [line.role for line in result.document.lines()][0] == "body"


def test_a_a_definition_list_headings_and_quotes_are_lines():
    result = body("<h2>Head</h2><dl><dt>Term</dt><dd>Meaning</dd></dl><blockquote>Quoted</blockquote>")
    assert lines(result) == [
        ("/html/body/h2", "Head"),
        ("/html/body/dl/dt", "Term"),
        ("/html/body/dl/dd", "Meaning"),
        ("/html/body/blockquote", "Quoted"),
    ]
    assert [line.role for line in result.document.lines()] == ["heading", "body", "body", "body"]


def test_a_every_anchor_is_the_path_lxml_gives_for_the_file_as_parsed():
    markup = (
        "<p>a</p><p>b<o:p>x</o:p></p><div><p>c</p><span><div>d</div></span><p>e</p></div>"
        "<table><tr><td>1<td>2<tr><td>3</table><table><tbody><tr><td>4</table>"
        "<ul><li>x<ul><li>y</li></ul></li></ul><script>1</script><p>f</p><img src=a.png><img src=b.png>"
    )
    source = f"<html><head><title>T</title></head><body>{markup}</body></html>"
    result = read_html(source)
    tree = lxml.html.document_fromstring(source.encode()).getroottree()
    known = {tree.getpath(element) for element in tree.iter() if isinstance(element.tag, str)}
    anchors = [line.anchor.path for line in result.document.lines()]
    anchors += [figure.anchor.path for figure in figures(result)]
    assert len(anchors) >= 12
    assert set(anchors) <= known
    assert "/html/body/table[2]/tbody/tr/td" in anchors  # a tbody only where the file has one
    assert "/html/body/table[1]/tr[2]/td" in anchors


# --- R18 (b): whitespace -------------------------------------------------------------------------


def test_b_whitespace_collapses_outside_pre_and_br_is_a_space_or_a_newline():
    result = body(
        "<p>  a \n\t b   <br> c<br/>d </p>"
        "<pre>  x\n  y\n</pre>"
        "<pre>a<br>b\n\nc\n</pre>"
        "<p>&nbsp;</p><p>1&nbsp;000</p><p> </p>"
    )
    assert lines(result) == [
        ("/html/body/p[1]", "a b c d"),
        ("/html/body/pre[1]", "  x\n  y"),
        ("/html/body/pre[2]", "a\nb\n\nc"),
        ("/html/body/p[3]", "1\xa0000"),
    ]


# --- R18 (c): dropped content and the title ------------------------------------------------------


def test_c_head_is_not_read_except_the_title_which_names_the_page():
    source = (
        "<html><head><title> The  Notice \n</title><meta name=x content='not text'>"
        "<style>p{}</style><script>1</script></head><body><p>Text</p></body></html>"
    )
    result = read_html(source, name="notice.html")
    assert texts(result) == ["Text"]
    assert result.document.pages[0].name == "The Notice"
    nameless = read_html("<html><head></head><body><p>Text</p></body></html>", name="plain.html")
    assert nameless.document.pages[0].name == "plain.html"
    bare = read_html("<p>Text</p>", name="bare.html")
    assert texts(bare) == ["Text"] and bare.document.pages[0].name == "bare.html"


# --- R18 (d): hidden content ---------------------------------------------------------------------


def test_d_hidden_content_is_read_and_flagged():
    result = body(
        "<p hidden>h1</p>"
        '<div style="color:red; display:none">h2<p>h3</p></div>'
        '<span style="VISIBILITY: hidden">h4</span>'
        '<p>vis <span style="display : none !important">h5</span> ok</p>'
        '<p style="display:block">shown</p><p style="color:red">plain</p>'
        "<ul hidden><li>h6</li></ul>"
    )
    found = {line.text: line for line in result.document.lines()}
    assert set(found) == {"h1", "h2", "h3", "h4", "vis h5 ok", "shown", "plain", "h6"}
    for text in ("h1", "h2", "h3", "h4", "vis h5 ok", "h6"):
        flags = found[text].flags
        assert [(f.rule, f.severity, f.message) for f in flags] == [("html.hidden", "review", HIDDEN)], text
    assert found["shown"].flags == [] and found["plain"].flags == []
    assert found["h3"].anchor.path == "/html/body/div/p"


# --- R18 (e): tables -----------------------------------------------------------------------------


def test_e_table_cells_are_lines_at_their_paths_and_a_nested_table_is_its_own():
    markup = (
        "<table><caption>Rates</caption>"
        "<thead><tr><th>Slab</th><th colspan=2>Rate</th></tr></thead>"
        "<tbody><tr><td rowspan=2>A</td><td>1</td><td>2</td></tr>"
        "<tr><td>3</td><td><table><tr><td>in</td></tr></table>tail</td></tr></tbody></table>"
        "<table><tr><td><p>para</p><p>two</p></td></tr></table>"
    )
    result = body(markup)
    p = "/html/body/table[1]"
    assert lines(result) == [
        (f"{p}/caption", "Rates"),
        (f"{p}/thead/tr/th[1]", "Slab"),
        (f"{p}/thead/tr/th[2]", "Rate"),
        (f"{p}/tbody/tr[1]/td[1]", "A"),
        (f"{p}/tbody/tr[1]/td[2]", "1"),
        (f"{p}/tbody/tr[1]/td[3]", "2"),
        (f"{p}/tbody/tr[2]/td[1]", "3"),
        (f"{p}/tbody/tr[2]/td[2]/table/tr/td", "in"),
        (f"{p}/tbody/tr[2]/td[2]", "tail"),
        ("/html/body/table[2]/tr/td/p[1]", "para"),
        ("/html/body/table[2]/tr/td/p[2]", "two"),
    ]
    found = result.document.lines()
    assert [(line.role, line.table_id) for line in found] == [
        ("body", None),
        *[("table", "p1-t1")] * 6,
        ("table", "p1-t2"),
        ("table", "p1-t1"),
        ("table", "p1-t3"),
        ("table", "p1-t3"),
    ]
    first, inner, third = result.document.pages[0].tables
    assert (first.id, first.rows, first.columns) == ("p1-t1", 3, 3)
    assert [(c.row, c.column, c.text, c.anchor.path.removeprefix(p)) for c in first.cells] == [
        (0, 0, "Slab", "/thead/tr/th[1]"),
        (0, 1, "Rate", "/thead/tr/th[2]"),
        (1, 0, "A", "/tbody/tr[1]/td[1]"),
        (1, 1, "1", "/tbody/tr[1]/td[2]"),
        (1, 2, "2", "/tbody/tr[1]/td[3]"),
        (2, 1, "3", "/tbody/tr[2]/td[1]"),
        (2, 2, "tail", "/tbody/tr[2]/td[2]"),
    ]
    assert (inner.id, inner.rows, inner.columns) == ("p1-t2", 1, 1)
    assert [(c.row, c.column, c.text) for c in inner.cells] == [(0, 0, "in")]
    assert [(c.text, c.anchor.path) for c in third.cells] == [("para\ntwo", "/html/body/table[2]/tr/td")]


# --- R18 (f): encodings --------------------------------------------------------------------------


def test_f_html_honours_a_declared_charset_and_a_bom_and_otherwise_guesses():
    cafe = "<p>caf\xe9 \u20ac</p>"
    declared = f'<html><head><meta charset="windows-1252"></head><body>{"<p>caf\xe9</p>"}</body></html>'
    result = read_html(declared.encode("cp1252"))
    assert texts(result) == ["caf\xe9"] and result.document.flags == []
    bom = read_html(b"\xef\xbb\xbf" + f"<html><body>{cafe}</body></html>".encode())
    assert texts(bom) == ["caf\xe9 \u20ac"] and bom.document.flags == []
    wide = read_html(f"<html><body>{cafe}</body></html>".encode("utf-16"))
    assert texts(wide) == ["caf\xe9 \u20ac"] and wide.document.flags == []
    plain = read_html(f"<html><body>{cafe}</body></html>".encode())
    assert texts(plain) == ["caf\xe9 \u20ac"] and plain.document.flags == []
    xhtml = read_html(f'<?xml version="1.0" encoding="utf-8"?><html><body>{cafe}</body></html>'.encode())
    assert texts(xhtml) == ["caf\xe9 \u20ac"]
    old = read_html("<html><body><p>caf\xe9 \u20ac</p></body></html>".encode("cp1252"))
    assert texts(old) == ["caf\xe9 \u20ac"]
    assert rules(old.document.flags) == ["encoding.guessed"]


def test_f_markdown_decodes_like_csv_and_flags_a_guess():
    assert texts(read_md(b"\xef\xbb\xbfcaf\xc3\xa9 \xe2\x82\xac")) == ["caf\xe9 \u20ac"]
    assert texts(read_md("caf\xe9 \u20ac".encode("utf-16"))) == ["caf\xe9 \u20ac"]
    plain = read_md("caf\xe9".encode())
    assert plain.document.flags == []
    guessed = read_md("caf\xe9 \u20ac".encode("cp1252"))
    assert texts(guessed) == ["caf\xe9 \u20ac"] and rules(guessed.document.flags) == ["encoding.guessed"]
    from ragnaroq.readers import md_reader

    with pytest.raises(ReaderRefused) as refused:
        md_reader.read(b"\xff\xfe\x00", "t.md", Config())  # a UTF-16 byte-order mark, half a character
    assert refused.value.rule == "md.invalid"


def test_f_the_decoder_is_shared_with_the_csv_reader():
    from ragnaroq.readers import _build, csv_reader

    assert not hasattr(csv_reader, "_decode")
    assert _build.decode("caf\xe9".encode("cp1252")) == ("caf\xe9", True)
    assert _build.decode(b"\xef\xbb\xbfa") == ("a", False)


# --- R18 (g): depth ------------------------------------------------------------------------------


def test_g_ten_thousand_nested_divs_neither_crash_nor_lose_text_without_a_flag():
    n = 10_000
    source = f"<html><body><p>before</p>{'<div>' * n}bottom{'</div>' * n}<p>after</p></body></html>"
    result = read_html(source)
    assert "html.too-deep" in page_flags(result)
    assert texts(result)[0] == "before"
    assert "bottom" not in texts(result)
    # With our own limit out of the way, libxml2's is what drops the rest, and it is flagged too.
    result = read_html(source, config=Config(max_depth=100_000))
    assert "html.too-deep" in page_flags(result)
    assert texts(result)[0] == "before"


def test_g_elements_deeper_than_max_depth_are_not_read_and_flag_the_page():
    source = "<html><body><div><div><div>d3<div>d4</div></div></div></div><p>after</p></body></html>"
    # html is depth 1, body 2, the first div 3: d3 sits at depth 5 and d4 at 6.
    deep = read_html(source, config=Config(max_depth=5))
    assert texts(deep) == ["d3", "after"] and page_flags(deep) == ["html.too-deep"]
    ok = read_html(source)
    assert texts(ok) == ["d3", "d4", "after"] and page_flags(ok) == []
    inline = read_html(
        "<html><body><p>a<span>b<span>c<span>d<b>e</b></span></span></span> tail</p></body></html>",
        config=Config(max_depth=5),
    )
    assert texts(inline) == ["abc tail"] and page_flags(inline) == ["html.too-deep"]


def test_g_markdown_nested_beyond_the_parsers_limit_is_flagged_never_silently_lost():
    forty = read_md(">" * 40 + " deep text\n\nafter\n")
    assert texts(forty) == ["deep text", "after"] and page_flags(forty) == []
    deep = read_md(">" * 150 + " deep text\n\nafter\n")
    assert page_flags(deep) == ["md.too-deep"]
    assert "the rest of the file from that point is not read" in deep.document.pages[0].flags[0].message
    assert "after" in texts(deep)
    shallow = read_md(">" * 5 + " shallow text\n")
    assert texts(shallow) == ["shallow text"] and page_flags(shallow) == []
    lists = read_md("".join("  " * i + "- item\n" for i in range(40)))
    assert texts(lists) == ["item"] * 40 and page_flags(lists) == []


# --- R18 (h): the line cap -----------------------------------------------------------------------


def test_h_html_stops_at_max_cells_and_says_so():
    config = Config(max_cells=3)
    result = body("<p>1</p><p>2</p><p>3</p><p>4</p><p>5</p>", config=config)
    assert texts(result) == ["1", "2", "3"] and page_flags(result) == ["html.line-limit"]
    exact = body("<p>1</p><p>2</p><p>3</p>", config=config)
    assert texts(exact) == ["1", "2", "3"] and page_flags(exact) == []
    table = body("<table><tr><td>1<td>2<td>3<td>4</table>", config=config)
    assert texts(table) == ["1", "2", "3"] and page_flags(table) == ["html.line-limit"]
    assert [c.text for c in table.document.pages[0].tables[0].cells] == ["1", "2", "3"]


def test_h_markdown_stops_at_max_cells_and_says_so():
    config = Config(max_cells=3)
    result = read_md("1\n\n2\n\n3\n\n4\n\n5\n", config=config)
    assert texts(result) == ["1", "2", "3"] and page_flags(result) == ["md.line-limit"]
    exact = read_md("1\n\n2\n\n3\n", config=config)
    assert page_flags(exact) == []
    table = read_md("| a | b |\n| - | - |\n| c | d |\n", config=config)
    assert texts(table) == ["a", "b", "c"] and page_flags(table) == ["md.line-limit"]


# --- R18 (i): images -----------------------------------------------------------------------------


def test_i_a_missing_file_no_src_and_no_base_are_not_found(tmp_path):
    result = body('<img src="nope.png"><img><img src=""><img src="  ">', base=tmp_path)
    assert [own(f) for f in figures(result)] == [["image.not-found"]] * 4
    (tmp_path / "logo.png").write_bytes(png())
    (tmp_path / "dir.png").mkdir()
    result = body('<img src="logo.png"><img src="dir.png"><img src="logo.png?x=1">')
    assert [own(f) for f in figures(result)] == [["image.not-found"]] * 3
    assert result.images == {}
    result = body('<img src="dir.png">', base=tmp_path)
    assert own(figures(result)[0]) == ["image.not-found"]


def test_i_a_local_image_over_the_limit_is_not_read(tmp_path, monkeypatch):
    (tmp_path / "big.png").write_bytes(png() * 10)
    config = Config(max_image_bytes=len(png()) * 10 - 1)
    result = body('<img src="big.png">', base=tmp_path, config=config)
    assert own(figures(result)[0]) == ["image.too-large"] and result.images == {}
    fine = body('<img src="big.png">', base=tmp_path, config=Config(max_image_bytes=len(png()) * 10))
    assert fine.images == {"p1-f1": png() * 10}


def test_i_the_same_file_named_again_is_read_once_and_shared(tmp_path):
    (tmp_path / "logo.png").write_bytes(png())
    result = body('<img src="logo.png">' * 50, base=tmp_path)
    assert len(result.images) == 50
    assert len({id(data) for data in result.images.values()}) == 1


def test_i_the_description_is_the_caption_then_alt_then_none():
    result = body(
        '<figure><img src=a.png alt="Alt"><figcaption>The <b>caption</b></figcaption></figure>'
        '<figure><img src=b.png alt="Only alt"></figure>'
        '<figure><img src=c.png alt="  "><figcaption></figcaption></figure>'
        '<img src=d.png alt="Loose alt"><img src=e.png alt="">'
        '<figure><figure><img src=f.png alt="Inner"></figure><figcaption>Outer</figcaption></figure>'
    )
    assert [(f.description, f.description_source) for f in figures(result)] == [
        ("The caption", "caption"),
        ("Only alt", "alt"),
        ("", "none"),
        ("Loose alt", "alt"),
        ("", "none"),
        ("Inner", "alt"),
    ]


def test_i_alt_text_is_never_part_of_a_line_and_srcset_is_ignored():
    result = body(
        '<p>Before <img src=a.png alt="Sneaky alt"> after</p><p><img src=b.png alt="Only"></p>'
        '<img src="https://example.org/a.png" srcset="https://example.org/b.png 2x, c.png 3x">'
    )
    assert lines(result) == [("/html/body/p[1]", "Before after")]
    assert [f.path for f in figures(result)] == ["", "", "https://example.org/a.png"]
    assert [own(f) for f in figures(result)] == [["image.not-found"]] * 2 + [["image.remote-not-fetched"]]


def test_i_figures_follow_the_word_readers_construction():
    result = body('<h1>A</h1><img src=a.png alt="x"><p>B</p><p>C <img src=b.png> D</p>')
    first, second = figures(result)
    assert (first.id, first.page, first.order) == ("p1-f1", 1, 1)
    assert (second.id, second.order) == ("p1-f2", 3)
    assert texts(result) == ["A", "B", "C D"]


def test_i_a_hidden_image_is_flagged():
    result = body('<div hidden><img src="https://example.org/x.png"></div>')
    assert own(figures(result)[0]) == ["image.remote-not-fetched", "html.hidden"]


def test_i_markdown_images_alt_and_lines():
    result = read_md(
        'See ![the *chart*](a.png "t") here\n\n![Only](b.png)\n\n![](c.png)\n\n'
        "| h |\n| - |\n| ![in cell](d.png) |\n"
    )
    assert [(line.anchor.lines, line.text) for line in result.document.lines()] == [
        ((1, 1), "See here"),
        ((7, 7), "h"),
    ]
    found = figures(result)
    assert [(f.anchor.lines, f.description, f.description_source) for f in found] == [
        ((1, 1), "the chart", "alt"),
        ((3, 3), "Only", "alt"),
        ((5, 5), "", "none"),
        ((9, 9), "in cell", "alt"),
    ]
    assert [(f.id, f.order) for f in found] == [("p1-f1", 1), ("p1-f2", 1), ("p1-f3", 1), ("p1-f4", 2)]


def test_i_an_img_in_markdown_raw_html_becomes_a_figure(tmp_path):
    (tmp_path / "logo.png").write_bytes(png())
    result = read_md(
        'Inline <img src="logo.png" alt="Raw"> picture and <img src="https://example.org/x.png"> too.\n\n'
        '<div>\n<img src="../out.png" alt="Out">\n</div>\n',
        base=tmp_path,
    )
    assert texts(result) == ["Inline picture and too."]
    one, two, three = figures(result)
    assert (one.anchor, one.description, own(one)) == (MdAnchor(lines=(1, 1)), "Raw", [])
    assert own(two) == ["image.remote-not-fetched"]
    assert (three.anchor, own(three)) == (MdAnchor(lines=(3, 5)), ["image.outside-folder"])
    assert result.images == {"p1-f1": png()}


# --- R18 (j), (k), (m) ---------------------------------------------------------------------------


def test_j_a_figures_own_flags_survive_decide_and_deciding_twice_changes_nothing():
    result = body(
        '<img src="https://example.org/x.png"><div hidden><img src="https://example.org/y.png" alt="Y"></div>'
        '<img src="../up.png" alt="Up">'
    )
    document = result.document
    before = [(f.id, f.status, [(x.rule, x.message) for x in f.flags]) for f in figures(result)]
    assert [rules(f.flags) for f in figures(result)] == [
        ["image.remote-not-fetched", "image.no-description"],
        ["image.remote-not-fetched", "html.hidden"],
        ["image.not-found"],
    ]
    policy.decide(document, Config().policy)
    policy.decide(document, Config().policy)
    after = [(f.id, f.status, [(x.rule, x.message) for x in f.flags]) for f in figures(result)]
    assert after == before
    assert [f.status for f in figures(result)] == ["flagged", "flagged", "flagged"]


def test_k_max_image_bytes_is_a_limit_that_does_not_change_what_is_read():
    assert Config().max_image_bytes == 20 * 1024**2
    assert Config(max_image_bytes=1).reading_sha256() == Config().reading_sha256()
    assert Config(max_image_bytes=1).sha256() != Config().sha256()


def test_m_evaluate_passes_the_folder_to_the_readers(tmp_path, monkeypatch):
    (tmp_path / "notice.html").write_bytes((DEMO / "notice.html").read_bytes())
    (tmp_path / "notice.html.expected.jsonl").write_bytes((DEMO / "notice.html.expected.jsonl").read_bytes())
    (tmp_path / "logo.png").write_bytes(png())
    seen = []
    real = evaluate.run_exact

    def spy(folder, read):
        def reading(data, name, config):
            result = read(data, name, config)
            seen.append(result)
            return result

        return real(folder, reading)

    monkeypatch.setattr(evaluate, "run_exact", spy)
    report = evaluate.run(tmp_path)
    assert report.exact.files[0].score.ok
    (result,) = seen
    assert result.images == {"p1-f1": png()}


# --- R18 (l): Markdown ---------------------------------------------------------------------------

MARKDOWN = """# Title

Para one
continues here.

- item a
  - nested b
- item c

> quote para
>
> - quoted item

| A | `code` | [link](http://x.y) |
| --- | --- | --- |
| 1 | 2 &amp; 3 | **4** |

```py
x = 1
```

---

    indented code

Text with &amp; entity and a<br>b and <!-- gone -->end.
"""


def test_l_markdown_blocks_inline_text_and_tables():
    result = read_md(MARKDOWN)
    found = [(line.anchor.lines, line.text, line.role, line.table_id) for line in result.document.lines()]
    assert found == [
        ((1, 1), "Title", "heading", None),
        ((3, 4), "Para one continues here.", "body", None),
        ((6, 6), "item a", "body", None),
        ((7, 7), "nested b", "body", None),
        ((8, 8), "item c", "body", None),
        ((10, 10), "quote para", "body", None),
        ((12, 12), "quoted item", "body", None),
        ((14, 14), "A", "table", "p1-t1"),
        ((14, 14), "code", "table", "p1-t1"),
        ((14, 14), "link", "table", "p1-t1"),
        ((16, 16), "1", "table", "p1-t1"),
        ((16, 16), "2 & 3", "table", "p1-t1"),
        ((16, 16), "4", "table", "p1-t1"),
        ((18, 20), "x = 1", "body", None),
        ((24, 24), "indented code", "body", None),
        ((26, 26), "Text with & entity and a b and end.", "body", None),
    ]
    (table,) = result.document.pages[0].tables
    assert (table.id, table.rows, table.columns) == ("p1-t1", 2, 3)
    assert [(c.row, c.column, c.text, c.anchor) for c in table.cells] == [
        (0, 0, "A", MdAnchor(lines=(14, 14))),
        (0, 1, "code", MdAnchor(lines=(14, 14))),
        (0, 2, "link", MdAnchor(lines=(14, 14))),
        (1, 0, "1", MdAnchor(lines=(16, 16))),
        (1, 1, "2 & 3", MdAnchor(lines=(16, 16))),
        (1, 2, "4", MdAnchor(lines=(16, 16))),
    ]
    assert result.document.pages[0].name == "t.md"
    assert result.document.title == "Title"


def test_l_a_blockquote_holding_a_list_and_a_nested_ordered_list():
    result = read_md("> intro\n> 1. one\n>    - deep\n> 2. two\n>\n> after\n")
    assert [(line.anchor.lines, line.text) for line in result.document.lines()] == [
        ((1, 1), "intro"),
        ((2, 2), "one"),
        ((3, 3), "deep"),
        ((4, 4), "two"),
        ((6, 6), "after"),
    ]


def test_l_a_table_cell_with_code_links_images_and_a_pipe():
    result = read_md("| a | b |\n|---|---|\n| `x \\| y` | [t](u) and [t2](v) |\n| only |\n")
    assert [(line.anchor.lines, line.text) for line in result.document.lines()] == [
        ((1, 1), "a"),
        ((1, 1), "b"),
        ((3, 3), "x | y"),
        ((3, 3), "t and t2"),
        ((4, 4), "only"),
    ]
    (table,) = result.document.pages[0].tables
    assert (table.rows, table.columns) == (3, 2)


def test_l_raw_html_blocks_are_read_as_their_visible_text_and_flagged():
    result = read_md(
        "<div>\n<p>Visible <b>text</b></p>\n<script>alert(1)</script>\n<style>p{}</style>\n"
        "<!-- note -->\n<p>Second</p>\n</div>\n\nPlain.\n\n<script>\nsteal()\n</script>\n\n<!-- only -->\n"
    )
    found = [(line.anchor.lines, line.text, rules(line.flags)) for line in result.document.lines()]
    assert found == [((1, 7), "Visible text Second", ["md.raw-html"]), ((9, 9), "Plain.", [])]


def test_l_raw_html_hidden_text_is_flagged_even_when_it_spans_blocks():
    result = read_md(
        "<div hidden>ignore this</div>\n\nvisible\n\n"
        '<div style="display:none">\n\nmiddle\n\n</div>\n\nend\n\n'
        'Text <span style="display:none">secret</span> more <script>bad()</script> done\n'
    )
    found = {line.text: rules(line.flags) for line in result.document.lines()}
    assert found == {
        "ignore this": ["md.raw-html", "html.hidden"],
        "visible": [],
        "middle": ["html.hidden"],
        "end": [],
        "Text secret more done": ["md.raw-html", "html.hidden"],
    }


def test_l_yaml_front_matter_is_lines_and_never_a_rule_or_heading():
    result = read_md("---\ntitle: Hello\n\ntags: [a, b]\n---\n# Heading\n")
    assert [(line.anchor.lines, line.text, line.role) for line in result.document.lines()] == [
        ((2, 2), "title: Hello", "body"),
        ((4, 4), "tags: [a, b]", "body"),
        ((6, 6), "Heading", "heading"),
    ]
    dots = read_md("---\nkey: v\n...\ntext\n")
    assert [(line.anchor.lines, line.text) for line in dots.document.lines()] == [
        ((2, 2), "key: v"),
        ((4, 4), "text"),
    ]
    open_ended = read_md("---\n\nText\n")
    assert [(line.anchor.lines, line.text) for line in open_ended.document.lines()] == [((3, 3), "Text")]


def test_l_nothing_is_lost_with_a_linked_reference_an_autolink_or_an_escape():
    result = read_md("Go to <https://example.org> or [ref] \\*not bold\\*.\n\n[ref]: https://example.org/r\n")
    assert texts(result) == ["Go to https://example.org or ref *not bold*."]


# --- R18 (n): errors -----------------------------------------------------------------------------


def test_n_an_empty_file_is_an_empty_document_and_a_parse_error_is_a_refusal(monkeypatch):
    from ragnaroq.readers import html_reader, md_reader

    for module, name in ((html_reader, "e.html"), (md_reader, "e.md")):
        for data in (b"", b"  \n\t\n", b"\xef\xbb\xbf"):
            result = module.read(data, name, Config())
            assert result.document.pages[0].lines == [] and result.document.flags == []
    comment_only = html_reader.read(b"<!-- nothing -->", "c.html", Config())
    assert comment_only.document.pages[0].lines == []

    from lxml import etree

    def broken(*args, **kwargs):
        raise etree.XMLSyntaxError("bad", 1, 1, 1)

    monkeypatch.setattr(lxml.html, "document_fromstring", broken)
    with pytest.raises(ReaderRefused) as refused:
        read_html("<p>x</p>")
    assert refused.value.rule == "html.invalid"

    def bug(*args, **kwargs):
        raise KeyError("a bug in our code")

    monkeypatch.setattr(lxml.html, "document_fromstring", bug)
    with pytest.raises(KeyError):
        read_html("<p>x</p>")


# --- R19: equations ------------------------------------------------------------------------------


def test_r19_mathml_is_read_in_place_and_flagged():
    markup = (
        "<p>Let <math><mi>x</mi><mo>=</mo><msup><mi>y</mi><mn>2</mn></msup></math> hold.</p>"
        "<p>No maths here.</p>"
        "<p><math display='block'><semantics><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow>"
        '<annotation encoding="application/x-tex">a + b</annotation></semantics></math></p>'
        "<p>Empty <math></math> math</p>"
    )
    result = body(markup)
    assert lines(result) == [
        ("/html/body/p[1]", "Let x = y 2 hold."),
        ("/html/body/p[2]", "No maths here."),
        ("/html/body/p[3]", "a + b"),
        ("/html/body/p[4]", "Empty math"),
    ]
    flagged = [[(f.rule, f.severity, f.message) for f in line.flags] for line in result.document.lines()]
    assert flagged == [
        [("html.equation", "review", EQUATION)],
        [],
        [("html.equation", "review", EQUATION)],
        [],
    ]


# --- what the probes found -----------------------------------------------------------------------


def test_text_after_the_closing_html_tag_is_still_read():
    result = read_html("<html><body><p>a</p></body></html><p>after</p><!-- c --><div>more</div>")
    assert texts(result) == ["a", "after", "more"]


def test_a_declared_encoding_the_bytes_do_not_fit_is_flagged():
    wrong = read_html(
        b'<html><head><meta charset="utf-8"></head><body><p>caf\xe9</p><p>next</p></body></html>'
    )
    assert texts(wrong) == ["caf\ufffd", "next"] and rules(wrong.document.flags) == ["encoding.mismatch"]
    assert "replacement" in wrong.document.flags[0].message
    unknown = read_html(b'<html><head><meta charset="x-nope"></head><body><p>caf\xc3\xa9</p></body></html>')
    assert texts(unknown) == ["caf\xe9"] and rules(unknown.document.flags) == ["encoding.mismatch"]
    assert "UTF-8" in unknown.document.flags[0].message


def test_a_setext_heading_is_anchored_to_both_of_its_lines():
    result = read_md("Title\n=====\n\nSub\n---\n\ntext\n")
    assert [(line.anchor.lines, line.text, line.role) for line in result.document.lines()] == [
        ((1, 2), "Title", "heading"),
        ((4, 5), "Sub", "heading"),
        ((7, 7), "text", "body"),
    ]


def test_markdown_inline_html_drops_scripts_and_keeps_the_words_around_it():
    result = read_md("a <script>bad()</script> b <style>x</style> c <b>d</b> <!-- x --> e<br>f\n")
    assert texts(result) == ["a b c d e f"]
    assert lines_flags(result) == [["md.raw-html"]]


def lines_flags(result):
    return [rules(line.flags) for line in result.document.lines()]


# --- fix round 1 (R22) ---------------------------------------------------------------------------


def timed(function, *args, **kwargs):
    start = time.perf_counter()
    result = function(*args, **kwargs)
    return result, time.perf_counter() - start


def test_c1_markdown_prose_naming_head_script_template_noscript_drops_nothing():
    head = read_md("The <head> element holds metadata.\n\nNext paragraph.\n\n# Heading\n")
    assert [(line.anchor.lines, line.text) for line in head.document.lines()] == [
        ((1, 1), "The element holds metadata."),
        ((3, 3), "Next paragraph."),
        ((5, 5), "Heading"),
    ]
    assert lines_flags(head) == [[], [], []]
    script = read_md("Put code in <script> tags.\n\nNext paragraph.\n\n![a](a.png)\n")
    assert [(line.anchor.lines, line.text) for line in script.document.lines()] == [
        ((1, 1), "Put code in tags."),
        ((3, 3), "Next paragraph."),
    ]
    assert lines_flags(script)[0] == ["md.raw-html"] and [f.anchor.lines for f in figures(script)] == [(5, 5)]
    for tag in ("template", "noscript", "style"):
        result = read_md(f"A <{tag}> tag.\n\nNext.\n")
        assert texts(result) == ["A tag.", "Next."], tag
        assert lines_flags(result) == [["md.raw-html"], []]
    closed = read_md("a <template>gone</template> b\n\nnext\n")
    assert texts(closed) == ["a b", "next"] and lines_flags(closed) == [["md.raw-html"], []]
    block = read_md("<head>\n\nstill read\n")
    assert texts(block)[-1] == "still read"


def test_i1_one_file_named_many_ways_is_held_once_and_the_total_is_capped(tmp_path):
    (tmp_path / "big.png").write_bytes(png() * 3)
    result = body("".join(f'<img src="big.png?{i}">' for i in range(40)), base=tmp_path)
    assert len(result.images) == 40 and len({id(data) for data in result.images.values()}) == 1
    md = read_md("\n\n".join(f"![a](big.png?{i})" for i in range(40)), base=tmp_path)
    assert len({id(data) for data in md.images.values()}) == 1
    blocks = read_md("\n\n".join('<div><img src="big.png"></div>' for _ in range(40)), base=tmp_path)
    assert len(blocks.images) == 40 and len({id(data) for data in blocks.images.values()}) == 1
    for name in "abc":
        (tmp_path / f"{name}.png").write_bytes(b"x" * 100)
    small = Config(max_unzipped_bytes=250)
    capped = body(
        '<img src="a.png"><img src="b.png"><img src="c.png"><img src="a.png">', base=tmp_path, config=small
    )
    assert [own(f) for f in figures(capped)] == [[], [], ["image.too-large"], []]
    assert sorted(capped.images) == ["p1-f1", "p1-f2", "p1-f4"]
    assert len({id(capped.images[k]) for k in ("p1-f1", "p1-f4")}) == 1


def test_i1_figures_count_toward_the_cap():
    result = body("<p>a</p>" + "<img src=x.png>" * 5, config=Config(max_cells=3))
    assert texts(result) == ["a"] and len(figures(result)) == 2
    assert page_flags(result) == ["html.line-limit"]
    md = read_md("a\n\n" + "\n\n".join(["![x](x.png)"] * 5), config=Config(max_cells=3))
    assert texts(md) == ["a"] and len(figures(md)) == 2 and page_flags(md) == ["md.line-limit"]


def test_i2_quadratic_inputs_finish_in_seconds():
    cases = {
        "script block": ("<pre>\n" + "<script" * 10000 + "\n", "a.md"),
        "bare tags": ("<pre>\n" + "<a" * 20000 + "\n", "b.md"),
        "quotes": ("<pre>\n" + "<a '" * 20000 + "\n", "c.md"),
        "open inline": ("<b>x " * 20000, "d.md"),
        "close misses": ("<b>x " * 10000 + "</i>y " * 10000, "e.md"),
    }
    for label, (source, _name) in cases.items():
        _, seconds = timed(read_md, source)
        assert seconds < 10, (label, seconds)
    result, seconds = timed(read_md, "<b>x " * 20000)
    assert "md.too-deep" in page_flags(result)
    huge = "a" * 200_000
    result, seconds = timed(read_html, f'<img src="{huge}"><img src="../{huge}">')
    assert seconds < 10
    assert [own(f) for f in figures(result)] == [["image.unreadable"]] * 2


def test_i2_table_spans_are_linear():
    row = "<tr>" + '<td colspan="1000" rowspan="1000"></td>' * 800 + "</tr>"
    _, seconds = timed(read_html, "<table>" + row * 40 + "</table><p>after</p>")
    assert seconds < 5
    one = "<table><tr>" + '<td colspan="1000" rowspan="65534"></td>' * 33000 + "</tr></table><p>x</p>"
    result, seconds = timed(read_html, one)
    assert seconds < 5 and texts(result) == ["x"]
    spans = body(
        "<table><tr><td colspan=99999 rowspan=99999>a</td><td>b</td></tr><tr><td>c</td></tr></table>"
    )
    (table,) = spans.document.pages[0].tables
    assert [(c.row, c.column, c.text) for c in table.cells] == [(0, 0, "a"), (0, 1000, "b"), (1, 1000, "c")]


def test_i3_each_run_of_text_is_a_line_in_document_order():
    result = body("<div>before<p>mid</p>after</div>")
    assert lines(result) == [
        ("/html/body/div", "before"),
        ("/html/body/div/p", "mid"),
        ("/html/body/div", "after"),
    ]
    stray = body("one<p>two</p>three<p>four</p>five")
    assert [text for _, text in lines(stray)] == ["one", "two", "three", "four", "five"]
    cell = body("<table><tr><td>lead<p>para one</p>tail</td></tr></table>")
    assert [text for _, text in lines(cell)] == ["lead", "para one", "tail"]
    (table,) = cell.document.pages[0].tables
    assert [c.text for c in table.cells] == ["lead\npara one\ntail"]
    picture = body("<div>a<img src=x.png>b<p>c</p>d</div>")
    assert [text for _, text in lines(picture)] == ["ab", "c", "d"]
    assert [f.order for f in figures(picture)] == [1]


def test_i4_declared_charsets_are_found_in_meta_tags_only_and_honoured():
    cafe = "<p>caf\xe9 \u20ac</p>"
    utf16 = read_html(f"<meta charset='utf-16'><body>{cafe}".encode())
    assert texts(utf16) == ["caf\xe9 \u20ac"] and utf16.document.flags == []
    utf32 = read_html(
        f'<meta http-equiv="Content-Type" content="text/html; charset=utf-32"><body>{cafe}'.encode()
    )
    assert texts(utf32) == ["caf\xe9 \u20ac"] and utf32.document.flags == []
    mention = read_html(f"<meta name=d content='how to set the charset'><body>{cafe}".encode())
    assert texts(mention) == ["caf\xe9 \u20ac"] and mention.document.flags == []
    commented = read_html(f"<!-- <meta charset='iso-8859-2'> --><body>{cafe}".encode())
    assert texts(commented) == ["caf\xe9 \u20ac"] and commented.document.flags == []
    late = read_html(f"<!--{'x' * 9000}--><meta charset='windows-1252'><body>{cafe}".encode())
    assert texts(late) == ["caf\xe9 \u20ac"]
    sjis = read_html("<meta charset='shift_jis'><body><p>\u65e5\u672c\u8a9e</p>".encode("shift_jis"))
    assert texts(sjis) == ["\u65e5\u672c\u8a9e"] and sjis.document.flags == []
    http = (
        '<meta http-equiv="Content-Type" content="text/html; charset=shift_jis"><body><p>\u65e5\u672c</p>'
    ).encode("shift_jis")
    http = read_html(http)
    assert texts(http) == ["\u65e5\u672c"]
    latin = read_html("<meta charset='iso-8859-1'><body><p>\u201cq\u201d</p>".encode("cp1252"))
    assert texts(latin) == ["\u201cq\u201d"]  # browsers read Latin-1 as Windows-1252
    bom = read_html(b"\xef\xbb\xbf" + f"<meta charset='windows-1252'><body>{cafe}".encode())
    assert texts(bom) == ["caf\xe9 \u20ac"]
    bad = read_html(b"<meta charset=utf-8><p>ok</p><p>bad \xff\xfe byte</p><p>after</p>")
    assert texts(bad) == ["ok", "bad \ufffd\ufffd byte", "after"]
    assert rules(bad.document.flags) == ["encoding.mismatch"]
    assert "utf-8" in bad.document.flags[0].message and "replacement" in bad.document.flags[0].message


def test_i5_the_parsers_size_limits_do_not_drop_the_rest_of_the_page():
    big = base64.b64encode(b"\x89PNG" + b"x" * 8_000_000).decode()
    result = read_html(f'<p>before</p><img src="data:image/png;base64,{big}"><p>after</p>')
    assert texts(result) == ["before", "after"] and len(result.images["p1-f1"]) == 8_000_004
    text = read_html(b"<html><body><p>before</p><p>" + b"x" * 11_000_000 + b"</p><p>after</p></body></html>")
    assert [len(t) for t in texts(text)] == [6, 11_000_000, 5] and page_flags(text) == []
    nested = f"<p>top</p>{'<div>' * 300}bottom{'</div>' * 300}<p>after</p>"
    cut = read_html(nested)
    assert texts(cut) == ["top", "after"] and page_flags(cut) == ["html.too-deep"]
    full = read_html(nested, config=Config(max_depth=1000))
    assert texts(full) == ["top", "bottom", "after"] and page_flags(full) == []


def test_m1_an_iframe_srcdoc_is_flagged_not_parsed():
    result = body('<p>a</p><iframe srcdoc="<p>inside</p>">fallback</iframe>')
    assert texts(result) == ["a", "fallback"] and page_flags(result) == ["html.iframe-not-read"]


def test_m2_ruby_annotations_are_not_line_text():
    result = body("<p><ruby>\u6f22<rt>kan</rt><rp>(</rp>\u5b57<rt>ji</rt></ruby> tail</p>")
    assert texts(result) == ["\u6f22\u5b57 tail"]


def test_m3_only_the_last_closing_tags_are_removed_and_only_when_text_follows():
    alt = (
        '<html><body><img src="a.png" alt="see </body> tag"><textarea></html></textarea>'
        "<p>x</p></body></html>"
    )
    result = read_html(alt)
    assert [f.description for f in figures(result)] == ["see </body> tag"]
    assert texts(result) == ["</html>", "x"]
    plain = read_html("<html><body><p>a</p></body></html>\n")
    assert texts(plain) == ["a"]
    trailing = read_html("<html><body><p>a</p></body></html>\n<p>b</p>")
    assert texts(trailing) == ["a", "b"]


def test_m4_markdown_empty_cells_are_skipped_like_word_and_html():
    result = read_md("| a | b |\n|---|---|\n| x | |\n")
    (table,) = result.document.pages[0].tables
    assert [(c.row, c.column, c.text) for c in table.cells] == [(0, 0, "a"), (0, 1, "b"), (1, 0, "x")]
    assert (table.rows, table.columns) == (2, 2)


def test_m6_blank_markdown_and_html_files_are_empty_documents_through_read_bytes():
    for name in ("e.md", "e.markdown", "e.html", "e.htm"):
        for data in (b"", b"  \n\t", b"\xef\xbb\xbf", b"\xef\xbb\xbf \n"):
            result = read_bytes(data, name, Config())
            assert result.document.pages[0].lines == [] and result.document.flags == [], (name, data)
    with pytest.raises(ReaderRefused) as refused:
        read_bytes(b"\x00\x01\x02", "binary.md", Config())
    assert refused.value.rule == "format.unknown"


def test_m8_a_caption_must_be_a_direct_child_of_the_figure():
    result = body(
        "<figure><div><figcaption>Nested</figcaption></div><img src=a.png alt='Alt'></figure>"
        "<figure><img src=b.png alt='Alt2'><figcaption>Direct</figcaption></figure>"
    )
    assert [(f.description, f.description_source) for f in figures(result)] == [
        ("Alt", "alt"),
        ("Direct", "caption"),
    ]


def test_m9_control_characters_around_a_src_are_stripped_before_the_scheme_check():
    result = body('<img src="\x01http://example.org/x.png\x02"><img src="\x1f data:image/png,%89PNG ">')
    first, second = figures(result)
    assert own(first) == ["image.remote-not-fetched"] and first.path == "http://example.org/x.png"
    assert own(second) == [] and result.images["p1-f2"] == b"\x89PNG"
