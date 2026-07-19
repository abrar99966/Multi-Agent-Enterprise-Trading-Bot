/**
 * TopBar — identity, active context, connection state, global actions.
 *
 * The connection chip polls GET /health (a constant-time liveness probe, not the
 * agent-probing /performance/health) so "is the backend up" never costs a real
 * query. Latency is measured client-side around that fetch.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Icon, StatusChip, cx, fmtLatency } from '../ui';
import { apiBase, CADENCE } from '../../../lib/ws/api';
import { useLivePoll } from '../../../lib/useLivePoll';

/** Wall clock, ticking once a second. Mount-only so SSR emits no time at all —
 *  a server-rendered clock is guaranteed to mismatch on hydration. */
function Clock() {
  const [now, setNow] = useState(null);
  useEffect(() => {
    const tick = () => setNow(new Date());
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);
  if (!now) return <span className="font-hx-mono text-hx-11 text-hx-text-dim tabular-nums">--:--:--</span>;
  return (
    <span className="font-hx-mono text-hx-11 text-hx-text-lo tabular-nums" suppressHydrationWarning>
      {now.toLocaleTimeString('en-GB', { hour12: false })}
    </span>
  );
}

/** Round-trips /health and reports status + measured latency. */
function useBackendHealth() {
  const latencyRef = useRef(null);
  const fetcher = useCallback((signal) => {
    const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now();
    return fetch(`${apiBase()}/health`, { signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((j) => {
        const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now();
        latencyRef.current = t1 - t0;
        return j;
      });
  }, []);
  fetcher.cacheKey = 'hx:health';

  const { data, error, loading } = useLivePoll(fetcher, 15000, ['hx:health']);

  // Degraded is a real state: the probe answered but not with "healthy".
  const status = error ? 'offline' : loading && !data ? 'stale' : data?.status === 'healthy' ? 'connected' : 'degraded';
  return { status, latency: latencyRef.current, error };
}

export function TopBar({
  module,
  symbol,
  onOpenPalette,
  onOpenHelp,
  onToggleContext,
  onToggleConsole,
  contextOpen,
  consoleOpen,
}) {
  const { status, latency } = useBackendHealth();

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b border-hx-border-subtle bg-hx-bg-sunken px-3">
      {/* brand */}
      <div className="flex items-center gap-2 pr-1">
        <span
          aria-hidden="true"
          className="grid h-5 w-5 place-items-center rounded bg-hx-accent-500/15 text-hx-accent-400"
        >
          <Icon name="spark" size={13} />
        </span>
        <span className="text-hx-12 font-semibold tracking-tight text-hx-text-hi">Helios</span>
        <span className="text-hx-10 uppercase tracking-wider text-hx-text-dim">Capital</span>
      </div>

      <div aria-hidden="true" className="h-4 w-px bg-hx-border-subtle" />

      {/* active context — module, then the symbol every module is scoped to */}
      <div className="flex min-w-0 items-center gap-2">
        <Icon name={module.icon} size={14} className="shrink-0 text-hx-text-lo" />
        <span className="truncate text-hx-12 font-medium text-hx-text-mid">{module.label}</span>
        {symbol && (
          <>
            <Icon name="chevron-right" size={12} className="shrink-0 text-hx-text-dim" />
            <span className="font-hx-mono text-hx-12 font-semibold text-hx-accent-300">{symbol}</span>
          </>
        )}
      </div>

      {/* palette trigger — doubles as the discoverability affordance for Ctrl+K */}
      <button
        type="button"
        onClick={onOpenPalette}
        className={cx(
          'ml-auto flex w-[260px] items-center gap-2 rounded-md border border-hx-border-subtle bg-hx-bg-base px-2.5 py-1',
          'text-hx-11 text-hx-text-dim transition-colors hover:border-hx-border-strong hover:text-hx-text-lo',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-hx-accent-400/70',
        )}
      >
        <Icon name="search" size={13} />
        <span>Search symbols, modules, actions</span>
        <kbd className="ml-auto rounded border border-hx-border-subtle bg-hx-bg-raised px-1 font-hx-mono text-hx-10 text-hx-text-dim">
          ⌘K
        </kbd>
      </button>

      <div className="flex items-center gap-2">
        <StatusChip
          status={status}
          showIcon
          detail={status === 'connected' && latency != null ? fmtLatency(latency) : undefined}
        />
        <Clock />
      </div>

      <div aria-hidden="true" className="h-4 w-px bg-hx-border-subtle" />

      <div className="flex items-center gap-0.5">
        <Button
          variant="ghost"
          size="xs"
          icon="columns"
          iconOnly
          aria-label={contextOpen ? 'Hide context panel' : 'Show context panel'}
          aria-pressed={contextOpen}
          onClick={onToggleContext}
        />
        <Button
          variant="ghost"
          size="xs"
          icon="logs"
          iconOnly
          aria-label={consoleOpen ? 'Hide console' : 'Show console'}
          aria-pressed={consoleOpen}
          onClick={onToggleConsole}
        />
        <Button
          variant="ghost"
          size="xs"
          icon="info"
          iconOnly
          aria-label="Keyboard shortcuts"
          onClick={onOpenHelp}
        />
      </div>
    </header>
  );
}

export default TopBar;
