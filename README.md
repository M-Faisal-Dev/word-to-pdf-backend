# Word-to-PDF backend

This standalone backend belongs only to the Word-to-PDF app and lives beside the mobile project in `word-to-pdf-backend/`. It does not call WePDF or all-doc-reader-backend. PDF-to-Word and PDF tools use this API; Word-to-PDF currently uses the separately configured conversion API.

- Bun/TypeScript public API: `127.0.0.1:5010`.
- Python/FastAPI internal converter: `127.0.0.1:8010`.
- Existing Gotenberg: `http://127.0.0.1:3005` on the host. No new Gotenberg container is created or configured.

## API contract

Both public endpoints take **one** multipart file in the **`files`** field:

| Endpoint | Input | Response |
| --- | --- | --- |
| `POST /api/v1/convert/word` | `.doc` or `.docx` | PDF binary |
| `POST /api/v1/convert/pdf-to-word` | `.pdf` | DOCX binary |
| `POST /api/v1/pdf/compress?level=balanced` | `.pdf` | Compressed PDF binary |
| `GET /health` | none | API + Gotenberg + Python health; HTTP 503 when an upstream is down |

```sh
curl --fail-with-body -F 'files=@sample.docx' http://127.0.0.1:5010/api/v1/convert/word -o converted.pdf
curl --fail-with-body -F 'files=@sample.pdf' http://127.0.0.1:5010/api/v1/convert/pdf-to-word -o converted.docx
curl http://127.0.0.1:5010/health
```

Python's internal endpoint is `POST /api/convert/pdf-to-word`, uses the `file` field, and accepts `X-Service-Token`. The public API handles the translation; mobile clients must never contain this internal token.

PDF-to-Word uses `pdf2docx` and PyMuPDF. Text is kept editable, including when a page has vector graphics: there is no whole-page rasterization. Scanned-only PDFs, encrypted PDFs, and PDFs over 300 pages are rejected. Mixed PDFs can contain image-only pages; no OCR is performed. Complex layouts can change during conversion.

## Local development

Requires Bun, Python 3.12 or 3.13, uv, and the existing Gotenberg on port 3005.

First create `api/.env` from `api/.env.example` and set a shared `PYTHON_SERVICE_TOKEN`. Do not overwrite an existing `.env`.

Terminal 1, from `word-to-pdf-backend/python`:

```sh
uv sync --python 3.12
uv run --env-file ../api/.env uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
```

Terminal 2, from `word-to-pdf-backend/api`:

```sh
bun install --frozen-lockfile
# Create .env from .env.example only if it does not already exist
bun run dev
```

Bun automatically loads `.env`. Keep the token identical in both services. Local Python permits no token only when its environment variable is unset, for local testing. Set a long shared token for deployment.

## Server deployment

Run Bun and Python directly on the host using a process manager such as systemd. Use the commands above without Bun's `--watch` (`bun run start`) and keep Python at one worker. Set the same `PYTHON_SERVICE_TOKEN` in both service environments. Run the processes as an unprivileged user with a writable temporary directory.

Bun connects to the already running Gotenberg container through `http://127.0.0.1:3005`; no additional Docker or Compose setup is needed. Keep Python bound to `127.0.0.1:8010` and put an HTTPS reverse proxy in front of the Bun API at `127.0.0.1:5010`.

Set `CORS_ORIGINS` to comma-separated permitted web origins. Native mobile requests do not require an Origin header. CORS is not user authentication.

`TRUST_PROXY=0` uses the socket IP for rate limiting. Behind a reverse proxy, use `TRUST_PROXY=1` only when the proxy overwrites `X-Real-IP` and clients cannot access the API directly. With the default disabled, proxy traffic shares a rate bucket. The rate limiter is per-process memory; run one API instance or add a shared limiter before scaling out.

## Limits and failure behavior

