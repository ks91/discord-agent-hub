from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile
from types import SimpleNamespace

from discord_agent_hub.document_extract import (
    DocumentExtractionError,
    extract_document_text,
    is_supported_document,
)


def test_is_supported_document_matches_requested_formats():
    assert is_supported_document("notes.txt")
    assert is_supported_document("paper.md")
    assert is_supported_document("table.csv")
    assert is_supported_document("scan.pdf")
    assert is_supported_document("draft.docx")
    assert is_supported_document("slides.pptx")
    assert is_supported_document("sheet.xlsx")
    assert not is_supported_document("legacy.doc")


def test_extract_document_text_reads_utf8_text_formats():
    raw = "hello\nworld\n".encode("utf-8")

    assert extract_document_text(filename="notes.txt", raw=raw) == "hello\nworld"
    assert extract_document_text(filename="notes.md", raw=raw) == "hello\nworld"
    assert extract_document_text(filename="notes.csv", raw=raw) == "hello\nworld"


def test_extract_document_text_reads_docx_paragraphs():
    raw = _build_zip(
        {
            "word/document.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
                "<w:body>"
                "<w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p>"
                "</w:body>"
                "</w:document>"
            )
        }
    )

    assert extract_document_text(filename="draft.docx", raw=raw) == "First paragraph.\n\nSecond paragraph."


def test_extract_document_text_reads_pptx_slides():
    raw = _build_zip(
        {
            "ppt/slides/slide1.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<p:sld xmlns:p='http://schemas.openxmlformats.org/presentationml/2006/main' "
                "xmlns:a='http://schemas.openxmlformats.org/drawingml/2006/main'>"
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Title</a:t></a:r></a:p>"
                "<a:p><a:r><a:t>Bullet</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>"
                "</p:sld>"
            )
        }
    )

    assert extract_document_text(filename="slides.pptx", raw=raw) == "[Slide 1]\nTitle\nBullet"


def test_extract_document_text_reads_xlsx_sheets():
    raw = _build_zip(
        {
            "xl/sharedStrings.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                "<si><t>Name</t></si><si><t>Alice</t></si>"
                "</sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                "<?xml version='1.0' encoding='UTF-8'?>"
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                "<sheetData>"
                "<row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1'><v>42</v></c></row>"
                "<row r='2'><c r='A2' t='s'><v>1</v></c></row>"
                "</sheetData>"
                "</worksheet>"
            ),
        }
    )

    assert extract_document_text(filename="sheet.xlsx", raw=raw) == "[Sheet 1]\nName\t42\nAlice"


def test_extract_document_text_prefers_pdftotext_when_available(monkeypatch):
    monkeypatch.setattr("discord_agent_hub.document_extract.shutil.which", lambda cmd: "/usr/bin/pdftotext")

    def fake_run(args, capture_output, text, check):
        with open(args[-1], "w", encoding="utf-8") as fh:
            fh.write("hello from pdftotext")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("discord_agent_hub.document_extract.subprocess.run", fake_run)

    assert extract_document_text(filename="paper.pdf", raw=b"%PDF-1.4") == "hello from pdftotext"


def test_extract_document_text_requires_optional_pdf_dependency(monkeypatch):
    import builtins

    monkeypatch.setattr("discord_agent_hub.document_extract.shutil.which", lambda cmd: None)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ModuleNotFoundError("No module named 'pypdf'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        extract_document_text(filename="paper.pdf", raw=b"%PDF-1.4")
    except DocumentExtractionError as exc:
        assert "pypdf" in str(exc)
    else:
        raise AssertionError("Expected missing PDF dependency to raise DocumentExtractionError")


def _build_zip(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()
