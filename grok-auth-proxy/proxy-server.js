'use strict';

const http = require('http');
const { createGate } = require('./request-gate');

function integer(env, key, fallback) {
  const raw = env[key] ?? String(fallback);
  const value = Number(raw);
  if (!/^[1-9][0-9]*$/.test(raw) || !Number.isSafeInteger(value)) {
    throw new Error(`${key} must be a positive integer`);
  }
  return value;
}

function configuration(env) {
  const config = {
    port: integer(env, 'GROK_PROXY_PORT', 3457),
    concurrency: integer(env, 'GROK_MAX_CONCURRENCY', 2),
    queue: integer(env, 'GROK_MAX_QUEUE', 200),
    waitMs: integer(env, 'GROK_QUEUE_WAIT_MS', 180000),
    timeoutMs: integer(env, 'GROK_SOCKET_TIMEOUT_MS', 300000),
    bodyLimit: integer(env, 'GROK_MAX_BODY_BYTES', 4194304),
    effort: env.GROK_REASONING_EFFORT || 'high',
  };
  if (config.port > 65535 || Math.max(config.waitMs, config.timeoutMs) > 2147483647) {
    throw new Error('Port or timer exceeds the supported range');
  }
  if (!['low', 'medium', 'high', 'xhigh', 'max'].includes(config.effort)) {
    throw new Error('Invalid GROK_REASONING_EFFORT');
  }
  return config;
}

function fail(res, status, error) {
  if (res.destroyed) return;
  if (res.headersSent) { res.destroy(); return; }
  res.writeHead(status, { 'content-type': 'application/json', connection: 'close',
    ...(status === 503 ? { 'retry-after': '5' } : {}) });
  res.end(JSON.stringify({ error }));
}

function bodyFor(req, res, limit, callback) {
  if (Number(req.headers['content-length']) > limit) {
    fail(res, 413, 'request_body_too_large');
    req.resume();
    return;
  }
  const chunks = [];
  let bytes = 0;
  let rejected = false;
  req.on('data', chunk => {
    bytes += chunk.length;
    if (rejected) return;
    if (bytes > limit) {
      rejected = true;
      chunks.length = 0;
      fail(res, 413, 'request_body_too_large');
      return;
    }
    chunks.push(chunk);
  });
  req.on('end', () => {
    if (!rejected && !res.destroyed && !res.writableEnded) callback(Buffer.concat(chunks));
  });
  req.once('aborted', () => res.destroy());
  req.once('error', () => res.destroy());
}

function prepareBody(req, body, effort) {
  const isChat = /^\/(?:v1\/)?(?:chat\/completions|messages)(?:\?|$)/.test(req.url);
  if (!isChat || !/application\/json/i.test(req.headers['content-type'] || '')) return body;
  const parsed = JSON.parse(body.toString('utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('JSON object required');
  }
  if (!parsed.reasoning_effort) parsed.reasoning_effort = effort;
  return Buffer.from(JSON.stringify(parsed));
}

function forward(req, res, body, config, dependencies) {
  const { jwt, ver } = dependencies.auth();
  if (!jwt) { fail(res, 401, 'no_grok_cli_session'); return; }
  const headers = { ...req.headers, host: dependencies.host, authorization: `Bearer ${jwt}`,
    'x-grok-client-version': ver, 'x-client-version': ver };
  for (const name of ['connection', 'keep-alive', 'proxy-authorization', 'proxy-authenticate',
    'te', 'trailer', 'transfer-encoding', 'upgrade', 'content-length']) delete headers[name];
  headers['content-length'] = body.length;
  const upstream = dependencies.request({ method: req.method, path: req.url, headers,
    timeout: config.timeoutMs }, response => {
    dependencies.capture(response.headers);
    response.once('aborted', () => res.destroy());
    response.once('error', () => res.destroy());
    if (res.destroyed) { response.destroy(); return; }
    res.writeHead(response.statusCode || 502, response.headers);
    response.pipe(res);
  });
  upstream.once('timeout', () => upstream.destroy(new Error('upstream_timeout')));
  upstream.once('error', () => fail(res, 502, 'upstream_connection_error'));
  res.once('close', () => upstream.destroy());
  upstream.end(body);
}

function createProxy(config, dependencies) {
  const gate = createGate(config.concurrency, config.queue, config.waitMs);
  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url === '/health') {
      const { jwt, ver } = dependencies.auth();
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ ok: true, hasJwt: Boolean(jwt), clientVersion: ver || null,
        gate: gate.snapshot() }));
      return;
    }
    let release = () => {};
    let cancel = () => {};
    res.once('close', () => { cancel(); release(); });
    res.once('finish', () => release());
    req.once('aborted', () => res.destroy());
    req.once('error', () => res.destroy());
    cancel = gate.acquire(done => {
      release = done;
      if (req.aborted || res.destroyed) { done(); return; }
      const deadline = setTimeout(() => {
        fail(res, 504, 'request_deadline');
      }, config.timeoutMs);
      res.once('close', () => clearTimeout(deadline));
      res.once('finish', () => clearTimeout(deadline));
      bodyFor(req, res, config.bodyLimit, body => {
        let prepared;
        try { prepared = prepareBody(req, body, config.effort); }
        catch { fail(res, 400, 'invalid_json_body'); return; }
        if (prepared.length > config.bodyLimit) { fail(res, 413, 'request_body_too_large'); return; }
        forward(req, res, prepared, config, dependencies);
      });
    }, reason => { fail(res, 503, reason); req.resume(); });
  });
  server.requestTimeout = config.timeoutMs;
  return server;
}

module.exports = { configuration, createProxy };
