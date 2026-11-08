import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, apiWithMeta } from '../api.js';
import { state } from '../state.js';

function mockResponse({
  status = 200,
  body = '',
  ct = 'application/json',
  jsonBody = null,
  version = null,
  headers = {},
} = {}) {
  const values = {
    'Content-Type': ct,
    ...(version ? { 'X-Content-Version': version } : {}),
    ...headers,
  };
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: vi.fn((name) => values[name] ?? null) },
    text: vi.fn().mockResolvedValue(typeof body === 'string' ? body : JSON.stringify(body)),
    json: vi.fn().mockResolvedValue(jsonBody),
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  global.fetch = vi.fn();
  state.currentProjectName = '';
  state.currentProjectDir = '';
  document.body.innerHTML = '<div id="modal-auth" style="display:none"></div>';
});

describe('api()', () => {
  it('sends the stored API token as a Bearer header', async () => {
    sessionStorage.setItem('api_token', 'abc123');
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('GET', '/api/something');
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe('Bearer abc123');
  });

  it('omits the Authorization header when no token is stored', async () => {
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('GET', '/api/something');
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBeUndefined();
  });

  it('serializes a body as JSON and sets Content-Type', async () => {
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('POST', '/api/save', { name: 'x' });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers['Content-Type']).toBe('application/json');
    expect(opts.body).toBe('{"name":"x"}');
  });

  it('applies the abort signal from options', async () => {
    const signal = new AbortController().signal;
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('GET', '/api/x', undefined, { signal });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.signal).toBe(signal);
  });

  it('passes custom request headers', async () => {
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('PUT', '/api/texts', { title: 'x' }, { headers: { 'If-Match': '"abc123"' } });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers['If-Match']).toBe('"abc123"');
    expect(opts.headers['Content-Type']).toBe('application/json');
  });

  it('appends project and project_dir query params', async () => {
    state.currentProjectName = 'trip 川西';
    state.currentProjectDir = 'C:/dir with space';
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('GET', '/api/videos');
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain('project=trip%20%E5%B7%9D%E8%A5%BF');
    expect(url).toContain('project_dir=C%3A%2Fdir%20with%20space');
  });

  it('reuses an existing query string with &', async () => {
    state.currentProjectName = 'trip';
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: {} }));
    await api('GET', '/api/x?a=1');
    const url = global.fetch.mock.calls[0][0];
    expect(url).toContain('a=1&project=trip');
  });

  it('shows the auth modal and throws on 401', async () => {
    global.fetch.mockResolvedValue(mockResponse({ status: 401 }));
    await expect(api('GET', '/api/x')).rejects.toThrow(/401/);
    expect(document.getElementById('modal-auth').style.display).toBe('flex');
  });

  it('rejects with parsed JSON error detail and status', async () => {
    global.fetch.mockResolvedValue(mockResponse({ status: 400, body: '{"error":"bad input"}' }));
    const err = await api('POST', '/api/x').catch((e) => e);
    expect(err.status).toBe(400);
    expect(err.body).toEqual({ error: 'bad input' });
    expect(err.message).toContain('bad input');
  });

  it('falls back to raw text when error body is not JSON', async () => {
    global.fetch.mockResolvedValue(mockResponse({ status: 500, body: 'boom' }));
    const err = await api('GET', '/api/x').catch((e) => e);
    expect(err.status).toBe(500);
    expect(err.message).toContain('boom');
  });

  it('returns null for 204 No Content', async () => {
    global.fetch.mockResolvedValue(mockResponse({ status: 204, body: '' }));
    await expect(api('GET', '/api/x')).resolves.toBeNull();
  });

  it('parses JSON response by content type', async () => {
    global.fetch.mockResolvedValue(mockResponse({ jsonBody: { ok: true } }));
    await expect(api('GET', '/api/x')).resolves.toEqual({ ok: true });
  });

  it('returns text when Content-Type is not JSON', async () => {
    global.fetch.mockResolvedValue(mockResponse({ body: 'plain', ct: 'text/plain' }));
    await expect(api('GET', '/api/x')).resolves.toBe('plain');
  });
});

describe('apiWithMeta()', () => {
  it('returns both data and the content version', async () => {
    global.fetch.mockResolvedValue(mockResponse({
      version: '0123456789abcdef',
      jsonBody: { ok: true },
    }));

    await expect(apiWithMeta('GET', '/api/plan')).resolves.toEqual({
      data: { ok: true },
      version: '0123456789abcdef',
    });
  });

  it('exposes conflict status, body, and current version on 409', async () => {
    global.fetch.mockResolvedValue(mockResponse({
      status: 409,
      body: JSON.stringify({
        ok: false,
        code: 'artifact_conflict',
        error: 'changed elsewhere',
        current_version: 'fedcba9876543210',
      }),
    }));

    const err = await apiWithMeta('PUT', '/api/plan').catch((e) => e);
    expect(err.status).toBe(409);
    expect(err.body.code).toBe('artifact_conflict');
    expect(err.body.current_version).toBe('fedcba9876543210');
    expect(err.currentVersion).toBe('fedcba9876543210');
  });

  it('returns a null body and version for 204', async () => {
    global.fetch.mockResolvedValue(mockResponse({ status: 204, version: '1111111111111111' }));
    await expect(apiWithMeta('DELETE', '/api/x')).resolves.toEqual({
      data: null,
      version: '1111111111111111',
    });
  });
});
