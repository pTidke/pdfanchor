"""Make references/chunking/generated: the tuning half of the chunking reference set.

Run with:  python references/chunking/make_generated.py

Each file holds invented, plain-English text and exercises a feature that one chunking strategy
depends on. The output is byte-identical run to run: dates are fixed, zip entries carry a fixed
date, PDFs are built with reportlab's `invariant=1`, and nothing is random. The questions in
`generated.questions.jsonl` were written by hand from the text as `ragnaroq convert` reads it.
"""

import datetime as dt
import json
import re
import zipfile
from pathlib import Path

from docx import Document as Docx
from openpyxl import Workbook
from pptx import Presentation
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

HERE = Path(__file__).parent
FIXED = dt.datetime(2026, 11, 26, 9, 0, 0)
ZIP_DATE = (2026, 11, 26, 9, 0, 0)


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


# --- notice.pdf -------------------------------------------------------------------------------

NOTICE_TITLE = "Notice of Temporary Market Stall Permits"

NOTICE = [
    "1. The Borough Council gives notice that applications for temporary stall permits at the Riverside "
    "Market will be accepted from the 5th January, 2026 and will close when every pitch for the trading "
    "period 2026-27 has been allotted.",
    "2. A stall holder must hold public liability insurance of not less than two million pounds and must "
    "show the certificate to the market officer on the first day of trading, as required by section 4(2) "
    "of the Market Regulations.",
    "3. Pitches are allotted in the order in which complete applications are received. An application is "
    "complete when the form, the insurance certificate, a photograph of the stall and the fee have all "
    "reached the market office.",
    "4. The fee for a full-day pitch is twenty-two pounds and the fee for a half-day pitch is fourteen "
    "pounds. Provided that a charity registered in the Borough shall pay half of the fee for a full-day "
    "pitch on not more than six days in the trading period.",
    "5. A stall holder who sells food must register the stall with the environmental health team, keep "
    "the stall clean throughout the day and display the hygiene rating given at the last inspection. "
    "The market officer may inspect any stall at any time during opening hours without giving notice. "
    "Where a stall is found to be unclean the officer will first ask the holder to clean it at once, and "
    "will record the request in the stall register. A second finding of the same fault within a month "
    "is recorded as a warning, and the holder receives the warning in writing before the next market "
    "day. A third finding within the trading period may lead to the permit being withdrawn for the rest "
    "of that period, though the holder may ask for the decision to be reviewed within seven days. The "
    "review is carried out by a senior officer who was not involved in the inspections, and the holder "
    "may bring one person to speak for them at the review meeting. Food that is kept at the wrong "
    "temperature, food that is past its date and food that is stored on the ground must be removed from "
    "sale immediately and may be taken away by the officer for disposal. Raw meat and fish must be kept "
    "apart from food that is ready to eat, in covered containers, and the holder must provide a separate "
    "knife and board for each. Every stall that cooks on site must keep a fire blanket and a fire "
    "extinguisher within reach of the cook, and gas bottles must stand upright and be fixed so that they "
    "cannot fall. Cooking oil must never be poured onto the ground or into the drains of the market "
    "square, and the holder must take used oil away in a sealed container at the end of each day. "
    "Stall holders who play music or use a generator must keep the noise low enough that a customer can "
    "speak to the person at the next stall without raising their voice. Waste from the stall, including "
    "packaging, boxes and cooking waste, must be bagged and placed in the bins at the east gate before "
    "the holder leaves, and the pitch must be left clear of litter, grease and straw.",
    "6. Provided that the market officer may refuse a permit to an applicant who has had a permit "
    "withdrawn in the previous two trading periods, the applicant may appeal to the Licensing Committee "
    "within fourteen days.",
    "7. Explanation.—A trading period begins on the first Saturday of April and ends on the last "
    "Saturday of the following March, and a day pitch is a pitch used between eight in the morning and "
    "six in the evening.",
    "8. Applications and enquiries should be sent to the Market Office, Town Hall Annexe, or by "
    "email to the address on the Council website, and every applicant will receive a written answer "
    "within ten working days.",
]


