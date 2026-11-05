import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.split_worker import split


def fixture(path):
    with pymupdf.open() as document:
        for index in range(4):
            page = document.new_page()
            page.insert_text((72, 72), f"Page {index + 1} searchable text")
            if index == 1:
                page.add_text_annot((100, 100), "Keep this annotation")
                page.set_rotation(90)
        document.save(path)


def test_selected_pages_preserve_order_rotation_text_and_annotations(tmp_path):
    source, output = tmp_path / "in.pdf", tmp_path / "out.pdf"
    fixture(source)
    split(str(source), str(output), 300, 2, 3)
    with pymupdf.open(output) as document:
        assert document.page_count == 2
        assert "Page 2 searchable text" in document[0].get_text()
        assert "Page 3 searchable text" in document[1].get_text()
        assert document[0].rotation == 90
        assert len(list(document[0].annots())) == 1
    with pymupdf.open(source) as document:
        assert document.page_count == 4


@pytest.mark.parametrize("first,last", [(0, 1), (3, 2), (1, 5)])
def test_invalid_ranges_are_rejected(tmp_path, first, last):
    source, output = tmp_path / "in.pdf", tmp_path / "out.pdf"
    fixture(source)
    with pytest.raises(ValueError):
        split(str(source), str(output), 300, first, last)
    assert not output.exists()


def test_split_endpoint(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    source = tmp_path / "in.pdf"
    fixture(source)
    with TestClient(app) as client:
        response = client.post("/api/pdf/split?from=2&to=3", files={"file": ("test.pdf", source.read_bytes())})
        assert response.status_code == 200, response.text
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            assert document.page_count == 2
        assert client.post("/api/pdf/split?from=0&to=1").status_code == 400
        assert client.post("/api/pdf/split?from=x&to=1").status_code == 400
        assert client.post("/api/pdf/split?from=1&to=99", files={"file": ("test.pdf", source.read_bytes())}).status_code == 422
