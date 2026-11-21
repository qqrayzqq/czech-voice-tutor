export const LANGUAGE_LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
export const ANSWER_LENGTHS = ['short', 'medium', 'long'];
export const INTERFACE_LANGUAGES = ['ru', 'uk'];
export const RESPONSE_MODES = ['voice', 'text'];

export function createProfilePreferences({ language_level = null, answer_length = 'short', response_mode = 'voice', interface_language = 'ru' } = {}) {
  if ((language_level !== null && !LANGUAGE_LEVELS.includes(language_level))
    || !ANSWER_LENGTHS.includes(answer_length) || !RESPONSE_MODES.includes(response_mode) || !INTERFACE_LANGUAGES.includes(interface_language)) {
    throw new Error('Invalid profile preference');
  }
  return {
    languageLevel: createPreference(language_level, LANGUAGE_LEVELS),
    answerLength: createPreference(answer_length, ANSWER_LENGTHS),
    responseMode: createPreference(response_mode, RESPONSE_MODES),
    interfaceLanguage: createPreference(interface_language, INTERFACE_LANGUAGES, true),
  };
}

function createPreference(initialValue, allowedValues, rollback = false) {
  return {
    savedValue: initialValue,
    selectedValue: initialValue,
    saving: false,
    error: false,
    confirmed: false,
    async select(value, persist) {
      if (!allowedValues.includes(value)) throw new Error('Invalid preference value');
      if (this.saving) return false;
      // A failed response may follow a successful commit; explicit selection must resave.
      if (value === this.savedValue && !this.error) {
        this.selectedValue = value;
        this.error = false;
        this.confirmed = false;
        return false;
      }
      this.selectedValue = value;
      this.saving = true;
      this.error = false;
      this.confirmed = false;
      try {
        await persist(value);
        this.savedValue = value;
        this.confirmed = true;
        return true;
      } catch {
        this.error = true;
        if (rollback) this.selectedValue = this.savedValue;
        return false;
      } finally {
        this.saving = false;
      }
    },
  };
}
