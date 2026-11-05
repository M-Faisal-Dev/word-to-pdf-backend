import { createApp, MAX_BYTES } from './app';
function positiveInt(name: string, fallback: number) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isSafeInteger(value) || value < 1) throw new Error(`Invalid ${name}`);
  return value;
}
const port = positiveInt('PORT', 5010);
if (port > 65535) throw new Error('Invalid PORT');
const handler = createApp({
  gotenbergUrl: process.env.GOTENBERG_URL || 'http://127.0.0.1:3005',
  pythonUrl: process.env.PYTHON_SERVICE_URL || 'http://127.0.0.1:8010',
  pythonToken: process.env.PYTHON_SERVICE_TOKEN,
  origins: (process.env.CORS_ORIGINS || 'http://localhost:8081').split(',').map(origin => origin.trim()).filter(Boolean),
  timeoutMs: positiveInt('CONVERSION_TIMEOUT_MS', 110000),
  maxActive: positiveInt('MAX_ACTIVE_CONVERSIONS', 2), rateLimit: positiveInt('RATE_LIMIT_PER_MINUTE', 20),
});
const server = Bun.serve({
  port, hostname: process.env.HOST || '127.0.0.1', maxRequestBodySize: MAX_BYTES + 65536, idleTimeout: 120,
  fetch(request, server) {
    // Only enable behind a proxy that overwrites this header and blocks direct access.
    const ip = process.env.TRUST_PROXY === '1' ? request.headers.get('x-real-ip') || 'unknown' : server.requestIP(request)?.address || 'unknown';
    return handler(request, ip);
  },
  error() { return Response.json({ error: 'Request could not be processed.' }, { status: 500 }); },
});
console.log(`Word conversion API listening on ${server.hostname}:${server.port}`);
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => {
  void server.stop(false);
  setTimeout(() => { void server.stop(true); process.exit(0); }, 120000).unref();
});
