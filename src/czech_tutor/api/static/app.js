import { FeedbackError, loadFeedback, parseReplyId, feedbackPresentation, feedbackHeading } from './feedback-client.mjs';
import { initializeLocalization, setLanguage, getLanguage, t } from './localization.mjs';
import { loadProfile } from './profile-client.mjs';
import { initializeMiniApp, initialFeedbackTab } from './mini-app-ui.mjs';

initializeLocalization();
const byId = id => document.getElementById(id);
const telegram = initializeMiniApp();
const demo = new URLSearchParams(window.location.search).get('demo') === '1';
const tabs = [byId('corrections-tab'), byId('reply-tab')];
const panels = [byId('corrections-panel'), byId('reply-panel')];
let pending = false;
let currentFeedback = null;

const demoFeedback = {
  reply_id: 1,
  reply_cs: 'Jablečný koláč zní skvěle! Pečeš často, nebo to bude tvůj první pokus?',
  note_ru: 'Предлог do отвечает на вопрос «куда?» и требует родительного падежа. После s в значении «вместе с» нужен творительный.',
  corrections: [
    { original: 'do obchodě', corrected: 'do obchodu', explanation_ru: 'После предлога do нужен родительный падеж: do obchodu. Форма obchodě — местный падеж: v obchodě («в магазине»).' },
    { original: 's můj kamarád', corrected: 's mým kamarádem', explanation_ru: '«С кем?» — творительный падеж. Меняются оба слова: můj → mým, kamarád → kamarádem.' },
    { original: 'tři jablko', corrected: 'tři jablka', explanation_ru: 'После tři здесь нужна форма множественного числа: jablka. В сочетании koupil jsem tři jablka это винительный падеж, совпадающий по форме с именительным.' },
  ],
};

function activateTab(index, focus = false) {
  tabs.forEach((tab, position) => {
    const selected = index === position;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    panels[position].hidden = !selected;
  });
  if (currentFeedback) {
    const heading = feedbackHeading(currentFeedback, index);
    byId('page-title').firstChild.textContent = t(heading.title);
    byId('page-description').textContent = t(heading.description);
    document.title = `${t(heading.title)} · Czech Voice Tutor`;
  }
  if (focus) tabs[index].focus();
}

function renderFeedback(feedback) {
  currentFeedback = feedback;
  const { status, errors, advice, showNote } = feedbackPresentation(feedback);
  function renderCards(items, id, isAdvice) {
    const list = byId(id);
    list.replaceChildren();
    items.forEach((correction, index) => {
      const card = byId('correction-template').content.cloneNode(true);
      card.querySelector('.correction-card').classList.toggle('advice-card', isAdvice);
      card.querySelector('.correction-number').textContent = String(index + 1).padStart(2, '0');
      card.querySelector('.correction-heading span:nth-child(2)').textContent = t(isAdvice ? 'Совет · не ошибка' : 'Точнее будет так');
      card.querySelector('.original .phrase-label').textContent = t('БЫЛО');
      card.querySelector('.corrected .phrase-label').textContent = t('ЛУЧШЕ');
      card.querySelector('.explanation-label').textContent = t('Почему так');
      card.querySelector('.original p').textContent = correction.original;
      card.querySelector('.corrected p').textContent = correction.corrected;
      card.querySelector('.explanation p').textContent = correction.explanation_ru;
      card.querySelector('.explanation p').lang = feedback.feedback_language || 'ru';
      list.append(card);
    });
  }
  renderCards(errors, 'correction-list', false);
  renderCards(advice, 'advice-list', true);
  byId('advice-section').hidden = !advice.length;
  byId('corrections-count').textContent = errors.length;
  byId('corrections-caption').textContent = errors.length ? t('БЫЛО → ЛУЧШЕ') : '';
  const emptyStates = {
    evaluated: ['Ошибок нет', advice.length ? 'Есть совет, как сказать естественнее.' : 'Продолжай разговор — практика помогает говорить свободнее.'],
    not_evaluated: ['Не оценивалось', 'В этом ответе нет оценки правильности твоей фразы.'],
    clarification: ['Нужно уточнение', 'Бот уточняет смысл. Эта фраза пока не оценивалась.'],
    superseded: ['Оценка отозвана', 'Прежние исправления скрыты: распознавание или смысл фразы были уточнены.'],
  };
  const [title, description] = emptyStates[status];
  byId('empty-title').textContent = t(title);
  byId('empty-description').textContent = t(description);
  byId('empty-state').hidden = errors.length !== 0;
  byId('empty-state').classList.toggle('unassessed', status !== 'evaluated');
  byId('empty-state').querySelector('.success-mark').hidden = status !== 'evaluated';
  byId('reply-text').textContent = feedback.reply_cs;
  byId('note-text').textContent = feedback.note_ru;
  byId('note-text').lang = feedback.feedback_language || 'ru';
  byId('note-card').hidden = !showNote;
  const explanationLanguage = feedback.feedback_language || 'ru';
  byId('explanation-language').textContent = t(explanationLanguage === 'uk'
    ? 'Язык сохранённого объяснения: украинский' : 'Язык сохранённого объяснения: русский');
  byId('explanation-language').hidden = explanationLanguage === getLanguage()
    || !(errors.length || advice.length || showNote);
  byId('feedback').hidden = false;
  activateTab(initialFeedbackTab(window.location.search, feedback));
}

