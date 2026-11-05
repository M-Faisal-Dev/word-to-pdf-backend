import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security_worker import AlreadyProtected, NotProtected, PasswordError, secure

PASSWORD = "test-password-123"


def fixture(path):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Secret searchable document")
        page.add_text_annot((100, 100), "Keep annotation")
        document.save(path)


def test_lock_unlock_roundtrip_keeps_original_and_content(tmp_path):
    source, locked, unlocked = [tmp_path / name for name in ["source.pdf", "locked.pdf", "unlocked.pdf"]]
    fixture(source)
    original = source.read_bytes()
    secure(str(source), str(locked), 300, "lock", PASSWORD)
    with pymupdf.open(locked) as document:
        assert document.needs_pass
        assert not document.authenticate("wrong")
        assert document.authenticate(PASSWORD)
        assert "AES" in document.metadata["encryption"]
        assert "256" in document.metadata["encryption"]
    with pytest.raises(PasswordError):
        secure(str(locked), str(unlocked), 300, "unlock", "wrong")
    assert not unlocked.exists()
    secure(str(locked), str(unlocked), 300, "unlock", PASSWORD)
    with pymupdf.open(unlocked) as document:
        assert not document.needs_pass
        assert not document.metadata.get("encryption")
        assert document.page_count == 1
        assert "Secret searchable document" in document[0].get_text()
        assert len(list(document[0].annots())) == 1
    assert source.read_bytes() == original


def test_invalid_passwords_and_already_protected(tmp_path):
    source, output = tmp_path / "in.pdf", tmp_path / "out.pdf"
    fixture(source)
    for password in ["short", "x" * 41, "\0" * 8]:
        with pytest.raises(ValueError):
            secure(str(source), str(output), 300, "lock", password)
    with pytest.raises(NotProtected):
        secure(str(source), str(output), 300, "unlock", PASSWORD)
    secure(str(source), str(output), 300, "lock", PASSWORD)
    with pytest.raises(AlreadyProtected):
        secure(str(output), str(tmp_path / "again.pdf"), 300, "lock", PASSWORD)


def test_endpoints_lock_and_unlock_password_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHON_SERVICE_TOKEN", "internal-test")
    source = tmp_path / "source.pdf"
    fixture(source)
    headers = {"X-Service-Token": "internal-test"}
    with TestClient(app) as client:
        assert client.post("/api/pdf/lock").status_code == 401
        response = client.post("/api/pdf/lock", files={"file": ("source.pdf", source.read_bytes())}, data={"password": PASSWORD}, headers=headers)
        assert response.status_code == 200, response.text
        locked = response.content
        with pymupdf.open(stream=locked, filetype="pdf") as document:
            assert document.needs_pass
        bad = client.post("/api/pdf/unlock", files={"file": ("locked.pdf", locked)}, data={"password": "wrong"}, headers=headers)
        assert bad.status_code == 403
        assert "wrong" not in bad.text
        response = client.post("/api/pdf/unlock", files={"file": ("locked.pdf", locked)}, data={"password": PASSWORD}, headers=headers)
        assert response.status_code == 200, response.text
        with pymupdf.open(stream=response.content, filetype="pdf") as document:
            assert not document.needs_pass
            assert "Secret searchable document" in document[0].get_text()
        assert client.post("/api/pdf/unlock", files={"file": ("plain.pdf", source.read_bytes())}, data={"password": PASSWORD}, headers=headers).status_code == 409
        assert client.post("/api/pdf/lock", files={"file": ("locked.pdf", locked)}, data={"password": PASSWORD}, headers=headers).status_code == 409
        assert client.post("/api/pdf/lock", files={"file": ("plain.pdf", source.read_bytes())}, data={"password": "short"}, headers=headers).status_code == 400