def make_notice(out: Path) -> None:
    styles = getSampleStyleSheet()
    path = out / "notice.pdf"
    document = SimpleDocTemplate(
        str(path), pagesize=A4, invariant=1, title=NOTICE_TITLE, author="Borough Council"
    )
    story = [Paragraph(NOTICE_TITLE, styles["Title"]), Spacer(1, 12)]
    for text in NOTICE:
        story.append(Paragraph(text, styles["BodyText"]))
        story.append(Spacer(1, 6))
    document.build(story)


# --- faq.docx ---------------------------------------------------------------------------------

LONG_RENEW = (
    "Most items can be renewed up to five times, provided that nobody else has reserved them. You can "
    "renew online by signing in with your card number and PIN, by phone, by email or at any desk in "
    "the building. Each renewal adds another three weeks to the loan, and the new due date is shown on "
    "the receipt and in your account. Items on the short loan shelf, such as new films and the most "
    "popular novels, can be renewed only once, and items that have been reserved cannot be renewed at "
    "all because the next reader is waiting for them. If the system refuses a renewal, it will say why "
    "and will show the date by which the item must come back. Renewing does not remove a fine that has "
    "already begun, so renew before the due date rather than after it. If you are going away for a long "
    "time, ask a member of staff to extend your loan before you go, because we can add up to eight "
    "weeks for holidays and hospital stays. The extension is recorded on your account and nothing more "
    "is needed from you when you return. People who cannot visit the library because of illness or "
    "disability can ask for the home delivery service, and the volunteers who run it will renew items "
    "for you whenever they call."
)

LONG_FINES = (
    "Adult items are charged at twenty pence a day, to a maximum of five pounds an item. Children's items "
    "are free of charge, and so are large-print books, audio books for people with sight loss and "
    "items borrowed on a young carer's card. The charge stops being added once you bring the item back "
    "and begins again only if you borrow it a second time and keep it too long. If you have lost an "
    "item, tell us as soon as you know, because the charge for a lost item is the price of a "
    "replacement and is less if the item is reported within a month of its due date. You can pay by "
    "card at the desk, by card on the website, or in cash, and you will always receive a receipt. If "
    "the amount you owe goes over ten pounds, your card is stopped until you have paid enough to bring "
    "the amount below that figure. If an item is lost in a flood, a fire or a burglary, show us the report from the insurer or the police and we will cancel the charge for it. Anyone who finds a charge hard to pay can talk to the library "
    "manager in private, and the manager may agree to spread the payments over several months or to "
    "cancel part of the charge when the circumstances are exceptional. We do not pass any charge to a "
    "debt agency, and we never charge a person for an item that was damaged before it was lent. "
    "Charges raised in error are always cancelled, and an error is any item that was returned on time "
    "but not recorded as returned, so keep your return receipt for a month."
)

LONG_ROOMS = (
    "The community room seats forty people at tables or seventy in rows, and it has a projector, a "
    "screen, a sound system and a kitchen with a kettle, an urn and a fridge. Local groups can book "
    "it for up to four hours at a time at a low hourly rate, and registered charities pay half of that "
    "rate. A booking must be made at least a week ahead, in writing or at the desk, and the person "
    "who makes it is responsible for the room until the key is handed back. The room must be left as "
    "it was found, with the chairs stacked, the floor swept, the tables wiped, the rubbish bagged and "
    "the kitchen clean. Alcohol may not be served without the manager's written permission, and "
    "events that charge an entry fee must tell us so when they book. We keep the room free on the "
    "first Tuesday of every month for the reading group, and during school exams we keep it as a quiet "
    "study space between nine and five. The room has step-free access from the car park, an induction "
    "loop for people who use hearing aids and a quiet corner for anyone who finds a crowd tiring. "
    "Cancellations made more than forty-eight hours before the booking carry no charge, and later "
    "ones are charged at half the hourly rate so that we can offer the room to somebody else."
)

