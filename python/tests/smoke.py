"""Opt-in integration check using existing Gotenberg on 3005. No services left running."""
import io
import os
import subprocess
import time
import zipfile
from pathlib import Path

import fitz
import httpx
from docx import Document


def main():
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHON_SERVICE_TOKEN": "smoke-test-token"}
    processes = []
    try:
        processes.append(subprocess.Popen(
            [str(root / "python/.venv/bin/python"), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18010"],
            cwd=root / "python", env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
        api_env = {**environment, "PORT": "15010", "HOST": "127.0.0.1", "PYTHON_SERVICE_URL": "http://127.0.0.1:18010", "GOTENBERG_URL": "http://127.0.0.1:3005"}
        processes.append(subprocess.Popen(["bun", "src/index.ts"], cwd=root / "api", env=api_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        with httpx.Client(timeout=115, trust_env=False) as client:
            for _ in range(80):
                try:
                    health = client.get("http://127.0.0.1:15010/health")
                    if health.status_code == 200:
                        print(f"Health: {health.json()}", flush=True)
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Local services or existing Gotenberg are not healthy")
            document = Document()
            document.add_paragraph("Word to PDF independent backend smoke test")
            stream = io.BytesIO()
            document.save(stream)
            response = client.post("http://127.0.0.1:15010/api/v1/convert/word", files={"files": ("test.docx", stream.getvalue())})
            assert response.status_code == 200, response.text
            with fitz.open(stream=response.content, filetype="pdf") as pdf:
                assert "independent backend" in pdf[0].get_text()
            print("PASS: DOCX → Bun API → existing Gotenberg:3005 → valid PDF", flush=True)
            with fitz.open() as document:
                page = document.new_page()
                page.insert_text((72, 72), "PDF to Word editable backend smoke test")
                page.draw_rect(fitz.Rect(70, 100, 200, 150))
                source = document.tobytes()
            response = client.post("http://127.0.0.1:15010/api/v1/convert/pdf-to-word", files={"files": ("test.pdf", source)})
            assert response.status_code == 200, response.text
            with zipfile.ZipFile(io.BytesIO(response.content)) as docx:
                assert "editable backend smoke test" in docx.read("word/document.xml").decode()
            print("PASS: PDF → Bun API → Python → editable DOCX", flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
