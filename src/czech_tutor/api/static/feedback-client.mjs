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
    || (data.feedback_status !== undefined && !['evaluated', 'not_evaluated', 'clarification', 'superseded'].includes(data.feedback_status))
    || (data.feedback_language !== undefined && !['ru', 'uk'].includes(data.feedback_language))
    || (data.interface_language !== undefined && !['ru', 'uk'].includes(data.interface_language))
    || (data.advice_count !== undefined && (!Number.isSafeInteger(data.advice_count) || data.advice_count < 0))
    || !data.corrections.every(item => item && ['original', 'corrected', 'explanation_ru']
      .every(key => typeof item[key] === 'string')
      && (item.kind === undefined || ['error', 'advice'].includes(item.kind)))) {
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

export function feedbackPresentation(feedback) {
  const status = feedback.feedback_status || 'evaluated';
  const current = status === 'evaluated' ? feedback.corrections : [];
  return {
    status,
    showNote: ['evaluated', 'not_evaluated'].includes(status) && Boolean(feedback.note_ru?.trim()),
    errors: current.filter(item => item.kind !== 'advice'),
    advice: current.filter(item => item.kind === 'advice'),
  };
}

export function feedbackHeading(feedback, tab) {
  if (tab === 1) return {
    title: 'Текст ответа',
    description: 'Прочитай ответ в своём темпе и возвращайся к разговору.',
  };
  const { status, errors, advice } = feedbackPresentation(feedback);
  if (status === 'not_evaluated') return {
    title: 'Пояснение', description: 'Ответ на твой вопрос — без оценки твоей фразы.',
  };
  if (status === 'clarification') return {
    title: 'Уточним смысл', description: 'Бот задаёт уточняющий вопрос. Оценки этой фразы пока нет.',
  };
  if (status === 'superseded') return {
    title: 'Оценка отозвана', description: 'Прежний разбор скрыт после уточнения. Здесь доступен сохранённый ответ.',
  };
  if (errors.length) return {
    title: 'Разбор ответа', description: 'Маленькие исправления. Больше уверенности в разговоре.',
  };
  if (advice.length) return {
    title: 'Небольшой совет', description: 'Твоя фраза верна. Вот как сделать её естественнее.',
  };
  return {
    title: 'Всё правильно', description: 'Ошибок нет. Продолжай разговор в своём темпе.',
  };
}