FAQ = [
    (
        "How do I join the library?",
        "Bring any document that shows your name and address to the front desk, and you will leave with a card the same day. Membership is free for everyone who lives, works or studies in the county.",
    ),
    (
        "How many items can I borrow at once?",
        "Adults may borrow up to twenty items at a time, and children may borrow up to twelve. Films and games count as two items each, and magazines do not count at all.",
    ),
    (
        "How long can I keep what I borrow?",
        "Books and magazines are lent for three weeks, films and games for one week, and talking books for four weeks. Your receipt shows the date on which each item is due back.",
    ),
    ("Can I renew my loans?", LONG_RENEW),
    ("What happens if I return an item late?", LONG_FINES),
    (
        "Can I reserve a book that is out on loan?",
        "Yes, reservations cost nothing for members. Ask at the desk or use the website, and we will send you an email or a text message when the book is ready to collect. Reserved books are held on the shelf behind the desk for ten days.",
    ),
    (
        "Is there Wi-Fi and are there computers I can use?",
        "There is free Wi-Fi throughout the building, and twelve public computers that can be booked for an hour at a time. Staff will help you with printing, scanning and filling in online forms, and printing costs ten pence a page.",
    ),
    ("Can I hire the community room?", LONG_ROOMS),
    (
        "Do you run activities for children?",
        "Rhyme Time for babies is held every Wednesday at half past ten, and the story club for children aged five to eight meets on Saturday mornings. During the summer holidays we run a reading challenge with a small prize for every child who finishes it.",
    ),
    (
        "Can I donate books to the library?",
        "We are glad to receive books in good condition that are less than ten years old. Please bring no more than two bags at a time to the desk, because we cannot store large collections and we pass unsold books to local charities.",
    ),
    (
        "What are your opening hours?",
        "The library opens from nine to seven on Monday, Tuesday and Thursday, from nine to five on Wednesday and Friday, and from ten to four on Saturday. It is closed on Sundays and on public holidays.",
    ),
    (
        "How do I suggest a book for the library to buy?",
        "Fill in a suggestion card at the desk or send the title through the website. We buy most suggestions that fit the collection, and we will tell you when the book arrives so that you can be the first to borrow it.",
    ),
]


def make_faq(out: Path) -> None:
    doc = Docx()
    doc.core_properties.created = FIXED
    doc.core_properties.modified = FIXED
    doc.core_properties.title = "Library questions and answers"
    doc.add_heading("Library questions and answers", level=1)
    for question, answer in FAQ:
        doc.add_paragraph(f"Q. {question}")
        doc.add_paragraph(f"A. {answer}")
    path = out / "faq.docx"
    doc.save(path)
    rezip(path)


# --- manual.md / manual.html ------------------------------------------------------------------

MANUAL_TITLE = "Pennant Backup User Manual"

# (level, heading, paragraphs)
MANUAL = [
    (
        1,
        "Installing Pennant",
        [
            "Pennant Backup copies chosen folders to a second disk or to a server on your network, and keeps older versions of every file so that a mistake can be undone.",
        ],
    ),
    (
        2,
        "System requirements",
        [
            "Pennant runs on any computer with at least four gigabytes of memory and two gigabytes of free disk space for the program and its settings.",
            "The backup disk should be at least as large as the folders you want to protect, and twice as large if you want to keep a year of older versions.",
        ],
    ),
    (
        3,
        "Download and verify",
        [
            "Download the installer from the product page and compare the checksum shown there with the one your computer reports before you open the file.",
        ],
    ),
    (
        3,
        "Run the installer",
        [
            "Double-click the installer, accept the licence and choose the folder for the program. The installer takes about a minute and does not need a restart.",
        ],
    ),
    (2, "First start", []),
    (
        1,
        "Making backups",
        [
            "A backup is a copy of your chosen folders made at a time you choose. The first backup copies everything, and later ones copy only what has changed.",
        ],
    ),
    (
        2,
        "Choosing folders",
        [
            "Press Add folder and pick the folders to protect. Pennant skips temporary files and the recycle bin unless you tell it to include them.",
            "Large video files are copied in pieces so that a failed copy can carry on from the point where it stopped instead of starting again.",
        ],
    ),
    (
        2,
        "Setting a schedule",
        [
            "Open the Schedule tab and choose how often to run: every hour, every night or every week. A backup that is missed because the computer was off runs as soon as it is switched on again.",
            "Nightly backups start at one in the morning by default, and you can move the start time to any hour when you are unlikely to be working.",
        ],
    ),
    (
        3,
        "Pausing a backup",
        [
            "Press Pause on the tray icon to stop a running backup. It will carry on from the same place when you press Resume.",
        ],
    ),
    (
        1,
        "Getting files back",
        [
            "To get a file back, open the Restore tab, find the file by name or by the date it was last saved, and press Restore.",
        ],
    ),
    (
        2,
        "Restoring older versions",
        [
            "Pennant keeps thirty daily versions, twelve monthly versions and every yearly version of each file. Choose the version by its date and restore it beside the current file so that nothing is overwritten.",
        ],
    ),
    (
        2,
        "Troubleshooting",
        [
            "If a backup fails, open the Log tab. Each failed file is listed with the reason, and most failures are caused by a file that was open in another program or by a disk that is full.",
            "If the backup disk is not recognised, unplug it and plug it in again, and then press Rescan in the Settings tab.",
        ],
    ),
]


