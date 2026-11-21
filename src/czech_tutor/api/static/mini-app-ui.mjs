import { t, onLanguageChange } from './localization.mjs';
import { parseReplyId } from './feedback-client.mjs';

export function initialFeedbackTab(search, feedback) {
  const tab = new URLSearchParams(search).get('tab');
  if (tab === 'reply') return 1;
  if (tab === 'corrections') return 0;
  if (!feedback) return 0;
  if (feedback.feedback_status && feedback.feedback_status !== 'evaluated') return 1;
  return feedback.corrections?.length ? 0 : 1;
}

export function buildMiniAppLink(page, search) {
  const params = new URLSearchParams();
  try {
    params.set('reply_id', String(parseReplyId(search)));
  } catch {
    // The profile is also available without a feedback message.
  }
  if (new URLSearchParams(search).get('demo') === '1') params.set('demo', '1');
  const query = params.toString();
  return `./${page}${query ? `?${query}` : ''}`;
}

export function initializeMiniApp() {
  const telegram = window.Telegram?.WebApp;
  const themeButton = document.getElementById('theme-toggle');
  const closeButton = document.getElementById('close-button');
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  let manualTheme = null;
  function applyTheme() {
    const theme = manualTheme || telegram?.colorScheme || (systemTheme.matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = theme;
    themeButton.setAttribute('aria-label', t(theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'));
  }
  themeButton.addEventListener('click', () => {
    manualTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    applyTheme();
  });
  closeButton.hidden = !telegram?.initData;
  closeButton.addEventListener('click', () => telegram?.close());
  if (telegram) {
    telegram.ready();
    if (telegram.initData) telegram.expand();
    telegram.onEvent('themeChanged', applyTheme);
  }
  systemTheme.addEventListener('change', applyTheme);
  onLanguageChange(applyTheme);
  applyTheme();
  return telegram;
}
