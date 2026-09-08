'use strict';
const assert = require('node:assert/strict');
const { test } = require('node:test');
const http = require('node:http');
const { once } = require('node:events');
const { configuration, createProxy } = require('../grok-auth-proxy/proxy-server');
const { headerNumber } = require('../grok-auth-proxy/grok-auth-proxy');

async function fixture(t, handler, overrides = {}) {
  let upstreamCalls = 0;
  const upstream = http.createServer((req, res) => { upstreamCalls++; handler(req, res); });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const proxy = createProxy({ ...configuration({}), timeoutMs: 2000, ...overrides }, {
    host: '127.0.0.1', auth: () => ({ jwt: 'fixture', ver: 'test' }), capture: () => {},
    request: (options, callback) => http.request({ ...options,
      hostname: '127.0.0.1', port: upstream.address().port }, callback),
  });
  proxy.listen(0, '127.0.0.1');
  await once(proxy, 'listening');
  t.after(async () => {
    proxy.closeAllConnections(); upstream.closeAllConnections();
    await Promise.all([new Promise(resolve => proxy.close(resolve)),
      new Promise(resolve => upstream.close(resolve))]);
  });
  return { port: proxy.address().port, calls: () => upstreamCalls };
}

function request(port, body, headers = {}, endpoint = '/v1/chat/completions') {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path: endpoint,
      method: 'POST', headers: { 'content-type': 'application/json', ...headers } }, res => {
      let text = '';
      res.on('data', chunk => { text += chunk; });
      res.on('error', reject);
      res.on('end', () => resolve({ status: res.statusCode, text, headers: res.headers }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

function health(port) {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}/health`, res => {
      let text = '';
      res.on('data', chunk => { text += chunk; });
      res.on('end', () => resolve(JSON.parse(text)));
    }).on('error', reject);
  });
}

async function until(check) {
  for (let i = 0; i < 100; i++) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail('condition not reached');
}

test('configuration has conservative defaults and rejects invalid numbers', () => {
  assert.equal(configuration({}).concurrency, 2);
  for (const value of ['NaN', '0', '-1', '1.5', '', 'Infinity']) {
    assert.throws(() => configuration({ GROK_MAX_CONCURRENCY: value }));
  }
  assert.throws(() => configuration({ GROK_PROXY_PORT: '65536' }));
  assert.throws(() => configuration({ GROK_SOCKET_TIMEOUT_MS: '2147483648' }));
});

test('usage preserves exhausted quota and missing is unknown', () => {
  assert.equal(headerNumber('0'), 0);
  assert.equal(headerNumber(undefined), null);
  assert.equal(headerNumber('invalid'), null);
});

test('chunked JSON is reframed once and explicit effort is preserved', async t => {
  const f = await fixture(t, (req, res) => {
    assert.equal(req.headers['transfer-encoding'], undefined);
    assert.equal(req.headers.authorization, 'Bearer fixture');
    let text = '';
    req.on('data', chunk => { text += chunk; });
    req.on('end', () => {
      assert.equal(Number(req.headers['content-length']), Buffer.byteLength(text));
      assert.equal(JSON.parse(text).reasoning_effort, 'medium');
      res.end('ok');
    });
  });
  const result = await request(f.port, JSON.stringify({ messages: [], reasoning_effort: 'medium' }),
    { 'transfer-encoding': 'chunked' });
  assert.equal(result.status, 200);
  assert.equal(f.calls(), 1);
});

test('missing effort is added and non-chat bodies pass unchanged', async t => {
  const bodies = [];
  const f = await fixture(t, (req, res) => {
    let text = '';
    req.on('data', chunk => { text += chunk; });
    req.on('end', () => { bodies.push(text); res.end('ok'); });
  });
  await request(f.port, '{"messages":[]}');
  await request(f.port, 'literal', {}, '/other');
  assert.equal(JSON.parse(bodies[0]).reasoning_effort, 'high');
  assert.equal(bodies[1], 'literal');
});

test('oversized fixed and chunked bodies never reach upstream', async t => {
  const f = await fixture(t, (req, res) => res.end('unexpected'), { bodyLimit: 64 });
  for (const headers of [{ 'content-length': '100' }, { 'transfer-encoding': 'chunked' }]) {
    assert.equal((await request(f.port, 'x'.repeat(100), headers)).status, 413);
  }
  assert.equal(f.calls(), 0);
  await until(async () => (await health(f.port)).gate.active === 0);
});

test('invalid JSON never reaches upstream', async t => {
  const f = await fixture(t, (req, res) => res.end('unexpected'));
  assert.equal((await request(f.port, 'not json')).status, 400);
  assert.equal(f.calls(), 0);
});

test('disconnect while queued removes work before starting upstream', async t => {
  let finishFirst;
  const f = await fixture(t, (req, res) => { finishFirst = () => res.end('ok'); }, { concurrency: 1 });
  const first = request(f.port, '{}');
  await until(() => f.calls() === 1);
  const queued = http.request({ hostname: '127.0.0.1', port: f.port,
    path: '/v1/chat/completions', method: 'POST' });
  queued.on('error', () => {});
  queued.end('{}');
  await until(async () => (await health(f.port)).gate.queued === 1);
  queued.destroy();
  await until(async () => (await health(f.port)).gate.queued === 0);
  finishFirst();
  await first;
  await until(async () => (await health(f.port)).gate.active === 0);
  assert.equal(f.calls(), 1);
});

test('queue is bounded and wait timeout does not start work', async t => {
  let finishFirst;
  const f = await fixture(t, (req, res) => { finishFirst = () => res.end('ok'); },
    { concurrency: 1, queue: 1, waitMs: 300 });
  const first = request(f.port, '{}');
  await until(() => f.calls() === 1);
  const second = request(f.port, '{}');
  await until(async () => (await health(f.port)).gate.queued === 1);
  const overflow = await request(f.port, '{}');
  assert.equal(overflow.status, 503);
  assert.equal(overflow.headers['retry-after'], '5');
  assert.equal((await second).status, 503);
  finishFirst();
  await first;
  assert.equal(f.calls(), 1);
});

test('aborted upstream stream releases slot for the next request', async t => {
  let call = 0;
  const f = await fixture(t, (req, res) => {
    call++;
    if (call === 1) {
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      res.write('data: partial\n\n');
      setTimeout(() => res.destroy(), 30);
    } else res.end('ok');
  }, { concurrency: 1 });
  await assert.rejects(request(f.port, '{}'));
  assert.equal((await request(f.port, '{}')).text, 'ok');
  await until(async () => (await health(f.port)).gate.active === 0);
});

test('deadline bounds a provider that never sends a response', async t => {
  const f = await fixture(t, () => {}, { timeoutMs: 100 });
  assert.equal((await request(f.port, '{}')).status, 504);
  await until(async () => (await health(f.port)).gate.active === 0);
});