def manual_markdown() -> str:
    lines = []
    for level, heading, paragraphs in MANUAL:
        lines.append(f"{'#' * level} {heading}")
        lines.append("")
        for paragraph in paragraphs:
            lines.extend([paragraph, ""])
    return "\n".join(lines)


def manual_html() -> str:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        f'<head><meta charset="utf-8"><title>{MANUAL_TITLE}</title></head>',
        "<body>",
    ]
    for level, heading, paragraphs in MANUAL:
        parts.append(f"<h{level}>{heading}</h{level}>")
        parts.extend(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)


# --- contract.docx ----------------------------------------------------------------------------

CONTRACT_TITLE = "Cleaning Services Agreement"

CONTRACT = [
    ("heading", "1. Definitions"),
    ("text", "In this Agreement the following words have the meanings given here."),
    ("text", '"Client" means Harbour Dental Practice, the business that receives the Services.'),
    ("text", '"Contractor" means Brightway Cleaning Limited, the business that provides the Services.'),
    (
        "text",
        '"Premises" means the clinic at 14 Quay Street, including the waiting room, the treatment rooms and the toilets.',
    ),
    (
        "text",
        '"Services" means the cleaning work described in Schedule 1 and any extra work that the Client asks for in writing.',
    ),
    ("text", '"Fee" means the monthly sum set out in Schedule 2, before tax.'),
    ("text", '"Term" means the period of twelve months that begins on the Start Date.'),
    ("heading", "2. The Services"),
    (
        "text",
        "2.1 The Contractor shall clean the Premises every weekday evening after the last patient has left, and shall finish by eight o'clock.",
    ),
    (
        "text",
        "2.2 The Contractor shall bring its own materials and equipment, and shall use only products that are safe for use in a clinic.",
    ),
    ("heading", "3. Payment"),
    ("text", "3.1 The Client shall pay the Fee within fourteen days of receiving each monthly invoice."),
    (
        "text",
        "3.2 If the Client pays late, the Contractor may charge interest on the unpaid amount at two per cent a month.",
    ),
    ("heading", "4. Keys and access"),
    (
        "text",
        "4.1 The Client shall give the Contractor one set of keys and the alarm code, and the Contractor shall keep them safe and shall not copy them.",
    ),
    (
        "text",
        "4.2 The Contractor shall tell the Client at once if a key is lost, and the Client may change the locks at the Contractor's cost.",
    ),
    ("heading", "5. Ending this Agreement"),
    ("text", "5.1 Either party may end this Agreement by giving the other two months' notice in writing."),
    (
        "text",
        "5.2 The Client may end this Agreement at once if the Contractor repeatedly fails to clean the Premises to the standard in Schedule 1.",
    ),
]


def make_contract(out: Path) -> None:
    doc = Docx()
    doc.core_properties.created = FIXED
    doc.core_properties.modified = FIXED
    doc.core_properties.title = CONTRACT_TITLE
    doc.add_heading(CONTRACT_TITLE, level=1)
    for kind, text in CONTRACT:
        if kind == "heading":
            doc.add_heading(text, level=2)
        else:
            doc.add_paragraph(text)
    path = out / "contract.docx"
    doc.save(path)
    rezip(path)


