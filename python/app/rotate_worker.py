"""Apply relative clockwise rotation while retaining all PDF pages and content."""
import sys

import pymupdf


def rotate(source: str, destination: str, max_pages: int, angle: int, pages: str) -> None:
    if angle not in {90, 180, 270}:
        raise ValueError("Invalid angle")
    with pymupdf.open(source) as document:
        if document.needs_pass or not 0 < document.page_count <= max_pages:
            raise ValueError("Unsupported PDF")
        if pages == "all":
            selected = set(range(1, document.page_count + 1))
        else:
            values = pages.split(",")
            if not values or not all(value.isdecimal() for value in values):
                raise ValueError("Invalid page selection")
            selected = {int(value) for value in values}
            if not selected or min(selected) < 1 or max(selected) > document.page_count:
                raise ValueError("Page out of range")
        count = document.page_count
        for number in selected:
            page = document[number - 1]
            page.set_rotation((page.rotation + angle) % 360)
        document.save(destination, garbage=4, deflate=True)
    with pymupdf.open(destination) as output:
        if output.page_count != count:
            raise ValueError("Page count changed")


if __name__ == "__main__":
    try:
        rotate(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    except Exception:
        sys.exit(2)