- Input and output maximum: 30 MiB per file. Bun limits the complete HTTP body to 30 MiB + 64 KiB multipart overhead. Python bounds streamed bodies as well as saved upload bytes.
- Default: 20 conversion requests per client per minute; 2 active public conversions; 1 active Python conversion. Excess requests return 429/503, not an unbounded queue.
- Python timeout: 90 seconds. Bun upstream timeout: 110 seconds. A reverse proxy must allow at least 120 seconds and a body of at least 31 MiB.
- Python conversion runs in a separate process. Timeouts kill and reap it. A Node client cancellation aborts its upstream request; Python work may continue until its own timeout, after which it is cleaned up.
- Each Python job gets a unique temporary directory. Source files are removed on failure and output directories after the response is sent. No user-provided filenames are used as filesystem paths.
- Runtime health indicates reachability, not a full conversion benchmark.
- Configure process memory limits in your host process manager for production. No document content is logged by the converter worker.

## Verification

```sh
# from word-to-pdf-backend/api
bun run typecheck
bun test

# from word-to-pdf-backend/python
uv run ruff check app tests
uv run python -m pytest -q
```

Opt-in integration check, with host Gotenberg already running:

```sh
# from word-to-pdf-backend/python
uv run python tests/smoke.py
```

This starts temporary API/Python services on 15010/18010, creates synthetic DOCX/PDF fixtures, checks real Word-to-PDF and editable PDF-to-Word outputs, and stops both temporary services. It does not upload user files or modify Gotenberg.

## PDF compression

`POST /api/v1/pdf/compress?level=lossless|balanced|strong` takes one `files` upload and forwards it to the internal Python `/api/pdf/compress` endpoint. Existing upload limits, rate limits, token authentication, subprocess timeout and temporary-file cleanup also apply.

- `lossless`: structural cleanup/deflate; images are not resampled.
- `balanced` (default): images above 180 DPI are resampled to 150 DPI; JPEG quality 75.
- `strong`: images above 126 DPI are resampled to 96 DPI; JPEG quality 55.

PyMuPDF keeps page content and searchable text instead of rasterizing pages. Up to 30 MB and 300 pages; encrypted PDFs are rejected. Already optimized PDFs may not shrink: if the generated result is larger, the original bytes are returned. Compression rewrites the document and does not preserve digital-signature validity.

## PDF splitting

`POST /api/v1/pdf/split?from=2&to=5` takes one PDF in the public `files` field and returns one PDF containing the inclusive, 1-based page range. It forwards to internal `/api/pdf/split` with the `file` field and service token. The subprocess shares the upload/output size, concurrency, timeout, and temporary cleanup safeguards of the other tools.

Ranges must be ordered and within the source document's 1–300 pages. Pages are copied without rasterization, preserving text, rotation, annotations and widgets; internal links outside the extracted range are excluded. The original file is never overwritten. Multiple outputs are separate API requests and count toward the shared 20-request/minute limit.

## PDF rotation

`POST /api/v1/pdf/rotate?angle=90&pages=1,3,5` takes one `files` PDF upload. Use `pages=all` for every page. Angles 90, 180, 270 are clockwise offsets from each page's existing rotation. Duplicate page numbers are applied only once. Python validates selections against the actual document page count, preserves all pages, and saves the full rotated PDF. Existing upload limits, service token, concurrency, timeout and cleanup rules apply. Like other PDF rewrites, digital-signature validity is not preserved.

## Lock / unlock PDF

`POST /api/v1/pdf/lock` and `/api/v1/pdf/unlock` take one `files` PDF and one `password` text field in multipart form data. Passwords must never be passed in URL parameters. Python receives the same password in its internal multipart body and passes it to the subprocess on stdin, not through command-line arguments or a temporary file.

Lock uses AES-256 open-password encryption. A password must contain 8–40 UTF-8 bytes (NUL rejected); the same password is set for user/owner access, with document permissions otherwise unrestricted. Unlock authenticates the supplied existing password (1–127 UTF-8 bytes) and saves an unencrypted copy. Wrong passwords return 403; already-protected lock inputs and unencrypted unlock inputs return 409. There is no password recovery or cracking. Original files, page order, searchable text and annotations are preserved; PDF rewriting invalidates existing digital signatures.

All existing file/page limits, internal token, subprocess timeout, rate limits and temporary cleanup safeguards apply. Serve production endpoints behind HTTPS. Passwords are not written to application logs or persisted by these services.
