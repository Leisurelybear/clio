/** Keep task identity, ordered snapshots, and terminal handling separate from DOM state. */
const ACTIVE = new Set(['queued', 'running', 'cancelling']);
const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

export class RunTaskSession {
  constructor() {
    this.project = '';
    this.task = null;
    this.seq = 0;
    this.finalized = new Set();
  }

  resetProject(project) {
    if (this.project === project) return false;
    this.project = project;
    this.task = null;
    this.seq = 0;
    this.finalized.clear();
    return true;
  }

  accept(task, { seq = 0, attach = false } = {}) {
    if (!task || task.kind !== 'pipeline') return false;
    if (seq && seq <= this.seq) return false;
    if (this.project && task.project_id && task.project_id !== this.project) return false;
    if (this.finalized.has(task.id)) return false;
    if (this.task?.id !== task.id) {
      // The stream joins events to current snapshots, so a fast retry can be
      // terminal by the time its first event reaches this page.
      const followsCurrent = this.task && task.retry_of === this.task.id;
      if (!attach && !ACTIVE.has(task.status) && !followsCurrent) return false;
      if (!attach && this.task && ACTIVE.has(this.task.status)
        && task.retry_of !== this.task.id
        && (!task.created_at || task.created_at <= (this.task.created_at || ''))) return false;
      if (this.task?.created_at && task.created_at && task.created_at < this.task.created_at) return false;
    } else {
      if (TERMINAL.has(this.task.status) && !TERMINAL.has(task.status)) return false;
      if (!seq && task.updated_at && this.task.updated_at && task.updated_at < this.task.updated_at) return false;
    }
    this.task = task;
    this.seq = Math.max(this.seq, Number(seq) || 0);
    return true;
  }

  finalize(id) {
    if (!id) return true;
    if (this.finalized.has(id)) return false;
    this.finalized.add(id);
    if (this.finalized.size > 200) this.finalized.delete(this.finalized.values().next().value);
    return true;
  }
}
