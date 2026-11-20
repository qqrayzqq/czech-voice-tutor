export class VoiceDraftError extends Error {
  constructor(code) {
    super(code);
    this.name = 'VoiceDraftError';
    this.code = code;
  }
}

function validJobId(value) {
  return Number.isSafeInteger(value) && value > 0;
}

export function parseVoiceJobId(search) {
  const values = new URLSearchParams(search).getAll('job_id');
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0]) || !validJobId(Number(values[0]))) {
    throw new VoiceDraftError('invalid-job');
  }
  return Number(values[0]);
}

function validateDraft(data, jobId, saved = false) {
  const keys = saved
    ? ['job_id', 'revision', 'text', 'max_chars', 'notification_sent']
    : ['job_id', 'revision', 'text', 'max_chars'];
  if (!data || Array.isArray(data) || typeof data !== 'object'
    || Object.keys(data).length !== keys.length || !keys.every(key => Object.hasOwn(data, key))
    || data.job_id !== jobId || !Number.isSafeInteger(data.revision) || data.revision < 0
    || typeof data.text !== 'string' || data.max_chars !== 2000
    || (saved && typeof data.notification_sent !== 'boolean')) {
    throw new VoiceDraftError('invalid-response');
  }
  return data;
}

async function requestDraft(jobId, initData, {
  method = 'GET', body, fetchImpl = globalThis.fetch, timeoutMs = 15000,
} = {}) {
  if (!validJobId(jobId)) throw new VoiceDraftError('invalid-job');
  if (typeof initData !== 'string' || !initData.trim()) throw new VoiceDraftError('missing-auth');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = { 'X-Telegram-Init-Data': initData, Accept: 'application/json' };
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetchImpl(`/api/voice-jobs/${jobId}/draft`, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
    });
    if (controller.signal.aborted) throw new VoiceDraftError('timeout');
    if (!response.ok) {
      const codes = { 401: 'unauthorized', 404: 'not-found', 409: 'conflict', 422: 'invalid-value' };
      throw new VoiceDraftError(codes[response.status] || 'unavailable');
    }
    try {
      const data = await response.json();
      if (controller.signal.aborted) throw new VoiceDraftError('timeout');
      return data;
    } catch (error) {
      if (error instanceof VoiceDraftError) throw error;
      throw new VoiceDraftError(controller.signal.aborted ? 'timeout' : 'invalid-response');
    }
  } catch (error) {
    if (error instanceof VoiceDraftError) throw error;
    throw new VoiceDraftError(controller.signal.aborted ? 'timeout' : 'network');
  } finally {
    clearTimeout(timeout);
  }
}

export async function loadVoiceDraft(jobId, initData, options = {}) {
  const data = await requestDraft(jobId, initData, options);
  return validateDraft(data, jobId);
}

export async function saveVoiceDraft(jobId, text, revision, initData, options = {}) {
  if (!validJobId(jobId)) throw new VoiceDraftError('invalid-job');
  if (typeof text !== 'string' || !text.trim() || Array.from(text.trim()).length > 2000
    || !Number.isSafeInteger(revision) || revision < 0 || revision >= Number.MAX_SAFE_INTEGER) {
    throw new VoiceDraftError('invalid-value');
  }
  const trimmed = text.trim();
  const data = await requestDraft(jobId, initData, {
    ...options, method: 'PATCH', body: { text: trimmed, revision },
  });
  validateDraft(data, jobId, true);
  if (data.revision !== revision + 1 || data.text !== trimmed) {
    throw new VoiceDraftError('invalid-response');
  }
  return data;
}
