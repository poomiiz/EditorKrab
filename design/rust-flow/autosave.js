// IndexedDB owns the durable outbox; only Rust acknowledgements mean "saved".
export class DraftSession {
  constructor(project, initial, changed, validDocument = d => d && typeof d === 'object') {
    this.project = project; this.initial = initial; this.changed = changed;
    this.serial = Promise.resolve(); this.record = null; this.blocked = false;
    this.volatile = false; this.pumping = false; this.closed = false;
    this.validDocument = validDocument;
  }

  validRecord(record) {
    const integer = value => Number.isSafeInteger(value) && value >= 0;
    return record?.id === this.project && integer(record.generation) && integer(record.revision) &&
      this.validDocument(record.document) && Array.isArray(record.undo) && Array.isArray(record.redo) &&
      record.undo.every(this.validDocument) && record.redo.every(this.validDocument) &&
      Array.isArray(record.pending) && record.pending.every((op, index) =>
        op && typeof op.operation_id === 'string' && op.operation_id.length > 0 &&
        op.expected_revision === record.revision + index && this.validDocument(op.document)) &&
      new Set(record.pending.map(op => op.operation_id)).size === record.pending.length;
  }

  async open() {
    this.db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('editorkrab-drafts-v1', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts', {keyPath:'id'});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(Error('draft_database_blocked'));
    });
    this.db.onversionchange = () => { this.blocked = true; this.db.close(); this.notify('conflict'); };
    this.record = await this.transaction(current => {
      const record = current || {
        id:this.project, generation:0, revision:0,
        document:structuredClone(typeof this.initial === 'function' ? this.initial() : this.initial),
        pending:[], undo:[], redo:[],
      };
      if (!this.validRecord(record)) throw Error('invalid_local_draft');
      this.created = !current;
      return record;
    });
    this.generation = this.record.generation;
    if (!this.record.pending.length) await this.refresh();
    if (this.blocked) throw Error('draft_conflict');
    this.notify(this.record.pending.length ? 'pending' : 'ready');
    this.timer = setInterval(() => this.flush(), 3000);
    this.flush();
    return structuredClone(this.record.document);
  }

  transaction(change) {
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction('drafts', 'readwrite');
      const table = tx.objectStore('drafts');
      let result, failure;
      const request = table.get(this.project);
      request.onsuccess = () => {
        try { result = change(request.result); table.put(result); }
        catch (error) { failure = error; tx.abort(); }
      };
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(failure || tx.error || Error('draft_transaction_aborted'));
      tx.onerror = () => { /* onabort supplies the transaction result */ };
    });
  }

  enqueue(change) {
    const next = this.serial.then(async () => {
      if (this.blocked || this.closed) throw Error('draft_conflict');
      try {
        this.record = await this.transaction(current => {
          if (!current || current.generation !== this.generation) throw Error('draft_conflict');
          const result = change(structuredClone(current));
          result.generation++;
          if (!this.validRecord(result)) throw Error('invalid_local_draft');
          return result;
        });
        this.generation = this.record.generation; this.volatile = false;
        return structuredClone(this.record.document);
      } catch (error) {
        if (error.message === 'draft_conflict') { this.blocked = true; this.notify('conflict'); }
        else { this.volatile = true; this.notify('storage_error'); }
        throw error;
      }
    });
    this.serial = next.catch(() => {});
    return next;
  }

  notify(state) {
    this.changed({state, revision:this.record?.revision || 0,
      pending:this.record?.pending.length || 0, undo:!!this.record?.undo.length,
      redo:!!this.record?.redo.length});
  }

  operation(record, document) {
    record.pending.push({operation_id:crypto.randomUUID(),
      expected_revision:record.revision + record.pending.length,
      document:structuredClone(document)});
    record.document = structuredClone(document);
  }

  async edit(document) {
    const copy = structuredClone(document);
    const result = await this.enqueue(record => {
      // UI undo is bounded; server operation snapshots retain acknowledged revisions.
      record.undo.push(record.document); record.undo = record.undo.slice(-100); record.redo = [];
      this.operation(record, copy); return record;
    });
    this.notify('pending'); this.flush(); return result;
  }

  async history(direction) {
    const result = await this.enqueue(record => {
      const from = direction === 'undo' ? record.undo : record.redo;
      const to = direction === 'undo' ? record.redo : record.undo;
      if (from.length) { to.push(record.document); this.operation(record, from.pop()); }
      return record;
    });
    this.notify('pending'); this.flush(); return result;
  }

  async refresh() {
    try {
      const response = await fetch('/api/v1/drafts/' + encodeURIComponent(this.project),
        {cache:'no-store', signal:AbortSignal.timeout(10000)});
      if (response.status === 404) {
        if (this.record.revision > 0) { this.blocked = true; this.notify('conflict'); }
        return;
      }
      if (!response.ok) throw Error('draft_read_failed');
      const remote = await response.json();
      if (remote.project_id !== this.project || !Number.isSafeInteger(remote.revision) ||
        remote.revision < 1 || !this.validDocument(remote.document)) {
        this.blocked = true; this.notify('conflict'); throw Error('invalid_ack');
      }
      if (remote.revision < this.record.revision) { this.blocked = true; this.notify('conflict'); return; }
      if (remote.revision > this.record.revision) {
        await this.enqueue(record => {
          if (record.pending.length) throw Error('draft_conflict');
          record.document = remote.document; record.revision = remote.revision; return record;
        });
      }
    } catch (error) {
      if (this.blocked) throw error;
      this.offline = true;
    }
  }

  async flush() {
    if (this.pumping || this.closed || this.blocked) return;
    this.pumping = true;
    try {
      await this.serial;
      while (this.record.pending.length && !this.blocked && !this.closed) {
        const operation = structuredClone(this.record.pending[0]);
        let response;
        try {
          response = await fetch('/api/v1/drafts/' + encodeURIComponent(this.project), {
            method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(operation),
            signal:AbortSignal.timeout(10000),
          });
        } catch (_) { this.notify('offline'); return; }
        if (response.status === 409) { this.blocked = true; this.notify('conflict'); return; }
        if (!response.ok) { this.notify(response.status >= 500 ? 'offline' : 'rejected'); return; }
        const ack = await response.json();
        if (ack.project_id !== this.project || ack.revision !== operation.expected_revision + 1) {
          this.blocked = true; this.notify('conflict'); return;
        }
        await this.enqueue(record => {
          if (record.pending[0]?.operation_id !== operation.operation_id) throw Error('draft_conflict');
          record.pending.shift(); record.revision = ack.revision; return record;
        });
      }
      if (!this.blocked && !this.closed && !this.volatile) {
        this.notify(this.record.revision > 0 ? 'saved' : 'ready');
      }
    } catch (_) { if (!this.blocked && !this.volatile) this.notify('offline'); }
    finally { this.pumping = false; }
  }

  close() { this.closed = true; clearInterval(this.timer); this.db?.close(); }
}
