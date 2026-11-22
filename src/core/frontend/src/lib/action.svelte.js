// Runs a user action and keeps what the person should see: a busy flag, an error to correct, or a success note.
export class Action {
  error = $state("");
  ok = $state("");
  busy = $state(false);

  async run(fn, okMessage = "") {
    this.error = "";
    this.ok = "";
    this.busy = true;
    try {
      const result = await fn();
      if (okMessage) this.ok = okMessage;
      return result;
    } catch (e) {
      this.error = e.message;
    } finally {
      this.busy = false;
    }
  }
}
