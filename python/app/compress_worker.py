"""Compress PDFs without rasterizing pages or removing searchable text."""
import shutil
import sys
from pathlib import Path

import pymupdf


def compress(source: str, destination: str, max_pages: int, level: str) -> None:
    if level not in {"lossless", "balanced", "strong"}:
        raise ValueError("Invalid level")
    with pymupdf.open(source) as document:
        if document.needs_pass or not 0 < document.page_count <= max_pages:
            raise ValueError("Unsupported PDF")
        page_count = document.page_count
        if level != "lossless":
            dpi, quality = (150, 75) if level == "balanced" else (96, 55)
            document.rewrite_images(dpi_threshold=dpi + 30, dpi_target=dpi, quality=quality)
        document.save(destination, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
    with pymupdf.open(destination) as check:
        if check.page_count != page_count:
            raise ValueError("Page count changed")
    # Already optimized files must never get larger; preserve original bytes instead.
    if Path(destination).stat().st_size >= Path(source).stat().st_size:
        shutil.copyfile(source, destination)


if __name__ == "__main__":
    try:
        compress(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])
    except Exception:
        sys.exit(2)
