"""Runs in an isolated process so timeout kills the converter, not just its waiter."""
import sys

import fitz
from pdf2docx import Converter


def convert(source: str, destination: str, max_pages: int) -> None:
    with fitz.open(source) as document:
        if document.needs_pass:
            raise ValueError("Password-protected PDF")
        if not 0 < len(document) <= max_pages:
            raise ValueError("PDF page limit exceeded or empty PDF")
        if not any(page.get_text().strip() for page in document):
            raise ValueError("Scanned PDF requires OCR, which is not supported")
    # Keep editable text: do not rasterize entire pages containing vector drawings.
    converter = Converter(source)
    try:
        converter.convert(destination, start=0, end=None, multi_processing=False)
    finally:
        converter.close()


if __name__ == "__main__":
    try:
        convert(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    except Exception:
        # Do not expose document contents or internal paths through logs/responses.
        sys.exit(2)