const errors = {
  'missing-auth': ['Открой разбор из Telegram', 'Нажми кнопку разбора под ответом бота. Так мы сможем показать именно твои исправления.'],
  unauthorized: ['Нужно открыть разбор заново', 'Доступ не подтверждён или время сессии истекло. Закрой это окно и открой разбор ещё раз из чата с ботом.'],
  'invalid-id': ['В ссылке не хватает ответа', 'Открой разбор по кнопке под нужным сообщением бота.'],
  'not-found': ['Этот разбор недоступен', 'Ответ не найден или принадлежит другому пользователю. Вернись в чат и выбери своё сообщение.'],
  unavailable: ['Разбор пока недоступен', 'Не удалось загрузить исправления. Попробуй ещё раз немного позже.'],
  network: ['Не удалось подключиться', 'Проверь интернет и попробуй загрузить разбор ещё раз.'],
  timeout: ['Загрузка заняла больше времени', 'Сервер не успел ответить. Попробуй ещё раз через несколько секунд.'],
  'invalid-response': ['Не удалось прочитать разбор', 'Ответ сервера пришёл в неожиданном формате. Попробуй загрузить его ещё раз.'],
};

function renderError(error) {
  const code = error instanceof FeedbackError ? error.code : 'unavailable';
  const [title, description] = errors[code] || errors.unavailable;
  byId('error-title').textContent = t(title);
  byId('error-description').textContent = t(description);
  byId('retry-button').hidden = !['unavailable', 'network', 'timeout', 'invalid-response'].includes(code);
  byId('demo-link').hidden = code !== 'missing-auth' || Boolean(telegram?.initData);
  byId('error-state').hidden = false;
}

async function start() {
  if (pending) return;
  pending = true;
  byId('feedback').hidden = true;
  byId('error-state').hidden = true;
  byId('loading-state').hidden = false;
  byId('main-content').setAttribute('aria-busy', 'true');
  try {
    if (demo) {
      const state = new URLSearchParams(window.location.search).get('state');
      const data = structuredClone(demoFeedback);
      data.feedback_language = 'ru';
      data.feedback_status = ['clarification', 'not_evaluated', 'superseded'].includes(state) ? state : 'evaluated';
      if (state === 'clean') { data.corrections = []; data.note_ru = ''; }
      if (state === 'advice') {
        data.corrections = [{ original: 'To je moc dobré.', corrected: 'To je výborné.',
          explanation_ru: 'Оба варианта правильные; výborné — более выразительный вариант.', kind: 'advice' }];
        data.note_ru = '';
      }
      renderFeedback(data);
    } else {
      if (!telegram?.initData) throw new FeedbackError('missing-auth');
      const replyId = parseReplyId(window.location.search);
      const profile = await loadProfile(telegram.initData);
      setLanguage(profile.interface_language);
      renderFeedback(await loadFeedback(replyId, telegram.initData));
    }
  } catch (error) {
    renderError(error);
  } finally {
    pending = false;
    byId('loading-state').hidden = true;
    byId('main-content').setAttribute('aria-busy', 'false');
  }
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(index));
  tab.addEventListener('keydown', event => {
    const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    activateTab(target, true);
  });
});
byId('retry-button').addEventListener('click', start);
byId('demo-badge').hidden = !demo;
start();
