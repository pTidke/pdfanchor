"""The code-table stress test: 20 pages of a ruled table whose cells hold codes such as
R28C0 next to amounts such as 28,000. Codes that mix letters and digits are where OCR
confuses O and 0, I and 1, B and 8, and where two readers can agree on the same
mistake.

    python references/make_code_table.py code-table.pdf
"""

import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

page = canvas.Canvas(sys.argv[1], pagesize=A4)
for number in range(20):
    page.setFont("Helvetica", 10)
    page.drawString(40, 810, f"FORM No. 10A page {number + 1}: particulars of the assessee")
    rows = [[f"R{r}C{k} {1000 * r + k:,}" for k in range(6)] for r in range(40)]
    table = Table(rows, colWidths=[85] * 6, rowHeights=18)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    table.wrapOn(page, 520, 760)
    table.drawOn(page, 40, 60)
    page.showPage()
page.save()
