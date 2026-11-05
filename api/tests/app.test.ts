import { describe, test, expect } from 'bun:test';
import { createApp, MAX_BYTES } from '../src/app';
const defaults = { gotenbergUrl: 'http://gotenberg:3005', pythonUrl: 'http://python:8010', pythonToken: 'internal-test-token' };
const pdf = '%PDF-1.7\nexample';
const doc = new Uint8Array([0xd0,0xcf,0x11,0xe0,0xa1,0xb1,0x1a,0xe1,0]);
function upload(path: string, name: string, bytes: string | Uint8Array, field = 'files') {
  const form = new FormData(); form.append(field, new Blob([typeof bytes === 'string' ? bytes : new Uint8Array(bytes)]), name);
  return new Request(`http://localhost${path}`, { method: 'POST', body: form });
}
const mockFetch = (handler: (url: string, init?: RequestInit) => Response | Promise<Response>) => ((url, init) => handler(String(url), init)) as typeof fetch;
describe('Independent conversion API', () => {
  test('Word forwards a single files field to existing Gotenberg and validates PDF', async () => {
    const app = createApp({ ...defaults, fetcher: mockFetch((url, init) => {
      expect(url).toBe('http://gotenberg:3005/forms/libreoffice/convert');
      expect([...((init?.body as FormData).keys())]).toEqual(['files']);
      expect((init?.headers as Record<string,string>)['X-Service-Token']).toBeUndefined();
      return new Response(pdf);
    }) });
    const response = await app(upload('/api/v1/convert/word', 'a.doc', doc), 'client');
    expect(response.status).toBe(200); expect(await response.text()).toBe(pdf);
  });
  test('PDF to Word forwards file and internal token only to Python', async () => {
    const app = createApp({ ...defaults, fetcher: mockFetch((url, init) => {
      expect(url).toBe('http://python:8010/api/convert/pdf-to-word');
      expect([...((init?.body as FormData).keys())]).toEqual(['file']);
      expect((init?.headers as Record<string,string>)['X-Service-Token']).toBe('internal-test-token');
      return new Response(new Uint8Array([0x50,0x4b,3,4,0,0,0,0]));
    }) });
    expect((await app(upload('/api/v1/convert/pdf-to-word', 'a.pdf', pdf), 'client')).status).toBe(200);
  });
  test('rejects invalid signatures, extra fields and oversized uploads', async () => {
    const app = createApp(defaults);
    expect((await app(upload('/api/v1/convert/word', 'a.docx', 'bad'), 'c')).status).toBe(415);
    expect((await app(upload('/api/v1/convert/pdf-to-word', 'a.pdf', pdf, 'wrong'), 'c')).status).toBe(400);
    const form = new FormData(); form.append('files', new Blob([doc]), 'a.doc'); form.append('extra', '1');
    expect((await app(new Request('http://localhost/api/v1/convert/word', { method:'POST',body:form }), 'c')).status).toBe(400);
    expect((await app(upload('/api/v1/convert/word','a.doc',new Uint8Array(MAX_BYTES + 1)), 'c')).status).toBe(413);
  });
  test('rate limits by client and applies explicit CORS allowlist', async () => {
    const app = createApp({ ...defaults, rateLimit: 1, origins: ['https://app.example'] });
    await app(upload('/api/v1/convert/word','a.doc','bad'), 'c');
    const limited = await app(upload('/api/v1/convert/word','a.doc','bad'), 'c');
    expect(limited.status).toBe(429); expect(limited.headers.get('Retry-After')).toBeTruthy();
    expect((await app(new Request('http://localhost/health', { headers: { Origin: 'https://evil.example' } }), 'c')).status).toBe(403);
    const allowed = await app(new Request('http://localhost/api/v1/convert/word', { method:'OPTIONS', headers:{Origin:'https://app.example'} }), 'c');
    expect(allowed.status).toBe(204); expect(allowed.headers.get('Access-Control-Allow-Origin')).toBe('https://app.example');
  });
  test('rejects broken output and upstream failures', async () => {
    for (const [upstream, expected] of [[200,502],[400,422],[503,503],[504,504]] as const) {
      const app = createApp({ ...defaults, fetcher:mockFetch(() => new Response('invalid',{status:upstream})) });
      expect((await app(upload('/api/v1/convert/word','a.doc',doc),'c')).status).toBe(expected);
    }
  });
  test('timeouts abort upstream and release capacity', async () => {
    const app = createApp({ ...defaults, timeoutMs: 10, maxActive: 1, fetcher: mockFetch((_url, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new Error('abort'))))) });
    const first = app(upload('/api/v1/convert/word','a.doc',doc),'a');
    await new Promise(resolve => setTimeout(resolve, 1));
    expect((await app(upload('/api/v1/convert/word','b.doc',doc),'b')).status).toBe(503);
    expect((await first).status).toBe(504);
    expect((await app(upload('/api/v1/convert/word','a.doc',doc),'a')).status).toBe(504);
  });
  test('health reports degraded upstream', async () => {
    const app = createApp({...defaults, fetcher:mockFetch(url => new Response('{}',{status:url.includes('python')?503:200}))});
    const response = await app(new Request('http://localhost/health'),'a');
    expect(response.status).toBe(503); expect((await response.json()).gotenberg).toBe('up');
  });
});

