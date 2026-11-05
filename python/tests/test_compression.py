import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.compress_worker import compress
from app.main import app


def source_pdf(path):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Searchable text must survive compression")
        samples = bytes(range(256)) * (1200 * 1200 * 3 // 256 + 1)
        image = pymupdf.Pixmap(pymupdf.csRGB, 1200, 1200, samples[:1200 * 1200 * 3], False)
        page.insert_image(pymupdf.Rect(72, 100, 360, 388), pixmap=image)
        document.new_page().insert_text((72, 72), "Second page")
        document.save(path, deflate=False)


@pytest.mark.parametrize("level", ["lossless", "balanced", "strong"])
def test_compress_preserves_text_pages_and_does_not_grow(tmp_path, level):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    source_pdf(source)
    compress(str(source), str(output), 300, level)
    assert output.stat().st_size < source.stat().st_size
    with pymupdf.open(output) as document:
        assert document.page_count == 2
        assert "Searchable text must survive compression" in document[0].get_text()
        assert "Second page" in document[1].get_text()


def test_compression_endpoint_and_invalid_level(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    source = tmp_path / "test.pdf"
    source_pdf(source)
    with TestClient(app) as client:
        response = client.post("/api/pdf/compress?level=balanced", files={"file": ("test.pdf", source.read_bytes())})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/pdf"
        assert len(response.content) < source.stat().st_size
        assert client.post("/api/pdf/compress?level=invalid").status_code == 400


def test_already_optimized_never_grows_and_encrypted_rejected(tmp_path):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Small document")
        document.save(source, garbage=4, deflate=True, use_objstms=1)
    compress(str(source), str(output), 300, "lossless")
    assert output.stat().st_size <= source.stat().st_size
    with pymupdf.open(source) as document:
        encrypted = tmp_path / "encrypted.pdf"
        document.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
    with pytest.raises(ValueError):
        compress(str(encrypted), str(output), 300, "balanced")
