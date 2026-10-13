"""Upload checks: what is accepted, what is refused, and that the bytes decide, not the name."""
import pytest

from gemini_brain.config.settings import settings
from gemini_brain.uploads import validate
from gemini_brain.uploads.validate import UploadRejected, clean_filename, inspect
from tests.unit import upload_files as f


def _refused(content: bytes, name: str = "x.bin") -> UploadRejected:
    with pytest.raises(UploadRejected) as err:
        inspect(content, name)
    return err.value


@pytest.mark.parametrize("make,name,kind,pages,counted", [
    (lambda: f.pdf(3), "bill.pdf", "pdf", 3, 3),
    (lambda: f.image("PNG"), "photo.png", "png", 1, 1),
    (lambda: f.image("JPEG"), "photo.jpg", "jpeg", 1, 1),
    (lambda: f.image("WEBP"), "photo.webp", "webp", 1, 1),
    (lambda: f.docx(), "contract.docx", "docx", 1, 0),
    (lambda: f.xlsx(), "budget.xlsx", "xlsx", 1, 0),
    (lambda: f.csv_bytes(), "lines.csv", "csv", 1, 0),
], ids=["pdf", "png", "jpeg", "webp", "docx", "xlsx", "csv"])
def test_each_allowed_kind_is_recognised(make, name, kind, pages, counted):
    got = inspect(make(), name)
    assert (got.kind, got.pages, got.counted_pages) == (kind, pages, counted)
    assert got.mime == validate.KINDS[kind][0]


def test_only_pdfs_and_images_count_against_the_daily_cap():
    assert inspect(f.pdf(2), "a.pdf").model_read
    assert not inspect(f.docx(), "a.docx").model_read and not inspect(f.csv_bytes(), "a.csv").model_read


def test_the_bytes_decide_not_the_name():
    assert inspect(f.pdf(), "holiday.jpg").kind == "pdf"
    assert inspect(f.image("PNG"), "bill.pdf").kind == "png"
    assert _refused(b"just some text, not a csv by name", "notes.txt").code == "unsupported_type"


def test_executable_and_unknown_content_is_refused():
    assert _refused(b"MZ\x90\x00" + b"\x00" * 100, "setup.exe").code == "unsupported_type"
    assert _refused(f.zip_with({"a.txt": b"hi"}), "a.zip").code == "unsupported_type"


def test_empty_file_is_refused():
    assert _refused(b"", "a.pdf").code == "empty"


def test_size_limit_applies_to_the_byte_just_over(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 2048)
    pdf_bytes = f.pdf()
    monkeypatch.setattr(settings, "upload_max_bytes", len(pdf_bytes))
    assert inspect(pdf_bytes, "a.pdf").kind == "pdf"
    monkeypatch.setattr(settings, "upload_max_bytes", len(pdf_bytes) - 1)
    err = _refused(pdf_bytes, "a.pdf")
    assert err.code == "too_large" and err.http_status == 413


def test_pdf_page_limit(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_pages", 20)
    assert inspect(f.pdf(20), "a.pdf").pages == 20
    assert _refused(f.pdf(21), "a.pdf").code == "too_many_pages"


def test_password_protected_pdf_is_refused():
    assert _refused(f.pdf(1, password="secret"), "a.pdf").code == "encrypted"


def test_damaged_pdf_is_refused():
    assert _refused(b"%PDF-1.7\nthis is not a pdf at all", "a.pdf").code == "unreadable"


def test_pixel_bomb_is_refused_without_decoding_it():
    # 1-bit, 9000 x 9000 = 81 million pixels: tiny on disk, huge in memory
    err = _refused(f.image("PNG", size=(9000, 9000), mode="1"), "a.png")
    assert err.code == "image_too_large"


def test_truncated_image_is_refused():
    assert _refused(f.image("PNG", size=(400, 300))[:200], "a.png").code == "unreadable"


def test_macro_files_are_refused():
    assert _refused(f.macro_docx(), "a.docm").code == "macros"


def test_zip_bomb_is_refused():
    assert _refused(f.zip_bomb(), "a.docx").code == "too_large"


def test_docx_text_limit():
    assert inspect(f.docx(chars_extra=validate.MAX_TEXT_CHARS - 100), "a.docx").kind == "docx"
    assert _refused(f.docx(chars_extra=validate.MAX_TEXT_CHARS + 1), "a.docx").code == "too_much_text"


def test_docx_pages_are_an_estimate_from_its_text():
    assert inspect(f.docx(chars_extra=9500), "a.docx").pages == 4


def test_xlsx_limits():
    assert inspect(f.xlsx(sheets=10), "a.xlsx").kind == "xlsx"
    assert _refused(f.xlsx(sheets=11), "a.xlsx").code == "too_much_text"
    assert _refused(f.xlsx(rows=validate.MAX_ROWS + 1), "a.xlsx").code == "too_much_text"


def test_csv_must_be_utf8_text_with_a_csv_name():
    assert inspect("é,ü\n1,2\n".encode("utf-8-sig"), "a.csv").kind == "csv"
    assert _refused("é,ü".encode("latin-1"), "a.csv").code == "unreadable"
    assert _refused(b"a,b\x00\n1,2", "a.csv").code == "unsupported_type"
    assert _refused(f.csv_bytes(validate.MAX_ROWS + 5), "a.csv").code == "too_much_text"


def test_file_names_are_cleaned():
    assert clean_filename("../../etc/passwd", "pdf") == "passwd.pdf"
    assert clean_filename("C:\\Users\\a\\bill<1>.JPG", "jpeg") == "bill1.jpg"
    assert clean_filename("  ..  ", "pdf") == "document.pdf"
    assert clean_filename("فاتورة.pdf", "pdf") == "فاتورة.pdf"
    assert len(clean_filename("a" * 500 + ".pdf", "pdf")) <= 115
    assert "\x00" not in clean_filename("bill\x00.pdf", "pdf")
