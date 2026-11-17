export class FeedbackError extends Error {
  constructor(code) {
    super(code);
    this.name = 'FeedbackError';
    this.code = code;
  }
}

export function parseReplyId(search) {
  const values = new URLSearchParams(search).getAll('reply_id');
  if (values.length !== 1 || !/^[1-9]\d*$/.test(values[0])) {
    throw new FeedbackError('invalid-id');
  }
  const replyId = Number(values[0]);
  if (!Number.isSafeInteger(replyId)) throw new FeedbackError('invalid-id');
  return replyId;
}

function validateFeedback(data, replyId) {
  if (!data || data.reply_id !== replyId || typeof data.reply_cs !== 'string'
    || typeof data.note_ru !== 'string' || !Array.isArray(data.corrections)
    || !data.corrections.every(item => item && ['original', 'corrected', 'explanation_ru']
      .every(key => typeof item[key] === 'string'))) {
    throw new FeedbackError('invalid-response');
  }
  return data;
}

export async function loadFeedback(replyId, initData, {
  fetchImpl = globalThis.fetch,
  timeoutMs = 15000,
} = {}) {
  if (!Number.isSafeInteger(replyId) || replyId <= 0) throw new FeedbackError('invalid-id');
  if (!initData) throw new FeedbackError('missing-auth');
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`/api/replies/${replyId}/feedback`, {
      method: 'GET',
      headers: { 'X-Telegram-Init-Data': initData, Accept: 'application/json' },
      signal: controller.signal,
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
    });
    if (!response.ok) {
      const codes = { 401: 'unauthorized', 404: 'not-found', 422: 'invalid-id' };
      throw new FeedbackError(codes[response.status] || 'unavailable');
    }
    let data;
    try {
      data = await response.json();
    } catch (error) {
      if (controller.signal.aborted) throw new FeedbackError('timeout');
      throw new FeedbackError('invalid-response');
    }
    return validateFeedback(data, replyId);
  } catch (error) {
    if (error instanceof FeedbackError) throw error;
    throw new FeedbackError(controller.signal.aborted ? 'timeout' : 'network');
  } finally {
    clearTimeout(timeout);
  }
}
