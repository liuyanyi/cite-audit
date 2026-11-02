// Native fetch transport for API-backed jsdom tests. Production uses the
// browser's EventSource; this adapter has no simulated server messages.
export class ApiEvents extends EventTarget {
  static base = '';
  static instances: ApiEvents[] = [];
  closed = false;
  kinds: string[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private controller = new AbortController();
  constructor(public url: string) {
    super(); ApiEvents.instances.push(this);
    void this.read();
  }
  close() { this.closed = true; this.controller.abort(); }
  private async read() {
    try {
      const response = await fetch(new URL(this.url, ApiEvents.base), { signal: this.controller.signal });
      if (!response.ok || !response.body) throw new Error(`SSE HTTP ${response.status}`);
      this.onopen?.();
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (!this.closed) {
          const result = await reader.read();
          if (result.done) break;
          buffer += decoder.decode(result.value, { stream: true });
          let end: number;
          while (!this.closed && (end = buffer.indexOf('\n\n')) >= 0) {
            const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            const fields = block.split('\n');
            const kind = fields.find(line => line.startsWith('event: '))?.slice(7);
            if (!kind) continue;
            this.kinds.push(kind);
            this.dispatchEvent(new MessageEvent(kind, {
              data: fields.find(line => line.startsWith('data: '))?.slice(6),
              lastEventId: fields.find(line => line.startsWith('id: '))?.slice(4),
            }));
          }
        }
      } finally { await reader.cancel().catch(() => {}); }
    } catch { if (!this.closed) this.onerror?.(); }
  }
}
