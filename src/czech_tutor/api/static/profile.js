import { initializeLocalization, setLanguage, t, onLanguageChange } from './localization.mjs';
import { initializeMiniApp, revealMiniApp } from './mini-app-ui.mjs';
import { createProfilePreferences } from './profile-state.mjs';
import { loadProfile, updateAnswerLength, updateLanguageLevel, updateResponseMode, updateInterfaceLanguage } from './profile-client.mjs';

initializeLocalization();
const byId = id => document.getElementById(id);
const params = new URLSearchParams(window.location.search);
const demo = params.get('demo') === '1';
const previewState = demo ? params.get('state') : null;
let preferences;
function setPreferences(settings = {}) {
  const profile = createProfilePreferences(settings);
  preferences = { level: profile.languageLevel, length: profile.answerLength, mode: profile.responseMode, locale: profile.interfaceLanguage };
}
setPreferences(demo ? { interface_language: params.get('lang') === 'uk' ? 'uk' : 'ru' } : {});
const radios = {
  level: [...document.querySelectorAll('input[name="language_level"]')],
  length: [...document.querySelectorAll('input[name="answer_length"]')],
  locale: [...document.querySelectorAll('input[name="interface_language"]')],
};
const levelHints = {
  A1: 'В разговоре — простые слова, короткие фразы и знакомые темы.',
  A2: 'В разговоре — повседневные ситуации: покупки, встречи, работа и отдых.',
  B1: 'В разговоре — истории, планы и обсуждения с понятными объяснениями.',
  B2: 'В разговоре — развёрнутые ответы, аргументы и более разнообразная речь.',
  C1: 'В разговоре — сложные темы, точные формулировки и оттенки смысла.',
  C2: 'В разговоре — свободное обсуждение, стилистика и тонкости формулировок.',
};
const lengthHints = {
  short: 'Коротко и по делу — удобно для живого диалога.',
  medium: 'Чуть больше деталей и примеров, чтобы поддержать разговор.',
  long: 'Развёрнутые ответы с подробностями и пояснениями.',
};
const lengthNames = { short: 'короткие', medium: 'средние', long: 'длинные' };
const failNextSave = {
  level: ['save-error', 'level-save-error'].includes(previewState),
  length: previewState === 'length-save-error',
  locale: previewState === 'locale-save-error',
};
let loading = false;
let ready = false;
let menuUpdated;

const telegram = initializeMiniApp();
byId('profile-demo-link').href = '?demo=1';
const textResponseToggle = byId('text-response-toggle');
textResponseToggle.addEventListener('change', () => {
  selectPreference('mode', textResponseToggle.checked ? 'text' : 'voice');
});

function renderPreference(key) {
  const preference = preferences[key];
  if (key === 'locale') {
    radios.locale.forEach(radio => { radio.checked = radio.value === preference.selectedValue; radio.disabled = preference.saving; });
    byId('locale-fieldset').setAttribute('aria-busy', String(preference.saving));
    byId('locale-save-error').hidden = !preference.error;
    byId('locale-status').textContent = preference.saving ? t('Сохраняем…')
      : preference.error ? '' : t(demo ? 'Язык выбран в демо.' : menuUpdated === false
        ? 'Язык сохранён. Если меню Telegram осталось прежним, открой чат заново.' : 'Язык сохранён.');
    return;
  }
  if (key === 'mode') {
    textResponseToggle.checked = preference.selectedValue === 'text';
    textResponseToggle.disabled = preference.saving;
    byId('response-mode-status').textContent = preference.saving ? t('Сохраняем…')
      : preference.error ? t('Сохранение не подтверждено. Повтори попытку.')
      : t(demo ? preference.savedValue === 'text' ? 'В демо выбран текстовый ответ.' : 'В демо выбран голосовой ответ.' : preference.savedValue === 'text' ? 'Сохранён текстовый ответ.' : 'Сохранён голосовой ответ.');
    byId('mode-save-error').hidden = !preference.error;
    return;
  }
  radios[key].forEach(radio => {
    radio.checked = radio.value === preference.selectedValue;
    radio.setAttribute('aria-disabled', String(preference.saving));
  });
  byId(`${key}-fieldset`).setAttribute('aria-busy', String(preference.saving));
  byId(`${key}-save-error`).hidden = !preference.error;
  const status = byId(`${key}-status`);
  status.classList.toggle('is-confirmed', preference.confirmed);
  if (preference.saving) {
    status.textContent = t(demo ? 'Применяем выбор в демо…' : 'Сохраняем…');
  } else if (preference.error) {
    status.textContent = t(demo ? 'Выбор пока не применён.' : 'Сохранение не подтверждено.');
  } else if (key === 'level') {
    status.textContent = preference.savedValue
      ? t(demo ? 'В демо выбран уровень {level}.' : 'Сохранён уровень {level}.', { level: preference.savedValue })
      : t('Нажми на уровень — он выберется сразу.');
  } else {
    status.textContent = t(demo ? 'В демо выбраны {length} ответы.' : 'Сохранены {length} ответы.', { length: t(lengthNames[preference.savedValue]) });
  }
  if (key === 'level') {
    byId('current-level').textContent = preference.savedValue ? t('Уровень {level}', { level: preference.savedValue }) : t('Уровень не выбран');
    byId('current-level').classList.toggle('has-level', Boolean(preference.savedValue));
    if (preference.selectedValue) byId('level-hint').textContent = t(levelHints[preference.selectedValue]);
  } else {
    byId('length-hint').textContent = t(lengthHints[preference.selectedValue]);
  }
}

