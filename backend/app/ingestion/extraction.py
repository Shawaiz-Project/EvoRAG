import csv
import re
from collections.abc import Iterable, Iterator, Sequence
from io import BytesIO, StringIO
from pathlib import PurePosixPath
from typing import Protocol
from zipfile import BadZipFile, ZipFile

from docx import Document as open_docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pypdf import PdfReader

from app.ingestion.chunking import Block

SUPPORTED_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_EXTRACTED_CHARS = 2_000_000
MAX_BLOCKS = 20_000
MAX_TABLE_COLUMNS = 256
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class InvalidDocument(ValueError):
    pass


class PageOCR(Protocol):
    def extract_page(self, pdf: bytes, page: int) -> str: ...


def decode_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise InvalidDocument("Text input must be UTF-8") from error
    if CONTROL_CHARACTERS.search(text):
        raise InvalidDocument("Text input contains binary or control characters")
    return text


def filename_and_mime(filename: str) -> tuple[str, str]:
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 255 or CONTROL_CHARACTERS.search(name) or not name.isprintable():
        raise InvalidDocument("Invalid file name")
    mime = SUPPORTED_MIME.get(PurePosixPath(name).suffix.lower())
    if mime is None:
        raise InvalidDocument("Unsupported file extension")
    return name, mime


def validate_upload(filename: str, body: bytes, max_bytes: int) -> tuple[str, str]:
    name, mime = filename_and_mime(filename)
    if not body or len(body) > max_bytes:
        raise InvalidDocument("File is empty or exceeds the file size limit")
    extension = PurePosixPath(name).suffix.lower()
    if extension == ".pdf" and not body.startswith(b"%PDF-"):
        raise InvalidDocument("PDF header is missing")
    if extension in (".docx", ".xlsx"):
        required = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
        try:
            with ZipFile(BytesIO(body)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 3000
                    or sum(entry.file_size for entry in entries) > min(max_bytes * 5, 100_000_000)
                    or any(entry.flag_bits & 1 for entry in entries)
                    or required not in archive.namelist()
                    or "[Content_Types].xml" not in archive.namelist()
                ):
                    raise InvalidDocument("Invalid or oversized Office archive")
        except BadZipFile as error:
            raise InvalidDocument("Invalid Office archive") from error
    return name, mime


def bounded_blocks(blocks: Iterable[Block]) -> list[Block]:
    result: list[Block] = []
    length = 0
    for block in blocks:
        if not block.text.strip():
            continue
        length += len(block.text)
        if length > MAX_EXTRACTED_CHARS or len(result) >= MAX_BLOCKS:
            raise InvalidDocument("Extracted text exceeds the processing limit")
        result.append(block)
    if not result:
        raise InvalidDocument("No text was found in this input")
    return result


def table_rows(rows: Iterable[Sequence[object]], section: str) -> Iterator[Block]:
    iterator = iter(rows)
    first = next(iterator, None)
    if not first:
        return
    header = [str(value).strip() if value is not None else "" for value in first]
    if len(header) > MAX_TABLE_COLUMNS:
        raise InvalidDocument("Table has too many columns")
    yield Block(" | ".join(header), section=section, kind="table", attributes={"row": 1})
    for index, row in enumerate(iterator, 2):
        if len(row) > MAX_TABLE_COLUMNS:
            raise InvalidDocument("Table has too many columns")
        cells = [str(value).strip() if value is not None else "" for value in row]
        if not any(cells):
            continue
        labels = [
            (header[column] if column < len(header) else "") or f"Column {column + 1}"
            for column in range(len(cells))
        ]
        pairs = [f"{label}: {value}" for label, value in zip(labels, cells, strict=True)]
        yield Block(" | ".join(pairs), section=section, kind="table", attributes={"row": index})


def markdown_blocks(text: str) -> Iterator[Block]:
    section = None
    pending: list[str] = []
    for line in text.splitlines():
        heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*#*", line)
        if heading:
            yield Block("\n".join(pending), section=section)
            section = heading[1]
            pending = [section]
        else:
            pending.append(line)
    yield Block("\n".join(pending), section=section)


def pdf_blocks(data: bytes, ocr: PageOCR | None) -> Iterator[Block]:
    document = PdfReader(BytesIO(data))
    if document.is_encrypted:
        raise InvalidDocument("Encrypted PDFs are unsupported")
    if len(document.pages) > 500:
        raise InvalidDocument("PDF exceeds the page limit")
    for number, page in enumerate(document.pages, 1):
        content = page.extract_text(extraction_mode="layout").strip()
        origin = "text"
        if not content and ocr is not None:
            content = ocr.extract_page(data, number)
            origin = "ocr"
        yield Block(content, page=number, attributes={"extraction": origin})


def docx_blocks(data: bytes) -> Iterator[Block]:
    document = open_docx(BytesIO(data))
    section = None
    table_number = 0
    pending: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            style = item.style
            if style and style.name.startswith("Heading"):
                yield Block("\n".join(pending), section=section)
                section = item.text.strip()
                pending = []
            pending.append(item.text)
        elif isinstance(item, Table):
            yield Block("\n".join(pending), section=section)
            pending = []
            table_number += 1
            label = f"{section} / Table {table_number}" if section else f"Table {table_number}"
            yield from table_rows(([cell.text for cell in row.cells] for row in item.rows), label)
    yield Block("\n".join(pending), section=section)


def spreadsheet_blocks(data: bytes) -> Iterator[Block]:
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        for worksheet in workbook.worksheets:
            if (worksheet.max_row or 0) > MAX_BLOCKS:
                raise InvalidDocument("Spreadsheet exceeds the row limit")
            if (worksheet.max_column or 0) > MAX_TABLE_COLUMNS:
                raise InvalidDocument("Spreadsheet has too many columns")
            yield from table_rows(worksheet.values, worksheet.title)
    finally:
        workbook.close()


def extract_file(
    filename: str, data: bytes, max_bytes: int, *, ocr: PageOCR | None = None
) -> list[Block]:
    name, _ = validate_upload(filename, data, max_bytes)
    match PurePosixPath(name).suffix.lower():
        case ".pdf":
            blocks = pdf_blocks(data, ocr)
        case ".docx":
            blocks = docx_blocks(data)
        case ".xlsx":
            blocks = spreadsheet_blocks(data)
        case ".md" | ".markdown":
            blocks = markdown_blocks(decode_text(data))
        case ".csv":
            rows = csv.reader(StringIO(decode_text(data), newline=""), strict=True)
            blocks = table_rows(rows, "CSV")
        case _:
            blocks = iter([Block(decode_text(data))])
    result = bounded_blocks(blocks)
    if any("\x00" in block.text for block in result):
        raise InvalidDocument("Extracted text contains null characters")
    return result
