/**
 * CopilotPanel — the persistent right-dock desk agent.
 *
 * Selection-aware by construction: whatever symbol/strategy the workspace has
 * selected is shown as a context header AND sent with the request, so "why was
 * this rejected?" resolves against what the user is actually looking at.
 *
 * The backend's /chat/ contract has drifted across builds, so the response
 * reader accepts every shape it has ever returned rather than assuming one.
 * Anything it cannot parse is surfaced verbatim instead of being swallowed —
 * a silent empty bubble is worse than an ugly one.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { apiBase } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  Icon,
  Panel,
  PanelBody,
  PanelHeader,
  Skeleton,
  cx,
  fmtTime,
} from '../../ui';

/** Canonical questions from the brief — one tap instead of typing. */
const PROMPTS = [
  'Why was this trade rejected?',
  "Explain today's drawdown.",
  'Optimize my strategy.',
  'Backtest this.',
  'Show similar historical situations.',
];

/** Pull assistant prose out of whichever envelope the backend used. */
function readReply(payload) {
  if (payload == null) return '';
  if (typeof payload === 'string') return payload;
  const direct =
    payload.reply ?? payload.response ?? payload.message ?? payload.answer ?? payload.text ?? payload.content;
  if (typeof direct === 'string') return direct;
  if (Array.isArray(payload.messages)) {
    const last = payload.messages[payload.messages.length - 1];
    if (typeof last === 'string') return last;
    if (last && typeof last.content === 'string') return last.content;
  }
  // Unknown envelope — show it rather than pretend the model said nothing.
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

/** Optional structured extras some builds attach alongside the prose. */
function readMeta(payload) {
  if (!payload || typeof payload !== 'object') return {};
  return {
    confidence: payload.confidence ?? payload.confidence_score ?? null,
    model: payload.model ?? payload.provider ?? null,
    reasoning: payload.reasoning ?? payload.rationale ?? payload.trace ?? null,
    sources: Array.isArray(payload.sources) ? payload.sources : Array.isArray(payload.evidence) ? payload.evidence : null,
    action: payload.suggested_action ?? payload.action ?? null,
  };
}

function Message({ msg }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const mine = msg.role === 'user';

  if (mine) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg rounded-br-sm border border-hx-accent-500/25 bg-hx-accent-500/10 px-2.5 py-1.5">
          <p className="whitespace-pre-wrap text-hx-12 text-hx-text-hi">{msg.text}</p>
          <p className="mt-0.5 text-right text-hx-10 text-hx-text-dim">{fmtTime(msg.at)}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-2">
      <span
        aria-hidden="true"
        className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded bg-hx-accent-500/15 text-hx-accent-400"
      >
        <Icon name="spark" size={12} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="text-hx-11 font-medium text-hx-text-hi">Helios</span>
          {msg.meta?.model && <span className="text-hx-10 text-hx-text-dim">{msg.meta.model}</span>}
          {msg.meta?.confidence != null && (
            <Badge tone={msg.meta.confidence >= 0.7 ? 'pos' : msg.meta.confidence >= 0.5 ? 'warn' : 'neutral'} size="xs">
              {Math.round(msg.meta.confidence * 100)}% conf
            </Badge>
          )}
          {msg.error && <Badge tone="neg" size="xs">error</Badge>}
        </div>

        <p
          className={cx(
            'mt-1 whitespace-pre-wrap text-hx-12 leading-relaxed',
            msg.error ? 'text-hx-neg-300' : 'text-hx-text-mid',
          )}
        >
          {msg.text}
        </p>

        {msg.meta?.reasoning && (
          <div className="mt-1.5">
            <button
              type="button"
              onClick={() => setTraceOpen((v) => !v)}
              aria-expanded={traceOpen}
              className="hx-focus inline-flex items-center gap-1 rounded text-hx-10 text-hx-text-dim hover:text-hx-text-mid"
            >
              <Icon name={traceOpen ? 'chevron-down' : 'chevron-right'} size={11} />
              Reasoning trace
            </button>
            {traceOpen && (
              <pre className="hx-scroll mt-1 max-h-40 overflow-auto rounded border border-hx-border-subtle bg-hx-bg-sunken p-2 font-hx-mono text-hx-10 text-hx-text-lo">
                {typeof msg.meta.reasoning === 'string'
                  ? msg.meta.reasoning
                  : JSON.stringify(msg.meta.reasoning, null, 2)}
              </pre>
            )}
          </div>
        )}

        {msg.meta?.sources?.length ? (
          <ul className="mt-1.5 flex flex-wrap gap-1">
            {msg.meta.sources.slice(0, 6).map((s, i) => (
              <li
                key={i}
                className="rounded border border-hx-border-subtle bg-hx-bg-sunken px-1.5 py-0.5 font-hx-mono text-hx-10 text-hx-text-dim"
              >
                {typeof s === 'string' ? s : s.id || s.title || 'source'}
              </li>
            ))}
          </ul>
        ) : null}

        <p className="mt-1 text-hx-10 text-hx-text-dim">{fmtTime(msg.at)}</p>
      </div>
    </div>
  );
}

