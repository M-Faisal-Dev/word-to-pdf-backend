"""Extract one inclusive, 1-based page range without rasterizing it."""
import sys

import pymupdf


def split(source: str, destination: str, max_pages: int, first: int, last: int) -> None:
    with pymupdf.open(source) as document:
        if document.needs_pass or not 0 < document.page_count <= max_pages:
            raise ValueError("Unsupported PDF")
        if not 1 <= first <= last <= document.page_count:
            raise ValueError("Invalid page range")
        with pymupdf.open() as output:
            output.insert_pdf(document, from_page=first - 1, to_page=last - 1, links=True, annots=True, widgets=True)
            output.save(destination, garbage=4, deflate=True)
    with pymupdf.open(destination) as output:
        if output.page_count != last - first + 1:
            raise ValueError("Unexpected page count")


if __name__ == "__main__":
    try:
        split(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    except Exception:
        sys.exit(2)
