import { initializeLocalization, setLanguage, t, onLanguageChange } from './localization.mjs';
import { loadProfile } from './profile-client.mjs';
import { initializeMiniApp, revealMiniApp } from './mini-app-ui.mjs';
import { loadVoiceDraft, parseVoiceJobId, saveVoiceDraft } from './voice-client.mjs';
import { createVoiceEditor } from './voice-state.mjs';

initializeLocalization();
const telegram = initializeMiniApp();
const params = new URLSearchParams(window.location.search);
const demo = params.get('demo') === '1';
const editor = createVoiceEditor();
const byId = id => document.getElementById(id);
const input = byId('voice-text');
const saveButton = byId('voice-save');
let jobId = null;
let closingConfirmation = false;
let savedFocus = false;
let confirmOpen = false;
let closeApproved = false;

const errorMessages = {
  'invalid-job': 'Эта ссылка не открывает текст. Вернись в чат с ботом и нажми кнопку правки ещё раз.',
  'missing-auth': 'Открой редактор из чата с ботом в Telegram.',
  unauthorized: 'Ссылка больше не действует. Открой редактор заново из чата с ботом.',
  'not-found': 'Этот текст недоступен. Вернись в чат с ботом и открой правку ещё раз.',
  conflict: 'Текст уже изменён, отправлен или время правки истекло. Твоя правка осталась здесь. Вернись в чат и отправь /voice, чтобы проверить текущий текст.',
  'invalid-value': 'Проверь текст: нужно от 1 до 2000 символов.',
  timeout: 'Не удалось дождаться ответа. Твоя правка осталась здесь. Попробуй сохранить ещё раз.',
  network: 'Не удалось связаться с ботом. Твоя правка осталась здесь. Проверь соединение и попробуй ещё раз.',
  'invalid-response': 'Не удалось подтвердить сохранение. Твоя правка осталась здесь. Попробуй сохранить ещё раз.',
  unavailable: 'Сохранение пока недоступно. Твоя правка осталась здесь. Попробуй ещё раз немного позже.',
};

function syncClosingConfirmation() {
  const needed = editor.dirty || editor.saving;
  if (needed === closingConfirmation) return;
  try {
    if (needed) telegram?.enableClosingConfirmation?.();
    else telegram?.disableClosingConfirmation?.();
    closingConfirmation = needed;
  } catch {
    // Explicit close controls still ask before discarding local edits.
  }
}

function render() {
  byId('main-content').setAttribute('aria-busy', String(editor.loading || editor.saving));
  byId('voice-loading').hidden = !editor.loading;
  byId('voice-editor').hidden = !editor.loaded || editor.saved;
  byId('voice-saved').hidden = !editor.saved;
  input.disabled = !editor.loaded;
  if (input.value !== editor.text) input.value = editor.text;
  byId('voice-count').textContent = `${editor.count} / ${editor.maxChars}`;
  const tooLong = editor.count > editor.maxChars;
  input.setAttribute('aria-invalid', String(tooLong));
  byId('voice-limit').textContent = editor.count === 0
    ? t('Добавь текст, чтобы сохранить.')
    : tooLong ? t('Сократи текст ещё на {count} симв.', { count: editor.count - editor.maxChars }) : t('До 2000 символов');
  byId('voice-limit').parentElement.classList.toggle('is-invalid', tooLong);
  saveButton.disabled = !editor.canSave;
  saveButton.firstChild.textContent = t(editor.saving ? 'Сохраняем…' : 'Сохранить текст');
  byId('voice-save-error').hidden = !editor.loaded || !editor.error;
  byId('voice-save-error').textContent = editor.error ? t(errorMessages[editor.error] || errorMessages.unavailable) : '';
  byId('voice-save-status').textContent = editor.saving
    ? t('Сохраняем текст…')
    : editor.notificationSent !== null && editor.dirty
      ? t('Предыдущая правка сохранена. Сохрани новые изменения, когда закончишь.') : '';
  byId('voice-saved-message').textContent = demo ? t('Демо: текст сохранён только здесь. Карточка в Telegram не изменялась.') : editor.notificationSent === false
    ? t('Правка сохранена, но карточку в чате обновить не удалось. Вернись в чат и отправь /voice.')
    : t('Карточка в чате обновлена. Вернись к ней и нажми «Отправить».');
  syncClosingConfirmation();
  if (editor.saved && !savedFocus) {
    byId('voice-saved').focus();
    savedFocus = true;
  }
}

