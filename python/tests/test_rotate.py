import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rotate_worker import rotate


def fixture(path):
    with pymupdf.open() as document:
        for index in range(3):
            page = document.new_page()
            page.insert_text((72, 72), f"Page {index + 1} searchable text")
            if index == 1:
                page.set_rotation(90)
                page.add_text_annot((100, 100), "Keep annotation")
        document.save(path)


@pytest.mark.parametrize("angle,pages,expected", [(90, "1,3", [90, 90, 90]), (180, "all", [180, 270, 180]), (270, "2,2", [0, 0, 0])])
def test_relative_rotation_preserves_other_pages_and_text(tmp_path, angle, pages, expected):
    source, output = tmp_path / "input.pdf", tmp_path / "output.pdf"
    fixture(source)
    rotate(str(source), str(output), 300, angle, pages)
    with pymupdf.open(output) as document:
        assert document.page_count == 3
        assert [page.rotation for page in document] == expected
        for index, page in enumerate(document):
            assert f"Page {index + 1} searchable text" in page.get_text()
        assert len(list(document[1].annots())) == 1
    with pymupdf.open(source) as document:
        assert [page.rotation for page in document] == [0, 90, 0]


@pytest.mark.parametrize("angle,pages", [(45, "all"), (90, ""), (90, "0"), (90, "4"), (90, "1,,2")])
def test_invalid_rotation_or_pages_rejected(tmp_path, angle, pages):
    source, output = tmp_path / "input.pdf", tmp_path / "output.pdf"
    fixture(source)
    with pytest.raises(ValueError):
        rotate(str(source), str(output), 300, angle, pages)
    assert not output.exists()


def test_rotation_endpoint(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    source = tmp_path / "input.pdf"
    fixture(source)
    with TestClient(app) as client:
        response = client.post("/api/pdf/rotate?angle=90&pages=1,3", files={"file": ("test.pdf", source.read_bytes())})
        assert response.status_code == 200, response.text
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            assert [page.rotation for page in document] == [90, 90, 90]
        assert client.post("/api/pdf/rotate?angle=45").status_code == 400
        assert client.post("/api/pdf/rotate?pages=0").status_code == 400
        assert client.post("/api/pdf/rotate?pages=99", files={"file": ("test.pdf", source.read_bytes())}).status_code == 422