test('compression forwards validated level and PDF to internal Python service', async () => {
  const app = createApp({ ...defaults, fetcher: mockFetch((url, init) => {
    expect(url).toBe('http://python:8010/api/pdf/compress?level=strong');
    expect([...(init?.body as FormData).keys()]).toEqual(['file']);
    expect((init?.headers as Record<string,string>)['X-Service-Token']).toBe('internal-test-token');
    return new Response(pdf);
  }) });
  expect((await app(upload('/api/v1/pdf/compress?level=strong', 'sample.pdf', pdf), 'client')).status).toBe(200);
  expect((await app(upload('/api/v1/pdf/compress?level=bogus', 'sample.pdf', pdf), 'client')).status).toBe(400);
  expect((await app(upload('/api/v1/pdf/compress', 'sample.doc', doc), 'client')).status).toBe(415);
});

test('split validates page ranges and forwards PDF to internal service', async () => {
  const app = createApp({ ...defaults, fetcher: mockFetch((url, init) => {
    expect(url).toBe('http://python:8010/api/pdf/split?from=2&to=4');
    expect([...(init?.body as FormData).keys()]).toEqual(['file']);
    expect((init?.headers as Record<string,string>)['X-Service-Token']).toBe('internal-test-token');
    return new Response(pdf);
  }) });
  expect((await app(upload('/api/v1/pdf/split?from=2&to=4', 'sample.pdf', pdf), 'client')).status).toBe(200);
  for (const range of ['from=0&to=4','from=4&to=2','from=1&to=301','from=x&to=4','']) {
    expect((await app(upload(`/api/v1/pdf/split?${range}`, 'sample.pdf', pdf), 'client')).status).toBe(400);
  }
});

test('rotate validates selection and forwards rotation to internal service', async () => {
  const app = createApp({ ...defaults, fetcher: mockFetch((url, init) => {
    expect(url).toBe('http://python:8010/api/pdf/rotate?angle=270&pages=1%2C3');
    expect([...(init?.body as FormData).keys()]).toEqual(['file']);
    expect((init?.headers as Record<string,string>)['X-Service-Token']).toBe('internal-test-token');
    return new Response(pdf);
  }) });
  expect((await app(upload('/api/v1/pdf/rotate?angle=270&pages=1,3','sample.pdf',pdf),'client')).status).toBe(200);
  for (const params of ['angle=45','pages=0','pages=1,,3','pages=301','pages=1-3']) expect((await app(upload(`/api/v1/pdf/rotate?${params}`,'sample.pdf',pdf),'client')).status).toBe(400);
});

test('lock/unlock send passwords in body only, with clear failures', async () => {
  let status=200;
  const app=createApp({...defaults,fetcher:mockFetch((url,init)=>{
    expect(url).toBe('http://python:8010/api/pdf/unlock');
    expect((init?.body as FormData).get('password')).toBe('test-password');
    expect((init?.headers as Record<string,string>)['X-Service-Token']).toBe('internal-test-token');
    return new Response(pdf,{status});
  })});
  const request=()=>{const form=new FormData();form.append('files',new Blob([pdf]),'test.pdf');form.append('password','test-password');return new Request('http://localhost/api/v1/pdf/unlock',{method:'POST',body:form});};
  expect((await app(request(),'client')).status).toBe(200);
  status=403;expect((await app(request(),'client')).status).toBe(403);
  status=409;expect((await app(request(),'client')).status).toBe(409);
  expect((await app(upload('/api/v1/pdf/lock','test.pdf',pdf),'client')).status).toBe(400);
});
