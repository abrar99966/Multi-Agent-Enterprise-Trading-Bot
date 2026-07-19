/**
 * Strategies & Learning.
 *
 * Strategies lists the tournament arms the backend exposes and lets the desk
 * push one into the Copilot for explanation. Learning drives training runs and
 * follows their progress at the documented 2s cadence — the only poll in the
 * product fast enough to feel live, and only while a run is active.
 */
import React, { useCallback, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget, jpost } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  DataGrid,
  EmptyState,
  Panel,
  PanelBody,
  PanelHeader,
  Skeleton,
  StatusChip,
  TONE_TEXT,
  cx,
  deltaTone,
  fmtNum,
  fmtPct,
} from '../../ui';

/* ---- Strategies --------------------------------------------------------- */

export function StrategiesModule({ strategyId, onSelectStrategy, onAskCopilot }) {
  const { data, loading, error, refresh } = useLivePoll(
    jget(`${apiBase()}/api/v1/learning/strategies`),
    CADENCE.allocator,
  );

  // The endpoint has shipped as both a bare array and an envelope.
  const rows = useMemo(() => {
    const raw = Array.isArray(data) ? data : data?.strategies || [];
    return raw.map((s, i) => (typeof s === 'string' ? { id: s, name: s } : { id: s.id ?? s.key ?? s.name ?? i, ...s }));
  }, [data]);

  const columns = useMemo(
    () => [
      { key: 'name', header: 'Strategy', render: (r) => <span className="text-hx-text-hi">{r.name ?? r.id}</span> },
      {
        key: 'kind',
        header: 'Type',
        width: 120,
        render: (r) => <span className="text-hx-text-lo">{r.kind || r.category || 'tournament arm'}</span>,
      },
      {
        key: 'wins',
        header: 'Wins',
        width: 80,
        align: 'right',
        render: (r) => (r.wins != null ? fmtNum(r.wins, { dp: 0 }) : '--'),
      },
      {
        key: 'act',
        header: '',
        width: 96,
        sortable: false,
        render: (r) => (
          <Button
            size="xs"
            variant="subtle"
            onClick={(e) => {
              e.stopPropagation();
              onAskCopilot && onAskCopilot(`Explain the ${r.name ?? r.id} strategy and when it underperforms.`);
            }}
          >
            Explain
          </Button>
        ),
      },
    ],
    [onAskCopilot],
  );

  return (
    <div className="h-full min-h-0 p-2">
      <Panel className="flex h-full min-h-0 flex-col">
        <PanelHeader
          icon="strategies"
          title="Strategy arms"
          subtitle="champion–challenger tournament"
          actions={<Button size="xs" variant="subtle" icon="refresh" onClick={refresh}>Refresh</Button>}
        />
        <PanelBody pad={false} className="min-h-0 flex-1">
          {error ? (
            <EmptyState
              variant="error"
              title="Strategy registry unavailable"
              hint={String(error.message || error)}
              action={<Button size="xs" variant="subtle" onClick={refresh}>Retry</Button>}
            />
          ) : (
            <DataGrid
              columns={columns}
              rows={rows}
              loading={loading && !data}
              selectedKey={strategyId}
              rowKey={(r) => r.id}
              exportName="strategies"
              emptyTitle="No strategies registered"
              emptyHint="The tournament publishes arms here once training has run."
              ariaLabel="Strategy arms"
              onRowClick={(r) => onSelectStrategy && onSelectStrategy(String(r.id))}
            />
          )}
        </PanelBody>
      </Panel>
    </div>
  );
}

/* ---- Learning ----------------------------------------------------------- */

export function LearningModule({ log }) {
  const base = apiBase();
  const { data: universes } = useLivePoll(jget(`${base}/api/v1/learning/universes`), CADENCE.performance);
  const { data: results, refresh: refreshResults } = useLivePoll(
    jget(`${base}/api/v1/learning/results`),
    CADENCE.performance,
  );

  const [universe, setUniverse] = useState('');
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState(null);

  // Status is polled fast, but only while a run is in flight — a 2s poll left
  // running all day is pure load for no information.
  const { data: status } = useLivePoll(jget(`${base}/api/v1/learning/status`), CADENCE.training);
  const running = Boolean(status?.running ?? (status?.done != null && status?.total != null && status.done < status.total));

  const universeList = useMemo(() => {
    const raw = Array.isArray(universes) ? universes : universes?.universes || [];
    return raw.map((u) => (typeof u === 'string' ? { key: u, label: u } : { key: u.key ?? u.id ?? u.name, label: u.label ?? u.name ?? u.key }));
  }, [universes]);

  const start = useCallback(async () => {
    setStarting(true);
    setErr(null);
    try {
      await jpost('/api/v1/learning/train', universe ? { universe } : {});
      log && log('info', `Training started${universe ? ` · ${universe}` : ''}`);
    } catch (e) {
      setErr(e.message);
    } finally {
      setStarting(false);
    }
  }, [universe, log]);

  const pct = status?.total ? Math.round(((status.done || 0) / status.total) * 100) : null;

  return (
    <div className="hx-scroll h-full min-h-0 space-y-2 overflow-y-auto p-2">
      <Panel>
        <PanelHeader
          icon="learning"
          title="Training"
          subtitle="walk-forward tournament over a symbol universe"
          actions={<StatusChip status={running ? 'live' : 'idle'} label={running ? 'running' : 'idle'} />}
        />
        <PanelBody className="space-y-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-hx-11 text-hx-text-lo">
              Universe
              <select
                value={universe}
                onChange={(e) => setUniverse(e.target.value)}
                className="hx-focus mt-1 block min-w-[220px] rounded border border-hx-border-subtle bg-hx-bg-sunken px-2 py-1 text-hx-12 text-hx-text-hi outline-none"
              >
                <option value="">(backend default)</option>
                {universeList.map((u) => (
                  <option key={u.key} value={u.key}>
                    {u.label}
                  </option>
                ))}
              </select>
            </label>
            <Button variant="primary" size="sm" onClick={start} loading={starting} disabled={running}>
              {running ? 'Training in progress' : 'Start training'}
            </Button>
          </div>

          {running && (
            <div>
              <div className="flex items-baseline justify-between text-hx-11">
                <span className="text-hx-text-lo">
                  {status?.current_symbol ? `Processing ${status.current_symbol}` : 'Working'}
                </span>
                <span className="font-hx-mono text-hx-text-mid">
                  {fmtNum(status?.done, { dp: 0 })} / {fmtNum(status?.total, { dp: 0 })}
                  {pct != null ? ` · ${pct}%` : ''}
                </span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded bg-white/5">
                <div
                  className="h-full bg-hx-accent-500 transition-[width] duration-500"
                  style={{ width: `${pct ?? 0}%` }}
                />
              </div>
            </div>
          )}

          {err && (
            <p role="alert" className="rounded border border-hx-neg-500/40 bg-hx-neg-500/10 p-2 text-hx-11 text-hx-neg-300">
              {err}
            </p>
          )}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader
          icon="analytics"
          title="Last run"
          actions={<Button size="xs" variant="subtle" icon="refresh" onClick={refreshResults}>Refresh</Button>}
        />
        <PanelBody>
          {results ? (
            <pre className="hx-scroll max-h-[320px] overflow-auto whitespace-pre-wrap break-words font-hx-mono text-hx-10 text-hx-text-lo">
              {JSON.stringify(results, null, 2)}
            </pre>
          ) : (
            <EmptyState icon="learning" title="No results yet" hint="Run training to populate tuned parameters and per-symbol winners." />
          )}
        </PanelBody>
      </Panel>
    </div>
  );
}

export default StrategiesModule;