# --- deck.pptx --------------------------------------------------------------------------------

DECK = [
    (
        "Welcome to Customer Care Training",
        "Today we learn how to help every caller well.",
        "Greet the room and explain that the session lasts ninety minutes with a break in the middle.",
    ),
    (
        "Listen before you answer",
        "Let the caller finish before you speak, and repeat the problem back to them in your own words.",
        "Tell the story of the caller who was interrupted three times and hung up. Ask the group what went wrong.",
    ),
    (
        "Find the real problem",
        "Ask open questions such as what happened, when it began and what the caller has already tried.",
        "Give each pair a card with a vague complaint and ask them to find out what is really wrong in two minutes.",
    ),
    (
        "Say what happens next",
        "Always end by telling the caller what you will do, when you will do it and how they will hear about it.",
        "Stress that a promise with no date is not a promise. Hand out the follow-up checklist.",
    ),
    (
        "Handling an angry caller",
        "Stay calm, lower your voice, say sorry for the trouble and move to the solution as soon as you can.",
        "Role play: one person is the caller and one the agent. Swap after five minutes and discuss what helped.",
    ),
    (
        "Review and next steps",
        "Practise the four steps this week and bring one difficult call to the next session.",
        "Thank everyone. Collect the feedback forms and remind them that the next session is on the first Thursday of next month.",
    ),
]


def make_deck(out: Path) -> None:
    prs = Presentation()
    prs.core_properties.created = FIXED
    prs.core_properties.modified = FIXED
    prs.core_properties.title = "Customer care training"
    for title, body, notes in DECK:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        slide.placeholders[1].text = body
        slide.notes_slide.notes_text_frame.text = notes
    path = out / "deck.pptx"
    prs.save(path)
    rezip(path)


# --- rates.xlsx -------------------------------------------------------------------------------

ITEMS = ["Spruce plank", "Oak beam", "Pine board", "Birch panel", "Cedar post", "Maple block"]
CLASSES = ["Grade A", "Grade B", "Grade C", "Grade D", "Rough"]
SIZES = ["2.4 metres", "3.0 metres", "3.6 metres", "4.2 metres", "4.8 metres"]
DEPOTS = ["North depot", "South depot", "East depot", "West depot", "Central depot"]


