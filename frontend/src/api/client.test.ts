import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, apiFetch } from './client';

const reply = (body: string, init: ResponseInit) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, init)));
};

afterEach(() => vi.unstubAllGlobals());

describe('apiFetch', () => {
  it('turns the error envelope into an ApiError', async () => {
    reply(JSON.stringify({ error: { code: 'PLAN_NOT_APPROVED', message: 'Approve first', details: { a: 1 } } }), {
      status: 409,
    });
    await expect(apiFetch('/x')).rejects.toMatchObject({ code: 'PLAN_NOT_APPROVED', status: 409, details: { a: 1 } });
  });

  it('turns a non-JSON error page into an HTTP_ERROR with the status', async () => {
    reply('<html>502 Bad Gateway</html>', { status: 502, statusText: 'Bad Gateway' });
    const error = await apiFetch('/x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: 'HTTP_ERROR', status: 502, message: '502 Bad Gateway' });
  });

  it('turns a non-JSON success body into an HTTP_ERROR, not a SyntaxError', async () => {
    reply('<html>captive portal</html>', { status: 200 });
    const error = await apiFetch('/x').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: 'HTTP_ERROR', status: 200 });
  });

  it('still returns parsed JSON, and null for an empty body', async () => {
    reply('{"status":"ok"}', { status: 200 });
    await expect(apiFetch('/x')).resolves.toEqual({ status: 'ok' });
    reply('', { status: 200 });
    await expect(apiFetch('/x')).resolves.toBeNull();
  });
});
