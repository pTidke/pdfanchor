"""Make references/demo: small, deliberately awkward files, each with a hand-written answer key.

Run with:  python references/make_demo_folder.py

Every file holds fixed, made-up content. Each `<file>.expected.jsonl` is written here by hand,
from what the file holds as a careful person would read it, never from a reader's output.
The anchor conventions are those of `ragnaroq.anchors`: Word numbers are 1-based and count every
body paragraph, even empty ones; PowerPoint shapes are named and paragraphs are 0-based; a merged
Excel cell anchors to its range; CSV rows are 1-based and count the header; JSON uses RFC 6901
pointers; XML paths index only same-named siblings; HTML paths are lxml's `getpath`; Markdown
lines are 1-based and inclusive. A PowerPoint table cell is a paragraph of its table's shape,
numbered 0-based across the rows; a Markdown table cell is anchored to the lines of its row.
"""

import copy
import datetime as dt
import io
import json
import re
import zipfile
from pathlib import Path

from docx import Document as Docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches as PInches

DEMO = Path(__file__).parent / "demo"
FIXED = dt.datetime(2026, 11, 26, 9, 0, 0)
ZIP_DATE = (2026, 11, 26, 9, 0, 0)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def write(name: str, data: bytes | str) -> None:
    path = DEMO / name
    if isinstance(data, str):
        data = data.encode("utf8")
    path.write_bytes(data)


def keyfile(name: str, items: list[dict]) -> None:
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    (DEMO / f"{name}.expected.jsonl").write_text(text, encoding="utf8")


def rezip(path: Path, change=None) -> None:
    """Rewrite a zip with a fixed date on every entry; `change(name, data)` may edit a part."""
    with zipfile.ZipFile(path) as source:
        parts = [(info.filename, source.read(info.filename)) for info in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in parts:
            if change:
                data = change(name, data)
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, data)


def png(path: Path, draw) -> bytes:
    image = Image.new("RGB", (240, 120), "white")
    draw(ImageDraw.Draw(image))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def chart(d: ImageDraw.ImageDraw) -> None:
    for n, height in enumerate((30, 55, 80, 95)):
        x = 30 + n * 50
        d.rectangle((x, 110 - height, x + 30, 110), fill=(40, 90, 160))


def logo(d: ImageDraw.ImageDraw) -> None:
    d.ellipse((60, 10, 180, 110), outline="black", width=6)


def diagram(d: ImageDraw.ImageDraw) -> None:
    d.rectangle((20, 40, 90, 80), outline="black", width=3)
    d.rectangle((150, 40, 220, 80), outline="black", width=3)
    d.line((90, 60, 150, 60), fill="black", width=3)


def rupees(d: ImageDraw.ImageDraw) -> None:
    d.text((40, 40), "₹12,000", fill="black", font=ImageFont.truetype(FONT, 32))


