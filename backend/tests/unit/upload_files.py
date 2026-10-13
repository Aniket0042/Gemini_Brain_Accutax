"""Builders for in-memory test files of every kind the upload feature accepts or refuses."""
from __future__ import annotations

import io
import zipfile


def pdf(pages: int = 1, password: str = "") -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def image(fmt: str = "PNG", size=(40, 30), mode: str = "RGB") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, "white" if mode != "1" else 1).save(buf, fmt)
    return buf.getvalue()


def docx(paragraphs=("Payment terms: 30 days.",), chars_extra: int = 0) -> bytes:
    import docx as docx_lib

    d = docx_lib.Document()
    for text in paragraphs:
        d.add_paragraph(text)
    if chars_extra:
        d.add_paragraph("x" * chars_extra)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def xlsx(sheets: int = 1, rows: int = 3) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    for i in range(sheets):
        ws = wb.active if i == 0 else wb.create_sheet()
        for r in range(rows):
            ws.append([r, f"line {r}", r * 1.5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(rows: int = 3) -> bytes:
    return ("date,ref,amount\n" + "\n".join(f"2026-01-{i % 28 + 1:02d},R{i},{i}.00" for i in range(rows))).encode()


def zip_with(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def macro_docx() -> bytes:
    """A real DOCX with a macro part added: the check must see it inside the ZIP."""
    base = zipfile.ZipFile(io.BytesIO(docx()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in base.infolist():
            z.writestr(info.filename, base.read(info.filename))
        z.writestr("word/vbaProject.bin", b"\x00" * 16)
    return buf.getvalue()


def zip_bomb() -> bytes:
    """About 60 MB when unpacked, a few dozen KB on the wire."""
    return zip_with({"word/document.xml": b"<w/>", "padding.bin": b"\x00" * (60 * 1024 * 1024)})


def text_pdf(lines, pages: int = 1) -> bytes:
    """A PDF with a real text layer."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595, 842))
    for _ in range(pages):
        y = 800
        for line in lines:
            c.drawString(40, y, line)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


BILL_LINES = ["TAX INVOICE", "Alex Adams Trading LLC", "TRN: 100123456700003", "Invoice No: 152643",
              "Date: 21/07/2018", "Transistor   2   75.00   150.00", "Total Sale 500.00", "VAT 5% 25.00",
              "Total Amount Paid 525.00"]
CONTRACT_LINES = ["Service agreement between the parties.", "Payment terms: thirty days from delivery.",
                  "The supplier shall deliver the goods to the buyer's warehouse.", "Governing law: the UAE.",
                  "Signed for the supplier. Signed for the buyer."]