async function selectPreference(key, value) {
  if (!ready) return;
  const pending = preferences[key].select(value, async selected => {
    if (demo) {
      await new Promise(resolve => setTimeout(resolve, 500));
      if (failNextSave[key]) {
        failNextSave[key] = false;
        throw new Error('Synthetic profile preference save failure');
      }
    } else {
      const save = { level: updateLanguageLevel, length: updateAnswerLength, mode: updateResponseMode, locale: updateInterfaceLanguage }[key];
      const result = await save(selected, telegram.initData);
      if (key === 'locale') menuUpdated = result.menu_updated;
    }
  });
  if (key === 'locale') setLanguage(preferences.locale.selectedValue);
  renderPreference(key);
  await pending;
  if (key === 'locale') setLanguage(preferences.locale.selectedValue);
  renderPreference(key);
}

async function showProfile({ showError = false, keepLoading = false } = {}) {
  if (loading) return;
  loading = true;
  ready = false;
  byId('profile-loading').hidden = false;
  byId('profile-content').hidden = true;
  byId('profile-unavailable').hidden = true;
  byId('profile-load-error').hidden = true;
  byId('main-content').setAttribute('aria-busy', 'true');
  try {
    if (demo) {
      await new Promise(resolve => setTimeout(resolve, 350));
      if (keepLoading) return;
      if (showError) throw new Error('Synthetic profile load failure');
    } else {
      if (!telegram?.initData) {
        byId('profile-unavailable').hidden = false;
        return;
      }
      const settings = await loadProfile(telegram.initData);
      setPreferences(settings);
      setLanguage(settings.interface_language);
    }
    byId('profile-demo-notice').hidden = !demo;
    byId('profile-live-notice').hidden = demo;
    byId('profile-content').hidden = false;
    Object.keys(preferences).forEach(renderPreference);
    ready = true;
  } catch (error) {
    const messages = {
      unauthorized: 'Сессия истекла или доступ недоступен. Закрой мини-приложение и открой его снова из Telegram.',
      'not-found': 'Твой профиль пока не найден. Вернись в чат с ботом.',
    };
    byId('profile-load-error-message').textContent = t(messages[error.code]
      || 'Не удалось загрузить настройки. Попробуй ещё раз немного позже.');
    byId('profile-load-error').hidden = false;
  } finally {
    if (!(demo && keepLoading)) {
      byId('profile-loading').hidden = true;
      byId('main-content').setAttribute('aria-busy', 'false');
    }
    loading = false;
    revealMiniApp();
  }
}

Object.keys(radios).forEach(key => {
  radios[key].forEach(radio => {
    radio.addEventListener('change', () => selectPreference(key, radio.value));
    radio.addEventListener('click', event => {
      if (preferences[key].saving) event.preventDefault();
    });
    radio.addEventListener('keydown', event => {
      if (preferences[key].saving && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ' ', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
      }
    });
  });
  if (key !== 'locale') byId(`${key}-retry`).addEventListener('click', () => {
    selectPreference(key, preferences[key].selectedValue);
  });
});

byId('mode-retry').addEventListener('click', () => selectPreference('mode', preferences.mode.selectedValue));

const stopLocaleUpdates = onLanguageChange(() => Object.keys(preferences).forEach(renderPreference));
window.addEventListener('pagehide', stopLocaleUpdates);
byId('profile-retry').addEventListener('click', () => showProfile());
showProfile({ showError: previewState === 'error', keepLoading: previewState === 'loading' });