# --- guide.docx ----------------------------------------------------------------------------------

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx() -> None:
    doc = Docx()
    props = doc.core_properties
    props.created = props.modified = FIXED
    props.author = props.last_modified_by = "Demo"
    doc.add_heading("Tax Guide 2026", 1)  # 1
    doc.add_heading("Rates", 2)  # 2
    paragraph = doc.add_paragraph("Interest is charged at 12% a year.")  # 3, with footnote 1
    run = OxmlElement("w:r")
    ref = OxmlElement("w:footnoteReference")
    ref.set(qn("w:id"), "1")
    run.append(ref)
    paragraph._p.append(run)
    tracked = doc.add_paragraph("The due date is ")  # 4: tracked changes, read as accepted
    inserted = OxmlElement("w:ins")
    for attribute, value in (("id", "10"), ("author", "Demo"), ("date", "2026-11-26T09:00:00Z")):
        inserted.set(qn(f"w:{attribute}"), value)
    inserted_run = OxmlElement("w:r")
    inserted_text = OxmlElement("w:t")
    inserted_text.text = "31 July"
    inserted_run.append(inserted_text)
    inserted.append(inserted_run)
    deleted = copy.deepcopy(inserted)
    deleted.tag = qn("w:del")
    deleted.set(qn("w:id"), "11")
    for text in deleted.iter(qn("w:t")):
        text.tag = qn("w:delText")
        text.text = "30 June"
    tracked._p.append(inserted)
    tracked._p.append(deleted)
    tracked.add_run(" each year.")
    table = doc.add_table(rows=3, cols=2)  # table 1
    for r, row in enumerate((("Slab", "Rate"), ("Up to 3 lakh", "Nil"), ("3 to 7 lakh", "5%"))):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    doc.add_paragraph("")  # 5: empty, and still counted
    picture = doc.add_paragraph()  # 6
    shape = picture.add_run().add_picture(io.BytesIO(png(DEMO / "chart.png", chart)), width=Inches(2))
    shape._inline.docPr.set("descr", "Bar chart of tax collections")
    doc.add_paragraph("Figure 1: Tax collections by year", style="Caption")  # 7
    last = doc.add_paragraph("Please confirm the rate with the client.")  # 8, has a comment
    doc.add_comment(last.runs, text="Check this against the circular.", author="Reviewer", initials="R")
    path = DEMO / "guide.docx"
    doc.save(path)

    footnotes = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes xmlns:w="{W}">'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r>'
        '</w:p></w:footnote><w:footnote w:id="1"><w:p><w:r><w:footnoteRef/></w:r>'
        "<w:r><w:t>Section 234A applies.</w:t></w:r></w:p></w:footnote></w:footnotes>"
    )

    def add_footnotes(name: str, data: bytes) -> bytes:
        if name not in ("[Content_Types].xml", "word/_rels/document.xml.rels"):
            return data
        text = data.decode("utf8")
        if name == "[Content_Types].xml":
            override = (
                '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-'
                'officedocument.wordprocessingml.footnotes+xml"/>'
            )
            return text.replace("</Types>", override + "</Types>").encode("utf8")
        rel = (
            '<Relationship Id="rIdFootnotes1" Type="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships/footnotes" Target="footnotes.xml"/>'
        )
        return text.replace("</Relationships>", rel + "</Relationships>").encode("utf8")

    rezip(path, add_footnotes)
    with zipfile.ZipFile(path, "a") as archive:  # the new part, with the same fixed date
        info = zipfile.ZipInfo("word/footnotes.xml", ZIP_DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, footnotes)

    def docx_line(text, **anchor):
        return {"anchor": {"type": "docx", **anchor}, "text": text}

    cells = [("Slab", "Rate"), ("Up to 3 lakh", "Nil"), ("3 to 7 lakh", "5%")]
    keyfile(
        "guide.docx",
        [
            docx_line("Tax Guide 2026", paragraph=1),
            docx_line("Rates", paragraph=2),
            docx_line("Interest is charged at 12% a year.", paragraph=3),
            docx_line("The due date is 31 July each year.", paragraph=4),
            *[
                docx_line(text, table=1, cell=[r, c])
                for r, row in enumerate(cells, 1)
                for c, text in enumerate(row, 1)
            ],
            docx_line("Figure 1: Tax collections by year", paragraph=7),
            docx_line("Please confirm the rate with the client.", paragraph=8),
            docx_line("Section 234A applies.", note="footnote 1"),
            {
                "figure": {"type": "docx", "paragraph": 6},
                "description": "Figure 1: Tax collections by year",
                "description_source": "caption",
            },
            {"document_flag": "docx.tracked-changes"},
            {"document_flag": "docx.comments"},
        ],
    )


# --- rates.xlsx and rates-nocache.xlsx -----------------------------------------------------------

LAKH = '[>=10000000]"₹"##\\,##\\,##\\,##0;[>=100000]"₹"##\\,##\\,##0;"₹"##,##0'


def _cache(sheet_xml: bytes, cell: str, value: str) -> bytes:
    """Give a formula cell its cached value, which openpyxl cannot write."""
    text = sheet_xml.decode("utf8")
    pattern = re.compile(rf'(<c r="{cell}"[^>]*><f>[^<]*</f>)(<v\s*/>|<v></v>)?')
    text, count = pattern.subn(rf"\1<v>{value}</v>", text)
    assert count == 1, (cell, text)
    return text.encode("utf8")


