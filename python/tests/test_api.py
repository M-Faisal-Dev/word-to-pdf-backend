import io
import zipfile

import fitz
from fastapi.testclient import TestClient

from app.main import app


def pdf_bytes(text=True, vector=False):
    with fitz.open() as doc:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), "Independent backend editable text")
        if vector:
            page.draw_rect(fitz.Rect(60, 100, 200, 150))
        return doc.tobytes()


def test_editable_docx_with_vectors_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "test-token")
    monkeypatch.setattr("app.main.tempfile.mkdtemp", lambda **kwargs: str(tmp_path / "job"))
    (tmp_path / "job").mkdir()
    with TestClient(app) as client:
        response = client.post("/api/convert/pdf-to-word", files={"file": ("../../unsafe.pdf", pdf_bytes(vector=True), "application/pdf")}, headers={"X-Service-Token": "test-token"})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as docx:
        assert "Independent backend editable text" in docx.read("word/document.xml").decode()
    assert not (tmp_path / "job").exists()


def test_invalid_upload_auth_and_health(monkeypatch):
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "test-token")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/convert/pdf-to-word").status_code == 401
        headers = {"X-Service-Token": "test-token"}
        assert client.post("/api/convert/pdf-to-word", files={"file": ("a.pdf", b"invalid")}, headers=headers).status_code == 415
        assert client.post("/api/convert/pdf-to-word", files={"wrong": ("a.pdf", pdf_bytes())}, headers=headers).status_code == 400
        assert client.post("/api/convert/pdf-to-word", files={"file": ("a.pdf", pdf_bytes(text=False))}, headers=headers).status_code == 422


def test_page_limit_and_capacity(monkeypatch):
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr("app.main.MAX_PAGES", 1)
    with fitz.open() as doc:
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "test")
        content = doc.tobytes()
    with TestClient(app) as client:
        assert client.post("/api/convert/pdf-to-word", files={"file": ("a.pdf", content)}).status_code == 422
        app.state.active = 1
        assert client.post("/api/convert/pdf-to-word", files={"file": ("a.pdf", content)}).status_code == 503
        app.state.active = 0


def test_timeout_kills_process_and_cleans_up(monkeypatch, tmp_path):
    monkeypatch.delenv("PYTHON_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr("app.main.TIMEOUT", 0.001)
    monkeypatch.setattr("app.main.tempfile.mkdtemp", lambda **kwargs: str(tmp_path / "job"))
    (tmp_path / "job").mkdir()
    with TestClient(app) as client:
        response = client.post("/api/convert/pdf-to-word", files={"file": ("a.pdf", pdf_bytes())})
        assert response.status_code == 504
        assert app.state.active == 0
    assert not (tmp_path / "job").exists()