export function CopilotPanel({ symbol, strategyId, seedPrompt, onSeedConsumed }) {
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const listRef = useRef(null);
  const abortRef = useRef(null);
  const seq = useRef(0);

  // Abort any in-flight request on unmount — a reply landing after teardown
  // would set state on a dead component.
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  const ask = useCallback(
    async (text) => {
      const q = (text ?? '').trim();
      if (!q || busy) return;

      setMessages((m) => m.concat({ id: ++seq.current, role: 'user', text: q, at: Date.now() }));
      setDraft('');
      setBusy(true);

      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      try {
        const res = await fetch(`${apiBase()}/api/v1/chat/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          // Context travels with the question so the answer is scoped to what
          // the desk is looking at, not to whatever the model last saw.
          body: JSON.stringify({ message: q, symbol: symbol || null, strategy: strategyId || null }),
          signal: ctrl.signal,
        });
        const raw = await res.text();
        let payload = null;
        try {
          payload = raw ? JSON.parse(raw) : null;
        } catch {
          payload = raw;
        }
        if (!res.ok) {
          const detail = payload && payload.detail ? payload.detail : `HTTP ${res.status}`;
          throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        setMessages((m) =>
          m.concat({
            id: ++seq.current,
            role: 'assistant',
            text: readReply(payload) || '(empty response)',
            meta: readMeta(payload),
            at: Date.now(),
          }),
        );
      } catch (e) {
        if (e?.name === 'AbortError') return;
        setMessages((m) =>
          m.concat({
            id: ++seq.current,
            role: 'assistant',
            error: true,
            text: `Copilot unavailable — ${e.message}`,
            at: Date.now(),
          }),
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, symbol, strategyId],
  );

  // A module can push a question in (e.g. "Explain this strategy").
  useEffect(() => {
    if (!seedPrompt) return;
    ask(seedPrompt);
    onSeedConsumed && onSeedConsumed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedPrompt]);

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || !e.shiftKey)) {
      e.preventDefault();
      ask(draft);
    }
  };

  return (
    <Panel flush className="flex h-full min-h-0 flex-col rounded-none border-0">
      <PanelHeader
        icon="copilot"
        title="AI Copilot"
        subtitle={symbol || strategyId ? `context: ${[symbol, strategyId].filter(Boolean).join(' · ')}` : 'no selection'}
        actions={
          messages.length ? (
            <Button size="xs" variant="subtle" onClick={() => setMessages([])}>
              Clear
            </Button>
          ) : null
        }
      />

      <PanelBody pad={false} scroll={false} className="flex min-h-0 flex-1 flex-col">
        <div ref={listRef} className="hx-scroll min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          {messages.length === 0 && !busy && (
            <div className="space-y-2">
              <p className="text-hx-12 text-hx-text-lo">
                Ask about the desk — positions, risk verdicts, drawdown, or a strategy&apos;s behaviour.
                Answers are advisory; the Copilot cannot place or modify orders.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <Message key={m.id} msg={m} />
          ))}
          {busy && (
            <div className="flex gap-2">
              <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded bg-hx-accent-500/15 text-hx-accent-400">
                <Icon name="spark" size={12} />
              </span>
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-4/5" />
              </div>
            </div>
          )}
        </div>

        {/* prompt chips */}
        <div className="hx-scroll flex shrink-0 gap-1 overflow-x-auto border-t border-hx-border-subtle px-2 py-1.5">
          {PROMPTS.map((p) => (
            <button
              key={p}
              type="button"
              disabled={busy}
              onClick={() => ask(p)}
              className="hx-focus shrink-0 rounded border border-hx-border-subtle bg-hx-bg-sunken px-2 py-1 text-hx-10 text-hx-text-lo transition-colors hover:border-hx-accent-500/40 hover:text-hx-text-hi disabled:opacity-50"
            >
              {p}
            </button>
          ))}
        </div>

        {/* composer */}
        <div className="shrink-0 border-t border-hx-border-subtle p-2">
          <div className="flex items-end gap-1.5 rounded-lg border border-hx-border-subtle bg-hx-bg-sunken px-2 py-1.5 focus-within:border-hx-accent-500/50">
            <textarea
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask Helios anything…"
              aria-label="Ask the Copilot"
              className="hx-scroll max-h-28 min-h-[20px] flex-1 resize-none bg-transparent text-hx-12 text-hx-text-hi outline-none placeholder:text-hx-text-dim"
            />
            <Button
              size="xs"
              variant="primary"
              iconOnly
              icon="chevron-right"
              loading={busy}
              disabled={!draft.trim()}
              onClick={() => ask(draft)}
              aria-label="Send message"
            />
          </div>
          <p className="mt-1 text-hx-10 text-hx-text-dim">
            Enter to send · Shift+Enter for a new line
          </p>
        </div>
      </PanelBody>
    </Panel>
  );
}

export default CopilotPanel;