def _xlsx(path: Path, workbook: Workbook, cache: tuple[str, str] | None) -> None:
    workbook.properties.created = workbook.properties.modified = FIXED
    workbook.properties.creator = workbook.properties.lastModifiedBy = "Demo"
    workbook.save(path)

    def change(name: str, data: bytes) -> bytes:
        if cache and name == "xl/worksheets/sheet1.xml":
            return _cache(data, *cache)
        return data

    rezip(path, change)


def make_xlsx() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Rates"
    ws["A1"] = "Item"
    ws["B1"] = "Values"
    ws.merge_cells("B1:D1")
    ws["A2"], ws["B2"] = "Rate", 0.05
    ws["B2"].number_format = "0%"
    ws["A3"], ws["B3"] = "Amount", 150000
    ws["B3"].number_format = LAKH
    ws["A4"], ws["B4"] = "Date", dt.datetime(2026, 11, 26)
    ws["B4"].number_format = "dd-mmm-yyyy"
    ws["A5"], ws["B5"] = "Tax", "=B2*B3"
    ws["A6"], ws["B6"] = "Odd", 1234.5
    ws["B6"].number_format = "0.0E+00;;"
    ws["A9"], ws["B9"] = "Secret row", "hidden"
    ws.row_dimensions[9].hidden = True
    ws["F2"] = "hidden column"
    ws.column_dimensions["F"].hidden = True
    hidden = wb.create_sheet("Lookup")
    hidden["A1"] = "secret"
    hidden.sheet_state = "hidden"
    _xlsx(DEMO / "rates.xlsx", wb, ("B5", "7500"))

    def cell(ref, text, flag=None):
        item = {"anchor": {"type": "xlsx", "sheet": "Rates", "cell": ref}, "text": text}
        return {**item, "flag": flag} if flag else item

    keyfile(
        "rates.xlsx",
        [
            cell("A1", "Item"),
            cell("B1:D1", "Values"),
            cell("A2", "Rate"),
            cell("B2", "5%"),
            cell("A3", "Amount"),
            cell("B3", "₹1,50,000"),
            cell("A4", "Date"),
            cell("B4", "26-Nov-2026"),
            cell("A5", "Tax"),
            cell("B5", "7500"),
            cell("A6", "Odd"),
            cell("B6", "1234.5", "xlsx.format-unknown"),
            {"document_flag": "xlsx.hidden"},
        ],
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Rates"
    ws["A1"], ws["B1"] = "Base", 100
    ws["A2"], ws["B2"] = "Tax", "=B1*2"
    _xlsx(DEMO / "rates-nocache.xlsx", wb, None)
    keyfile(
        "rates-nocache.xlsx",
        [cell("A1", "Base"), cell("B1", "100"), cell("A2", "Tax"), cell("B2", "", "xlsx.no-cached-value")],
    )


# --- deck.pptx -----------------------------------------------------------------------------------


def make_pptx() -> None:
    deck = Presentation()
    props = deck.core_properties
    props.created = props.modified = FIXED
    props.author = props.last_modified_by = "Demo"
    first = deck.slides.add_slide(deck.slide_layouts[0])
    first.shapes.title.name = "Title 1"
    first.shapes.title.text = "Budget Briefing 2026"
    first.placeholders[1].name = "Subtitle 2"
    first.placeholders[1].text = "Ministry of Finance"

    second = deck.slides.add_slide(deck.slide_layouts[1])
    second.shapes.title.name = "Title 1"
    second.shapes.title.text = "Key changes"
    body = second.placeholders[1]
    body.name = "Content 2"
    body.text_frame.text = "New slab above 15 lakh"
    body.text_frame.add_paragraph().text = "Standard deduction raised"
    second.notes_slide.notes_text_frame.text = "Mention the transition rule."

    third = deck.slides.add_slide(deck.slide_layouts[5])
    third.shapes.title.name = "Title 1"
    third.shapes.title.text = "Collections"
    picture = third.shapes.add_picture(io.BytesIO(png(DEMO / "x.png", rupees)), PInches(1), PInches(2))
    picture.name = "Picture 2"
    del picture._element.nvPicPr.cNvPr.attrib["descr"]  # python-pptx fills it with a file name

    fourth = deck.slides.add_slide(deck.slide_layouts[5])
    fourth.shapes.title.name = "Title 1"
    fourth.shapes.title.text = "Slabs"
    frame = fourth.shapes.add_table(2, 2, PInches(1), PInches(2), PInches(6), PInches(1))
    frame.name = "Table 2"
    for r, row in enumerate((("Slab", "Rate"), ("Up to 12 lakh", "Nil"))):
        for c, text in enumerate(row):
            frame.table.cell(r, c).text = text
    path = DEMO / "deck.pptx"
    deck.save(path)
    rezip(path)

    def line(slide, shape, text, paragraph=0, notes=False, cell=None):
        anchor = {"type": "pptx", "slide": slide, "shape": shape, "paragraph": paragraph}
        if cell:
            anchor["cell"] = list(cell)
        return {"anchor": {**anchor, "notes": True} if notes else anchor, "text": text}

    keyfile(
        "deck.pptx",
        [
            line(1, "Title 1", "Budget Briefing 2026"),
            line(1, "Subtitle 2", "Ministry of Finance"),
            line(2, "Title 1", "Key changes"),
            line(2, "Content 2", "New slab above 15 lakh", 0),
            line(2, "Content 2", "Standard deduction raised", 1),
            line(2, "Notes", "Mention the transition rule.", 0, notes=True),
            line(3, "Title 1", "Collections"),
            {
                "figure": {"type": "pptx", "slide": 3, "shape": "Picture 2", "paragraph": 0, "notes": False},
                "description": "",
                "description_source": "none",
            },
            line(4, "Title 1", "Slabs"),
            line(4, "Table 2", "Slab", cell=(1, 1)),
            line(4, "Table 2", "Rate", cell=(1, 2)),
            line(4, "Table 2", "Up to 12 lakh", cell=(2, 1)),
            line(4, "Table 2", "Nil", cell=(2, 2)),
        ],
    )


# --- html, markdown, json, xml, csv --------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
<h1>Public Notice</h1>
<p>Returns are due by 31 July 2026.</p>
<table>
<tr><th>Slab</th><th>Rate</th></tr>
<tr><td>Up to 3 lakh</td><td>Nil</td></tr>
</table>
<img alt="Logo" src="logo.png">
<img src="https://example.org/x.png">
<script>var hidden = "this is not text";</script>
</body>
</html>
"""

MD = """# Guide to Rates

Interest applies to late payments.

## Filing

| Slab | Rate |
| --- | --- |
| Up to 3 lakh | Nil |

```python
print("tax")
```

![Diagram](diagram.png)
"""

JSON = """{
  "taxpayer": {"name": "Asha Rao", "pan": "ABCDE1234F"},
  "year": 2026,
  "limit": 100000,
  "limit": 150000,
  "deductions": [
    {"section": "80C", "amount": 150000, "note": "PPF"},
    {"section": "80D", "amount": 25000, "note": "Health"},
    {"section": "80G", "amount": 5000, "note": "Donation"}
  ]
}
"""

XML = """<?xml version="1.0" encoding="utf-8"?>
<notice id="N-7" date="2026-07-31">
  <title>Extension of due date</title>
  <body>
    <para>The due date is extended.</para>
    <para lang="hi">Please file early.</para>
  </body>
  <signatory role="Commissioner">A. Rao</signatory>
</notice>
"""

BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<lolz>&lol2;</lolz>
"""

CSV = 'name;rate;note\nCafé;5%;"Paid in €\nmonthly"\nTea;12%;plain\n'


def make_text_files() -> None:
    write("logo.png", png(DEMO / "logo.png", logo))
    write("diagram.png", png(DEMO / "diagram.png", diagram))

    write("notice.html", HTML)
    h = lambda path, text: {"anchor": {"type": "html", "path": path}, "text": text}  # noqa: E731
    keyfile(
        "notice.html",
        [
            h("/html/body/h1", "Public Notice"),
            h("/html/body/p", "Returns are due by 31 July 2026."),
            h("/html/body/table/tr[1]/th[1]", "Slab"),
            h("/html/body/table/tr[1]/th[2]", "Rate"),
            h("/html/body/table/tr[2]/td[1]", "Up to 3 lakh"),
            h("/html/body/table/tr[2]/td[2]", "Nil"),
            {
                "figure": {"type": "html", "path": "/html/body/img[1]"},
                "description": "Logo",
                "description_source": "alt",
            },
            {
                "figure": {"type": "html", "path": "/html/body/img[2]"},
                "description": "",
                "description_source": "none",
                "flag": "image.remote-not-fetched",
            },
        ],
    )

    write("readme.md", MD)

    def m(first, last, text):
        return {"anchor": {"type": "md", "lines": [first, last]}, "text": text}

    keyfile(
        "readme.md",
        [
            m(1, 1, "Guide to Rates"),
            m(3, 3, "Interest applies to late payments."),
            m(5, 5, "Filing"),
            m(7, 7, "Slab"),
            m(7, 7, "Rate"),
            m(9, 9, "Up to 3 lakh"),
            m(9, 9, "Nil"),
            m(11, 13, 'print("tax")'),
            {
                "figure": {"type": "md", "lines": [15, 15]},
                "description": "Diagram",
                "description_source": "alt",
            },
        ],
    )

    write("deductions.json", JSON)
    j = lambda pointer, text: {"anchor": {"type": "json", "pointer": pointer}, "text": text}  # noqa: E731
    keyfile(
        "deductions.json",
        [
            j("/taxpayer/name", "Asha Rao"),
            j("/taxpayer/pan", "ABCDE1234F"),
            j("/year", "2026"),
            j("/limit", "150000"),
            *[
                j(f"/deductions/{n}/{field}", text)
                for n, row in enumerate(
                    (("80C", "150000", "PPF"), ("80D", "25000", "Health"), ("80G", "5000", "Donation"))
                )
                for field, text in zip(("section", "amount", "note"), row, strict=True)
            ],
            {"document_flag": "json.duplicate-key"},
        ],
    )

    write("notice.xml", XML)
    x = lambda path, text: {"anchor": {"type": "xml", "path": path}, "text": text}  # noqa: E731
    keyfile(
        "notice.xml",
        [
            x("/notice/@id", "N-7"),
            x("/notice/@date", "2026-07-31"),
            x("/notice/title", "Extension of due date"),
            x("/notice/body/para[1]", "The due date is extended."),
            x("/notice/body/para[2]/@lang", "hi"),
            x("/notice/body/para[2]", "Please file early."),
            x("/notice/signatory/@role", "Commissioner"),
            x("/notice/signatory", "A. Rao"),
        ],
    )

    write("bomb.xml", BOMB)
    keyfile("bomb.xml", [{"document_flag": "xml.entities"}])

    write("rates.csv", CSV.encode("cp1252"))

    def c(row, column, text):
        return {"anchor": {"type": "csv", "row": row, "column": column}, "text": text}

    keyfile(
        "rates.csv",
        [
            c(1, "name", "name"),
            c(1, "rate", "rate"),
            c(1, "note", "note"),
            c(2, "name", "Café"),
            c(2, "rate", "5%"),
            c(2, "note", "Paid in €\nmonthly"),
            c(3, "name", "Tea"),
            c(3, "rate", "12%"),
            c(3, "note", "plain"),
            {"document_flag": "encoding.guessed"},
        ],
    )


def main() -> None:
    DEMO.mkdir(parents=True, exist_ok=True)
    make_docx()
    make_xlsx()
    make_pptx()
    make_text_files()


if __name__ == "__main__":
    main()
