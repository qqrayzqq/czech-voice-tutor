export function countVoiceCharacters(text) {
  return Array.from(text.trim()).length;
}

export function createVoiceEditor() {
  let editVersion = 0;
  return {
    text: '',
    savedText: null,
    revision: null,
    maxChars: 2000,
    loaded: false,
    loading: false,
    saving: false,
    saved: false,
    error: null,
    notificationSent: null,
    get count() { return countVoiceCharacters(this.text); },
    get dirty() { return this.loaded && this.text !== this.savedText; },
    get canSave() {
      return this.loaded && !this.loading && !this.saving && !this.saved
        && this.count > 0 && this.count <= this.maxChars;
    },
    edit(text) {
      this.text = text;
      editVersion += 1;
      this.saved = false;
    },
    async load(fetchDraft) {
      if (this.loading || this.loaded) return false;
      this.loading = true;
      this.error = null;
      const startedAt = editVersion;
      try {
        const data = await fetchDraft();
        this.savedText = data.text;
        this.revision = data.revision;
        this.maxChars = data.max_chars;
        if (startedAt === editVersion) this.text = data.text;
        this.loaded = true;
        return true;
      } catch (error) {
        this.error = error?.code || 'unavailable';
        return false;
      } finally {
        this.loading = false;
      }
    },
    async save(persist) {
      if (!this.canSave) return false;
      const text = this.text.trim();
      const revision = this.revision;
      const startedAt = editVersion;
      this.saving = true;
      this.error = null;
      try {
        const data = await persist(text, revision);
        this.savedText = data.text;
        this.revision = data.revision;
        this.notificationSent = data.notification_sent;
        if (startedAt === editVersion) this.text = data.text;
        this.saved = this.text === data.text;
        return true;
      } catch (error) {
        this.error = error?.code || 'unavailable';
        return false;
      } finally {
        this.saving = false;
      }
    },
  };
}
