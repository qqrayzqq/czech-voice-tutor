import { parseReplyId } from './feedback-client.mjs';

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
    themeButton.setAttribute('aria-label', theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему');
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
  applyTheme();
  return telegram;
}
