const assert = require('node:assert/strict');
const { test, afterEach } = require('node:test');
const path = require('node:path');
const Module = require('node:module');
const esbuild = require('../frontend/node_modules/esbuild');
const result = esbuild.buildSync({entryPoints: [path.resolve(__dirname, '../frontend/src/api/client.ts')], bundle: true, platform: 'node', format: 'cjs', write: false});
const compiled = new Module('api-client-test', module);
compiled._compile(result.outputFiles[0].text, 'api-client-test.cjs');
const { api, request, send, apiUrl, setCsrf, ApiError } = compiled.exports;
const originalFetch = global.fetch;
afterEach(() => { global.fetch = originalFetch; setCsrf(''); });

test('one URL builder maps domains and preserves query and encoded IDs', () => {
  assert.equal(apiUrl('/projects/p/writing-sessions/s/resume', 'POST'), '/api/v2/projects/p/writing/sessions/s/actions/resume');
  assert.equal(apiUrl('/settings'), '/api/v2/model-profiles/primary');
  assert.equal(apiUrl('/projects/p/evidence-knowledge/units?query=a%26b'), '/api/v2/projects/p/knowledge/sources/units?query=a%26b');
  assert.equal(apiUrl('/files/f%20a/download'), '/api/v2/files/f%20a/download');
  assert.throws(() => apiUrl('https://evil.invalid'), /路径/);
});

test('collection envelope unwraps for existing pages, remains available through request', async () => {
  global.fetch = async () => Response.json({data: {items: [{id: 'p'}], next_cursor: null, total: 1}, meta: {api_version: 'v2', kind: 'collection', request_id: 'req-test'}});
  assert.deepEqual(await api('/projects'), [{id: 'p'}]);
  assert.equal((await request('/projects')).data.total, 1);
});

test('task response unwraps without losing links in the full contract', async () => {
  global.fetch = async () => Response.json({data: {task: {id: 't', status: 'queued'}, links: {self: '/api/v2/tasks/t'}}, meta: {api_version: 'v2', kind: 'task'}});
  assert.equal((await api('/tasks/t')).id, 't');
  assert.equal((await request('/tasks/t')).data.links.self, '/api/v2/tasks/t');
});

test('Headers instances, CSRF, request id and idempotency headers reach the server', async () => {
  setCsrf('csrf-fake');
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/v2/projects/p/writing/sessions');
    assert.equal(options.headers.get('Idempotency-Key'), 'stable-key');
    assert.equal(options.headers.get('X-CSRF-Token'), 'csrf-fake');
    assert.ok(options.headers.get('X-Request-Id').startsWith('req_'));
    return Response.json({data: {}, meta: {api_version: 'v2'}});
  };
  await api('/projects/p/writing-sessions', {method: 'POST', body: '{}', headers: new Headers({'Idempotency-Key': 'stable-key'})});
});

test('multipart uploads preserve the browser boundary', async () => {
  global.fetch = async (_, options) => { assert.equal(options.headers.has('Content-Type'), false); return Response.json({ok: true}); };
  const form = new FormData(); form.append('file', new Blob(['raw']), 'source.txt');
  assert.deepEqual(await api('/projects/p/files', {method: 'POST', body: form}), {ok: true});
});

test('unknown upstream errors retain request id and never automatically retry', async () => {
  let calls = 0;
  global.fetch = async () => { calls++; return Response.json({data: null, error: {code: 'UPSTREAM_ERROR', message: 'unknown', unknown_outcome: true, retriable: false}, meta: {api_version: 'v2'}}, {status: 502, headers: {'X-Request-Id': 'trace-1'}}); };
  await assert.rejects(send('/projects/p/writing-sessions'), e => e instanceof ApiError && e.unknownOutcome && e.requestId === 'trace-1');
  assert.equal(calls, 1);
});

test('caller cancellation works when a timeout is also configured', async () => {
  global.fetch = async (_, options) => new Promise((resolve, reject) => {
    if (options.signal.aborted) reject(options.signal.reason);
    else options.signal.addEventListener('abort', () => reject(options.signal.reason));
  });
  const control = new AbortController();
  const pending = api('/projects', {signal: control.signal, timeout: 10000});
  control.abort();
  await assert.rejects(pending, {name: 'AbortError'});
});
