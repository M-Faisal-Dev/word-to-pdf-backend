import asyncio
import hmac
import json
import os
import shutil
import sys
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile

MAX_BYTES = 30 * 1024 * 1024
TIMEOUT = int(os.getenv("PYTHON_TIMEOUT_SECONDS", "90"))
MAX_PAGES = int(os.getenv("MAX_PDF_PAGES", "300"))
MAX_ACTIVE = int(os.getenv("PYTHON_MAX_ACTIVE", "1"))
if min(TIMEOUT, MAX_PAGES, MAX_ACTIVE) < 1:
    raise ValueError("Conversion limits must be positive")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.active = 0
    yield


class BodyLimitMiddleware:
    """Bound chunked bodies too, before multipart parsing spools them to disk."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        size = 0

        async def bounded_receive():
            nonlocal size
            message = await receive()
            size += len(message.get("body", b""))
            if size > MAX_BYTES + 65536:
                raise HTTPException(413, "Maximum input size is 30 MB")
            return message

        await self.app(scope, bounded_receive, send)


app = FastAPI(title="Word-to-PDF internal converter", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(BodyLimitMiddleware)


@app.get("/health")
async def health():
    return {"status": "up"}


@app.post("/api/convert/pdf-to-word")
async def pdf_to_word(request: Request):
    return await process_pdf(request)


@app.post("/api/pdf/compress")
async def compress_pdf(request: Request):
    return await process_pdf(request, compress=True)


@app.post("/api/pdf/split")
async def split_pdf(request: Request):
    return await process_pdf(request, split=True)


@app.post("/api/pdf/rotate")
async def rotate_pdf(request: Request):
    return await process_pdf(request, rotate=True)


@app.post("/api/pdf/lock")
async def lock_pdf(request: Request):
    return await process_pdf(request, security="lock")


@app.post("/api/pdf/unlock")
async def unlock_pdf(request: Request):
    return await process_pdf(request, security="unlock")


async def process_pdf(request: Request, compress=False, split=False, rotate=False, security=None):
    password = None
    pages = request.query_params.get("pages", "all")
    angle = 90
    if rotate:
        try:
            angle = int(request.query_params.get("angle", "90"))
            if angle not in {90, 180, 270} or len(pages) > 1200:
                raise ValueError("Invalid rotation")
            if pages != "all":
                if not pages or not all(page.isdecimal() and 1 <= int(page) <= MAX_PAGES for page in pages.split(",")):
                    raise ValueError("Invalid pages")
        except ValueError:
            raise HTTPException(400, "Invalid pages or rotation") from None
    first, last = 0, 0
    if split:
        try:
            first, last = int(request.query_params.get("from", "0")), int(request.query_params.get("to", "0"))
        except ValueError:
            raise HTTPException(400, "Invalid page range") from None
        if not 1 <= first <= last <= MAX_PAGES:
            raise HTTPException(400, "Invalid page range")
    level = request.query_params.get("level", "balanced")
    if compress and level not in {"lossless", "balanced", "strong"}:
        raise HTTPException(400, "Invalid compression level")
    token = os.getenv("PYTHON_SERVICE_TOKEN", "")
    if token and not hmac.compare_digest(request.headers.get("x-service-token", ""), token):
        raise HTTPException(401, "Invalid service token")
    if app.state.active >= MAX_ACTIVE:
        raise HTTPException(503, "Converter busy")
    app.state.active += 1
    directory = None
    process = None
    keep_output = False
    try:
        if int(request.headers.get("content-length", "0")) > MAX_BYTES + 65536:
            raise HTTPException(413, "Maximum input size is 30 MB")
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            raise HTTPException(415, "Expected multipart/form-data")
        async with request.form(max_files=1, max_fields=1 if security else 0, max_part_size=MAX_BYTES) as form:
            entries = list(form.multi_items())
            uploads = form.getlist("file")
            if len(uploads) != 1 or not isinstance(uploads[0], UploadFile) or len(entries) != (2 if security else 1):
                raise HTTPException(400, "Upload exactly one PDF and the required fields")
            if security:
                supplied = form.getlist("password")
                if len(supplied) != 1 or not isinstance(supplied[0], str) or "password" in request.query_params:
                    raise HTTPException(400, "Send one password in the form body")
                password = supplied[0]
                size = len(password.encode("utf-8"))
                if "\0" in password or (not 8 <= size <= 40 if security == "lock" else not 1 <= size <= 127):
                    raise HTTPException(400, "Invalid password length")
            upload = uploads[0]
            if not (upload.filename or "").lower().endswith(".pdf"):
                raise HTTPException(415, "Only PDF files are supported")
            directory = Path(tempfile.mkdtemp(prefix="pdf-to-word-"))
            source, output = directory / "input.pdf", directory / ("output.pdf" if compress or split or rotate or security else "output.docx")
            size = 0
            with source.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, "Maximum input size is 30 MB")
                    destination.write(chunk)
            with source.open("rb") as handle:
                if size < 8 or handle.read(5) != b"%PDF-":
                    raise HTTPException(415, "Invalid PDF")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.security_worker" if security else "app.rotate_worker" if rotate else "app.split_worker" if split else "app.compress_worker" if compress else "app.worker", str(source), str(output), str(MAX_PAGES), *([security] if security else [str(angle), pages] if rotate else [str(first), str(last)] if split else [level] if compress else []),
            stdin=asyncio.subprocess.PIPE if security else None,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.communicate(json.dumps({"password": password}).encode()) if security else process.wait(), timeout=TIMEOUT)
        except TimeoutError:
            raise HTTPException(504, "Conversion timed out") from None
        if security and process.returncode == 3:
            raise HTTPException(403, "Incorrect PDF password")
        if security and process.returncode in {4, 5}:
            raise HTTPException(409, "PDF already protected" if security == "lock" else "PDF is not protected")
        if process.returncode or not output.exists():
            raise HTTPException(422, "Cannot process this PDF. It may be damaged, password protected, unsupported, or exceed the page limit.")
        if not 0 < output.stat().st_size <= MAX_BYTES:
            raise HTTPException(422, "Converted document exceeds 30 MB or is empty")
        if not compress and not split and not rotate and not security:
            with zipfile.ZipFile(output) as document:
                if "word/document.xml" not in document.namelist():
                    raise HTTPException(502, "Invalid Word document")
        keep_output = True
        return FileResponse(
            output, filename=f"{security}ed.pdf" if security else "rotated.pdf" if rotate else "split.pdf" if split else "compressed.pdf" if compress else "converted.docx",
            media_type="application/pdf" if compress or split or rotate or security else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    except HTTPException:
        raise
    except Exception:
        return JSONResponse({"detail": "Conversion failed"}, status_code=422)
    finally:
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        if directory and not keep_output:
            shutil.rmtree(directory, ignore_errors=True)
        app.state.active -= 1
