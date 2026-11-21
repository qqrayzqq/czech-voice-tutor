import { ANSWER_LENGTHS, LANGUAGE_LEVELS, RESPONSE_MODES } from './profile-state.mjs';

export class ProfileError extends Error {
  constructor(code) {
    super(code);
    this.name = 'ProfileError';
    this.code = code;
  }
}

async function requestProfile(path, initData, {
  method = 'GET', body, fetchImpl = globalThis.fetch, timeoutMs = 15000,
} = {}) {
  if (!initData) throw new ProfileError('missing-auth');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers = { 'X-Telegram-Init-Data': initData, Accept: 'application/json' };
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetchImpl(`/api/profile${path}`, {
      method, headers, body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
    });
    if (!response.ok) {
      const codes = { 401: 'unauthorized', 404: 'not-found', 422: 'invalid-value' };
      throw new ProfileError(codes[response.status] || 'unavailable');
    }
    try {
      return await response.json();
    } catch {
      throw new ProfileError(controller.signal.aborted ? 'timeout' : 'invalid-response');
    }
  } catch (error) {
    if (error instanceof ProfileError) throw error;
    throw new ProfileError(controller.signal.aborted ? 'timeout' : 'network');
  } finally {
    clearTimeout(timeout);
  }
}

export async function loadProfile(initData, options = {}) {
  const data = await requestProfile('', initData, options);
  if (!data || Array.isArray(data)
    || !(data.language_level === null || LANGUAGE_LEVELS.includes(data.language_level))
    || !ANSWER_LENGTHS.includes(data.answer_length) || !RESPONSE_MODES.includes(data.response_mode)) {
    throw new ProfileError('invalid-response');
  }
  return data;
}

async function savePreference(path, field, value, allowedValues, initData, options) {
  if (!allowedValues.includes(value)) throw new ProfileError('invalid-value');
  const data = await requestProfile(path, initData, {
    ...options, method: 'PATCH', body: { [field]: value },
  });
  if (!data || Object.keys(data).length !== 1 || data[field] !== value) {
    throw new ProfileError('invalid-response');
  }
  return data;
}

export function updateLanguageLevel(value, initData, options = {}) {
  return savePreference('/language-level', 'language_level', value, LANGUAGE_LEVELS, initData, options);
}

export function updateAnswerLength(value, initData, options = {}) {
  return savePreference('/answer-length', 'answer_length', value, ANSWER_LENGTHS, initData, options);
}

export function updateResponseMode(value, initData, options = {}) {
  return savePreference('/response-mode', 'response_mode', value, RESPONSE_MODES, initData, options);
}