def rate_rows() -> list[list]:
    rows = []
    for n in range(300):
        item = ITEMS[n % 6]
        grade = CLASSES[(n // 6) % 5]
        size = SIZES[(n // 30) % 5]
        depot = DEPOTS[(n * 7) % 5]
        price = 12 + (n * 37) % 90 + (n % 4) * 0.25
        rows.append([f"R{n + 1:03d}", f"{item} {grade}", size, depot, price])
    return rows


def fix_modified(name: str, data: bytes) -> bytes:
    if name != "docProps/core.xml":
        return data
    text = re.sub(r"(<dcterms:modified[^>]*>)[^<]*", rf"\g<1>{FIXED:%Y-%m-%dT%H:%M:%SZ}", data.decode("utf8"))
    return text.encode("utf8")


def make_rates(out: Path) -> None:
    wb = Workbook()
    wb.properties.created = FIXED
    wb.properties.modified = FIXED
    wb.properties.title = "Timber rates"
    sheet = wb.active
    sheet.title = "Rates"
    sheet.append(["Code", "Product", "Length", "Depot", "Price per piece"])
    for row in rate_rows():
        sheet.append(row)
    path = out / "rates.xlsx"
    wb.save(path)  # openpyxl stamps `modified` with the clock on save, so put the fixed date back
    rezip(path, fix_modified)


# --- records.json / table.json ----------------------------------------------------------------

RECORDS = {
    "equipment": {
        "excavator-01": {
            "description": "Tracked excavator used for foundations and drainage trenches",
            "location": {"site": "Millbrook Road", "bay": 3},
            "service": {
                "last": "2026-09-14",
                "hours": 1840,
                "notes": "Hydraulic hose replaced after a small leak",
            },
        },
        "generator-07": {
            "description": "Quiet diesel generator that powers the site office",
            "location": {"site": "Millbrook Road", "bay": 1},
            "service": {
                "last": "2026-10-02",
                "hours": 620,
                "notes": "Oil and filters changed, fuel tank cleaned",
            },
        },
        "scaffold-12": {
            "description": "Steel scaffold tower set with six boards and a safety rail",
            "location": {"site": "Church Lane", "bay": 2},
            "service": {
                "last": "2026-08-21",
                "hours": 0,
                "notes": "Inspected by the safety officer and tagged green",
            },
        },
        "mixer-03": {
            "description": "Portable concrete mixer with a one hundred and fifty litre drum",
            "location": {"site": "Church Lane", "bay": 5},
            "service": {
                "last": "2026-07-30",
                "hours": 410,
                "notes": "Drum paddles welded and the motor belt replaced",
            },
        },
    },
    "operators": [
        {"name": "Ines Carvalho", "licence": "Class B", "approved": ["excavator-01", "mixer-03"]},
        {"name": "Tomas Brandt", "licence": "Class A", "approved": ["generator-07", "scaffold-12"]},
    ],
}

TABLE = [
    {
        "stop": "Harbour Gate",
        "route": "14",
        "first_bus": "05:40",
        "last_bus": "23:10",
        "accessible": "yes",
        "note": "Sheltered stop beside the ferry terminal",
    },
    {
        "stop": "Market Square",
        "route": "14",
        "first_bus": "05:44",
        "last_bus": "23:14",
        "accessible": "yes",
        "note": "Stop is closed on Saturdays when the market is held",
    },
    {
        "stop": "Old Mill",
        "route": "9",
        "first_bus": "06:05",
        "last_bus": "22:35",
        "accessible": "no",
        "note": "Steep kerb and no ramp, so wheelchairs cannot board here",
    },
    {
        "stop": "Station Road",
        "route": "9",
        "first_bus": "06:12",
        "last_bus": "22:42",
        "accessible": "yes",
        "note": "Ticket machine and a covered bench outside the station",
    },
    {
        "stop": "Hilltop School",
        "route": "21",
        "first_bus": "06:30",
        "last_bus": "19:50",
        "accessible": "no",
        "note": "Runs on school days only and stops at the main gate",
    },
    {
        "stop": "Riverside Park",
        "route": "21",
        "first_bus": "06:36",
        "last_bus": "19:56",
        "accessible": "yes",
        "note": "Level boarding next to the playground and the cafe",
    },
    {
        "stop": "County Hospital",
        "route": "33",
        "first_bus": "05:15",
        "last_bus": "00:20",
        "accessible": "yes",
        "note": "Night service stops outside the emergency entrance",
    },
    {
        "stop": "Airport Terminal",
        "route": "33",
        "first_bus": "05:55",
        "last_bus": "00:50",
        "accessible": "yes",
        "note": "Luggage racks on every bus and a fifteen minute wait at peak times",
    },
]

# --- essay.md ---------------------------------------------------------------------------------

ESSAY = [
    "A city is often judged by its buildings, but the plainest measure of how well it treats its people may be the trees along its streets. A mature plane or lime tree can cool a pavement by several degrees on a summer afternoon, and the shade it casts lets old people walk to the shops when the sun would otherwise keep them at home. The cooling does not come from shadow alone. A leaf gives off water as vapour, and the vapour takes heat from the air around it, so that a street lined with trees may be cooler than a street of the same width that has none.",
    "Trees also clean the air in ways that are easy to overlook. Their leaves catch fine dust and soot, and a hedge or a row of trees between a busy road and a school playground can lower the amount of dust that children breathe. The effect is modest and trees cannot replace cleaner engines, but it is real, and it costs very little compared with the price of a filter on every chimney. The same leaves slow the rain as it falls, so that less water runs into the drains at once, and the roots open the soil to let water soak away. A district with many trees is less likely to flood when a storm comes.",
    "The difficulty is that a street tree has a hard life. Its roots must find water under tarmac, its trunk is knocked by lorries and its branches are cut by contractors who work to a timetable. Many young trees die in the first five years because nobody waters them in a dry spring, and many older trees are lost because a pipe is laid through their roots without a thought for what is above. Planting a tree is the easy and cheerful part of the work. Keeping it alive, year after year, is a matter of patience and of money, and money is what councils lack.",
    "Some cities have found that the answer lies in sharing the work. In one district, each tree has a name on a small tag, and neighbours who agree to water it in summer sign their names on a list at the library. The council pays for the pruning and the replacements, and the neighbours supply the buckets and the watching. Losses of young trees in that district fell by half in four years. More interesting than the figure was the change in how people spoke about the street, which they began to call theirs, and some of them started to sweep the leaves from the pavement without being asked.",
    "There is also the matter of choosing the right tree. A species that thrives in a park may sicken beside a road where salt is spread in winter, and one that is lovely in spring may drop sticky sap on parked cars all summer and be hated for it. Planners now try to mix species, so that a single disease cannot strip a whole street bare, as the elm disease did in the last century. A mixed street is also better for birds and insects, which need different trees at different times of the year, and a street that sings in the morning is a street that people are glad to live in.",
    "None of this means that trees are always welcome. Roots can lift paving stones and crack old drains, leaves can block gutters in autumn, and a tree that grows too tall can darken the windows of the people who live beneath it. A fair policy admits these costs and meets them, with regular pruning, with root barriers for new trees and with quick repairs when damage is done. People forgive a good deal when they see that someone is looking after the problem, and they grow angry only when a complaint is ignored for a year and then answered with a form.",
    "Money is the hardest part of the argument, and it deserves a plain answer. A street tree costs perhaps three hundred pounds to buy, plant and stake, and a good deal more to look after over its first ten years. Against that must be set the saving in drains, the rise in the value of nearby houses and the fall in summer bills for cooling, and careful studies in several countries suggest that the benefits exceed the costs by a wide margin. The trouble is that the costs fall on the council this year while the benefits arrive slowly and are shared among thousands of people, none of whom sends a bill. A budget that is balanced one year at a time will always favour the pothole over the tree.",
    "Children are a further reason. A child who grows up on a street with trees learns the names of a few of them, watches them change from month to month and climbs one when no adult is looking. Teachers who take classes to a nearby avenue report that pupils write more and better about a tree they have measured with string than about any picture in a book. Such lessons cost almost nothing, and they leave the child with a small and lasting sense that the place they live in is alive, and that its life is partly in their keeping.",
    "Maintenance deserves its own paragraph, because it is the part that most often fails. A young tree needs about twenty litres of water a week through its first two summers, and a plastic bag fitted around the base and filled once a week does the work of a watering can for a fraction of the effort. Stakes must be loosened after two years or they will cut into the bark, and the grass around the trunk must be kept short so that a mower or a strimmer never strikes the tree. A tree surgeon who is called in early can remove a weak branch with one cut, while one who is called after a storm may have to take down the whole tree. Records matter too, since a council that knows the age, species and health of each tree can plan its pruning and its replacements years ahead.",
    "In the end the case for street trees rests on something simple. People like to be outdoors when the outdoors is pleasant, and a tree makes it pleasant at little cost. A shaded street is walked rather than driven, and a street that is walked is a street where shops stay open and neighbours say good morning. Anyone who doubts this should stand in a treeless car park at noon in July and then walk to the nearest shaded lane. Cities that planted trees a century ago are now enjoying the shade, and the cities that plant them today will give the same gift to people who have not yet been born.",
]


def make_essay(out: Path) -> None:
    (out / "essay.md").write_text("\n\n".join(ESSAY) + "\n", encoding="utf8")


# --- the set ----------------------------------------------------------------------------------


def make_generated(out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    make_notice(out)
    make_faq(out)
    (out / "manual.md").write_text(manual_markdown(), encoding="utf8")
    (out / "manual.html").write_text(manual_html(), encoding="utf8")
    make_contract(out)
    make_deck(out)
    make_rates(out)
    (out / "records.json").write_text(json.dumps(RECORDS, indent=2) + "\n", encoding="utf8")
    (out / "table.json").write_text(json.dumps(TABLE, indent=2) + "\n", encoding="utf8")
    make_essay(out)


def main() -> None:
    make_generated(HERE / "generated")


if __name__ == "__main__":
    main()
