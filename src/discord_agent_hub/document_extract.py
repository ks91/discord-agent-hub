from __future__ import annotations

from io import BytesIO
import re
from zipfile import ZipFile
import xml.etree.ElementTree as ET


SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
}


class DocumentExtractionError(RuntimeError):
    pass


def is_supported_document(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in SUPPORTED_DOCUMENT_EXTENSIONS)


def extract_document_text(*, filename: str, raw: bytes) -> str:
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".csv")):
        return raw.decode("utf-8-sig", errors="replace").strip()
    if lower.endswith(".pdf"):
        return _extract_pdf_text(raw)
    if lower.endswith(".docx"):
        return _extract_docx_text(raw)
    if lower.endswith(".pptx"):
        return _extract_pptx_text(raw)
    if lower.endswith(".xlsx"):
        return _extract_xlsx_text(raw)
    raise DocumentExtractionError(f"Unsupported document type: {filename}")


def _extract_pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise DocumentExtractionError(
            "PDF support requires the optional `pypdf` dependency."
        ) from exc

    reader = PdfReader(BytesIO(raw))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages.append(text)
    return "\n\n".join(pages).strip()


def _extract_docx_text(raw: bytes) -> str:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            document_xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise DocumentExtractionError("DOCX file is missing word/document.xml") from exc

    root = ET.fromstring(document_xml)
    paragraphs = []
    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "p":
            continue
        chunks = []
        for child in paragraph.iter():
            if _local_name(child.tag) == "t" and child.text:
                chunks.append(child.text)
        text = "".join(chunks).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs).strip()


def _extract_pptx_text(raw: bytes) -> str:
    slide_pattern = re.compile(r"ppt/slides/slide(\d+)\.xml$")
    slide_entries: list[tuple[int, str]] = []
    with ZipFile(BytesIO(raw)) as archive:
        for name in archive.namelist():
            match = slide_pattern.match(name)
            if match:
                slide_entries.append((int(match.group(1)), name))

        slides = []
        for slide_number, slide_name in sorted(slide_entries):
            root = ET.fromstring(archive.read(slide_name))
            texts = []
            for item in root.iter():
                if _local_name(item.tag) == "t" and item.text:
                    texts.append(item.text.strip())
            slide_text = "\n".join(text for text in texts if text)
            if slide_text:
                slides.append(f"[Slide {slide_number}]\n{slide_text}")
    return "\n\n".join(slides).strip()


def _extract_xlsx_text(raw: bytes) -> str:
    with ZipFile(BytesIO(raw)) as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_pattern = re.compile(r"xl/worksheets/sheet(\d+)\.xml$")
        sheet_entries: list[tuple[int, str]] = []
        for name in archive.namelist():
            match = sheet_pattern.match(name)
            if match:
                sheet_entries.append((int(match.group(1)), name))

        sheets = []
        for sheet_number, sheet_name in sorted(sheet_entries):
            rows = _extract_sheet_rows(archive.read(sheet_name), shared_strings)
            if rows:
                sheet_text = "\n".join("\t".join(row).rstrip() for row in rows)
                sheets.append(f"[Sheet {sheet_number}]\n{sheet_text}")
    return "\n\n".join(sheets).strip()


def _load_shared_strings(archive: ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    values = []
    for item in root.iter():
        if _local_name(item.tag) != "si":
            continue
        chunks = []
        for child in item.iter():
            if _local_name(child.tag) == "t" and child.text:
                chunks.append(child.text)
        values.append("".join(chunks))
    return values


def _extract_sheet_rows(sheet_xml: bytes, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(sheet_xml)
    rows: list[list[str]] = []
    for row in root.iter():
        if _local_name(row.tag) != "row":
            continue
        values: list[str] = []
        for cell in row:
            if _local_name(cell.tag) != "c":
                continue
            ref = cell.attrib.get("r", "")
            column_index = _column_index(ref)
            while len(values) < column_index:
                values.append("")
            values.append(_extract_cell_value(cell, shared_strings))
        while values and values[-1] == "":
            values.pop()
        if values:
            rows.append(values)
    return rows


def _extract_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        text = []
        for child in cell.iter():
            if _local_name(child.tag) == "t" and child.text:
                text.append(child.text)
        return "".join(text)

    value = ""
    for child in cell:
        if _local_name(child.tag) == "v" and child.text:
            value = child.text
            break

    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError):
            return value
    return value


def _column_index(cell_ref: str) -> int:
    column = ""
    for char in cell_ref:
        if char.isalpha():
            column += char.upper()
        else:
            break
    if not column:
        return 0

    index = 0
    for char in column:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]