function showLoadError(code) {
  byId('voice-loading').hidden = true;
  byId('voice-load-error').hidden = false;
  const messages = {
    timeout: 'Не удалось дождаться текста. Попробуй открыть его ещё раз.',
    network: 'Не удалось загрузить текст. Проверь соединение и попробуй ещё раз.',
    unavailable: 'Текст пока недоступен. Попробуй ещё раз немного позже.',
    'invalid-response': 'Не удалось загрузить текст. Попробуй ещё раз.',
    conflict: 'Текст уже отправлен или время правки истекло. Вернись в чат и отправь /voice.',
  };
  byId('voice-load-error-message').textContent = t(messages[code] || errorMessages[code] || messages.unavailable);
  byId('voice-retry').hidden = !['network', 'timeout', 'unavailable', 'invalid-response'].includes(code);
  byId('main-content').setAttribute('aria-busy', 'false');
}

async function load() {
  byId('voice-load-error').hidden = true;
  const pending = editor.load(async () => {
    if (demo) return { job_id: 1, revision: 0, text: 'Dnes jsem šel do obchodu.', max_chars: 2000 };
    const settings = await loadProfile(telegram.initData);
    setLanguage(settings.interface_language);
    return loadVoiceDraft(jobId, telegram.initData);
  });
  render();
  await pending;
  render();
  if (!editor.loaded) showLoadError(editor.error);
  revealMiniApp();
}

function requestClose() {
  if (confirmOpen) return;
  const close = () => {
    closeApproved = true;
    try {
      if (closingConfirmation) telegram?.disableClosingConfirmation?.();
    } catch {
      // The explicit confirmation already authorized discarding local edits.
    }
    telegram?.close();
  };
  if (!editor.dirty && !editor.saving) {
    close();
    return;
  }
  const message = t(editor.saving ? 'Сохранение ещё выполняется. Вернуться в чат?' : 'Выйти без сохранения изменений?');
  if (typeof telegram?.showConfirm === 'function') {
    try {
      confirmOpen = true;
      telegram.showConfirm(message, confirmed => {
        confirmOpen = false;
        if (confirmed) close();
      });
      return;
    } catch {
      confirmOpen = false;
    }
  }
  if (window.confirm(message)) close();
}

// The shared header closes immediately; guard its event before that listener runs.
byId('close-button').addEventListener('click', event => {
  event.stopImmediatePropagation();
  requestClose();
}, true);
byId('voice-cancel').addEventListener('click', requestClose);
byId('voice-go-chat').addEventListener('click', requestClose);
byId('voice-retry').addEventListener('click', load);
input.addEventListener('input', () => {
  editor.edit(input.value);
  render();
});
byId('voice-editor').addEventListener('submit', async event => {
  event.preventDefault();
  const pending = editor.save((text, revision) => demo
    ? Promise.resolve({ job_id: 1, text, revision: revision + 1, max_chars: 2000, notification_sent: true })
    : saveVoiceDraft(jobId, text, revision, telegram.initData));
  render();
  await pending;
  render();
});
window.addEventListener('beforeunload', event => {
  if (!closeApproved && (editor.dirty || editor.saving)) {
    event.preventDefault();
    event.returnValue = '';
  }
});

const stopLocaleUpdates = onLanguageChange(() => { if (editor.loaded) render(); });
window.addEventListener('pagehide', stopLocaleUpdates);

if (demo) {
  byId('voice-demo-notice').hidden = false;
  void load();
} else if (!telegram?.initData?.trim()) {
  byId('voice-loading').hidden = true;
  byId('voice-unavailable').hidden = false;
  byId('main-content').setAttribute('aria-busy', 'false');
  revealMiniApp();
} else {
  try {
    jobId = parseVoiceJobId(window.location.search);
    void load();
  } catch {
    showLoadError('invalid-job');
    revealMiniApp();
  }
}
