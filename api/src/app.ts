export const MAX_BYTES = 30 * 1024 * 1024;
const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
type Options = {
  gotenbergUrl: string; pythonUrl: string; pythonToken?: string; origins?: string[];
  timeoutMs?: number; maxActive?: number; rateLimit?: number;
  fetcher?: typeof fetch;
};
class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
function baseUrl(value: string) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error('Invalid upstream URL');
  return url.toString().replace(/\/$/, '');
}
async function readBounded(response: Response): Promise<Uint8Array<ArrayBuffer>> {
  if (Number(response.headers.get('content-length')) > MAX_BYTES) throw new ApiError(502, 'Converted file exceeds 30 MB.');
  const reader = response.body?.getReader();
  if (!reader) throw new ApiError(502, 'Conversion service returned an empty file.');
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) { await reader.cancel(); throw new ApiError(502, 'Converted file exceeds 30 MB.'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const output = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return output;
}

export function createApp(options: Options) {
  const gotenberg = baseUrl(options.gotenbergUrl), python = baseUrl(options.pythonUrl);
  const fetcher = options.fetcher ?? fetch;
  const timeoutMs = options.timeoutMs ?? 110000;
  const buckets = new Map<string, { count: number; reset: number }>();
  let active = 0;
  function json(error: string, status: number) { return Response.json({ error }, { status }); }
  async function handle(request: Request, clientIp: string): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (request.method === 'OPTIONS') return new Response(null, { status: 204 });
    if (request.method === 'GET' && path === '/health') {
      const results = await Promise.allSettled([
        fetcher(`${gotenberg}/health`, { signal: AbortSignal.timeout(5000) }),
        fetcher(`${python}/health`, { signal: AbortSignal.timeout(5000) }),
      ]);
      const statuses = results.map(result => result.status === 'fulfilled' && result.value.ok ? 'up' : 'down');
      // Health response bodies must not leave upstream sockets occupied.
      await Promise.all(results.map(result => result.status === 'fulfilled' ? result.value.body?.cancel() : undefined));
      return Response.json({ status: statuses.every(status => status === 'up') ? 'up' : 'degraded', gotenberg: statuses[0], python: statuses[1] }, { status: statuses.includes('down') ? 503 : 200 });
    }
    const toWord = path === '/api/v1/convert/pdf-to-word';
    const compress = path === '/api/v1/pdf/compress';
    const split = path === '/api/v1/pdf/split';
    const rotate = path === '/api/v1/pdf/rotate';
    const security = path === '/api/v1/pdf/lock' ? 'lock' : path === '/api/v1/pdf/unlock' ? 'unlock' : undefined;
    const pythonPdf = toWord || compress || split || rotate || !!security;
    const params = new URL(request.url).searchParams;
    const angle = Number(params.get('angle') || 90);
    const pages = params.get('pages') || 'all';
    if (rotate && (![90, 180, 270].includes(angle) || pages.length > 1200 || (pages !== 'all' && !pages.split(',').every(page => /^\d+$/.test(page) && Number(page) >= 1 && Number(page) <= 300)))) return json('Choose valid pages and a rotation of 90, 180 or 270 degrees.', 400);
    const first = Number(params.get('from')), last = Number(params.get('to'));
    if (split && (!Number.isInteger(first) || !Number.isInteger(last) || first < 1 || last < first || last > 300)) return json('Choose valid page numbers from 1 to 300.', 400);
    const level = new URL(request.url).searchParams.get('level') || 'balanced';
    if (compress && !['lossless', 'balanced', 'strong'].includes(level)) return json('Invalid compression level.', 400);
    if (path !== '/api/v1/convert/word' && !toWord && !compress && !split && !rotate && !security) return json('Not found.', 404);
    if (request.method !== 'POST') return new Response(null, { status: 405, headers: { Allow: 'POST' } });
    const now = Date.now();
    for (const [key, bucket] of buckets) if (bucket.reset <= now) buckets.delete(key);
    let bucket = buckets.get(clientIp);
    if (!bucket) {
      if (buckets.size >= 10000) return json('Server busy. Please retry shortly.', 503);
      bucket = { count: 0, reset: now + 60000 }; buckets.set(clientIp, bucket);
    }
    if (++bucket.count > (options.rateLimit ?? 20)) return new Response(JSON.stringify({ error: 'Too many conversions. Retry after a minute.' }), { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': String(Math.ceil((bucket.reset - now) / 1000)) } });
    if (active >= (options.maxActive ?? 2)) return json('Conversion server is busy. Please retry.', 503);
    active++;
    const controller = new AbortController();
    const cancel = () => controller.abort();
    request.signal.addEventListener('abort', cancel);
    if (request.signal.aborted) cancel();
    const timer = setTimeout(cancel, timeoutMs);
    try {
      if (!request.headers.get('content-type')?.startsWith('multipart/form-data')) throw new ApiError(415, 'Send multipart/form-data with one files field.');
      if (Number(request.headers.get('content-length')) > MAX_BYTES + 65536) throw new ApiError(413, 'Maximum input size is 30 MB.');
      const form = await request.formData().catch(() => { throw new ApiError(400, 'Invalid multipart upload.'); });
      const fields = [...form.entries()];
      const values = form.getAll('files');
      const value: unknown = values[0];
      if (values.length !== 1 || !(value instanceof File) || (security ? fields.length !== 2 || form.getAll('password').length !== 1 : fields.length !== 1)) throw new ApiError(400, security ? 'Upload one file and one password field.' : 'Upload exactly one file in the files field.');
      let password: string | undefined;
      if (security) {
        if (params.has('password')) throw new ApiError(400, 'Send the password in the form body only.');
        const supplied = form.get('password');
        const length = typeof supplied === 'string' ? new TextEncoder().encode(supplied).length : 0;
        if (typeof supplied !== 'string' || supplied.includes('\0') || (security === 'lock' ? length < 8 || length > 40 : length < 1 || length > 127)) throw new ApiError(400, security === 'lock' ? 'Use a password of 8–40 UTF-8 bytes.' : 'Enter the PDF password (up to 127 UTF-8 bytes).');
        password = supplied;
      }
      const file = value;
      if (!file.size || file.size > MAX_BYTES) throw new ApiError(413, 'Choose a non-empty file up to 30 MB.');
      const extension = /\.(docx|doc|pdf)$/i.exec(file.name)?.[1].toLowerCase();
      const header = new Uint8Array(await file.slice(0, 8).arrayBuffer());
      const zip = header[0] === 0x50 && header[1] === 0x4b && header[2] === 3 && header[3] === 4;
      const ole = [0xd0,0xcf,0x11,0xe0,0xa1,0xb1,0x1a,0xe1].every((byte, i) => header[i] === byte);
      const pdf = new TextDecoder().decode(header.slice(0, 5)) === '%PDF-';
      if (pythonPdf ? extension !== 'pdf' || !pdf : !(extension === 'doc' && ole || extension === 'docx' && zip)) throw new ApiError(415, pythonPdf ? 'Only valid PDF files are supported.' : 'Only valid DOC and DOCX files are supported.');
      const body = new FormData(); body.append(pythonPdf ? 'file' : 'files', file, `document.${extension}`);
      if (password !== undefined) body.append('password', password);
      const headers: Record<string, string> = { Accept: toWord ? DOCX_MIME : 'application/pdf' };
      if (pythonPdf && options.pythonToken) headers['X-Service-Token'] = options.pythonToken;
      const upstream = await fetcher(security ? `${python}/api/pdf/${security}` : rotate ? `${python}/api/pdf/rotate?angle=${angle}&pages=${encodeURIComponent(pages)}` : split ? `${python}/api/pdf/split?from=${first}&to=${last}` : compress ? `${python}/api/pdf/compress?level=${level}` : toWord ? `${python}/api/convert/pdf-to-word` : `${gotenberg}/forms/libreoffice/convert`, { method: 'POST', body, signal: controller.signal, headers });
      if (!upstream.ok) {
        await upstream.body?.cancel();
        const status = upstream.status;
        if (security && status === 403) throw new ApiError(403, 'Incorrect PDF password. Please try again.');
        if (security && status === 409) throw new ApiError(409, security === 'lock' ? 'This PDF is already password protected. Unlock it first.' : 'This PDF is not password protected.');
        if ([400, 415, 422].includes(status)) throw new ApiError(422, security ? 'Cannot process this PDF. It may be damaged or exceed the page limit.' : rotate ? 'Cannot rotate this PDF. Check page numbers or whether the PDF is damaged or password protected.' : split ? 'Cannot split this PDF. Check page numbers or whether the PDF is damaged or password protected.' : compress ? 'Cannot compress this PDF. It may be damaged, password protected, or over the page limit.' : 'Cannot convert this document. It may be damaged, password protected, scanned, or unsupported.');
        if ([429, 503].includes(status)) throw new ApiError(503, 'Conversion service is busy. Please retry.');
        if (status === 504) throw new ApiError(504, 'Conversion timed out. Please retry.');
        throw new ApiError(502, 'Conversion service could not complete the request.');
      }
      const output = await readBounded(upstream);
      if (output.length < 8 || (toWord ? !(output[0] === 0x50 && output[1] === 0x4b && output[2] === 3 && output[3] === 4) : new TextDecoder().decode(output.slice(0, 5)) !== '%PDF-')) throw new ApiError(502, 'Conversion service returned an invalid file.');
      return new Response(output, { headers: { 'Content-Type': toWord ? DOCX_MIME : 'application/pdf', 'Content-Disposition': `attachment; filename="converted.${toWord ? 'docx' : 'pdf'}"` } });
    } catch (error) {
      if (controller.signal.aborted) return json('Conversion timed out or was cancelled.', 504);
      if (error instanceof ApiError) return json(error.message, error.status);
      return json('Cannot reach the conversion service. Please retry.', 502);
    } finally { clearTimeout(timer); request.signal.removeEventListener('abort', cancel); active--; }
  }
  return async (request: Request, clientIp: string): Promise<Response> => {
    const origin = request.headers.get('origin');
    let response: Response;
    if (origin && !(options.origins ?? []).includes(origin)) response = json('Origin not allowed.', 403);
    else response = await handle(request, clientIp);
    response.headers.set('Cache-Control', 'no-store');
    response.headers.set('X-Content-Type-Options', 'nosniff');
    response.headers.set('Vary', 'Origin');
    if (origin && (options.origins ?? []).includes(origin)) {
      response.headers.set('Access-Control-Allow-Origin', origin);
      response.headers.set('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
      response.headers.set('Access-Control-Allow-Headers', 'Content-Type');
      response.headers.set('Access-Control-Expose-Headers', 'Content-Disposition, Retry-After');
    }
    return response;
  };
}
