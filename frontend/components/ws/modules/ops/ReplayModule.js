/**
 * ReplayModule — incident replay over POST /api/v1/dash/journal/{name}/replay.
 *
 * The determinism contract: re-running a journal's bars through fresh components
 * must regenerate byte-identical intent and fill streams. A clean session diffs
 * to zero; anything else is surfaced with the first differing event, because the
 * FIRST divergence is the only one that is diagnostic — every later difference is
 * downstream of it.
 *
 * Replay is a synchronous, expensive backend operation. It is therefore only ever
 * triggered by an explicit user action and is never polled.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelToolbar, PanelBody,
  Button, ButtonGroup, Badge, StatusChip, Icon, Timeline, EmptyState, Skeleton, InfoTip,
  DataGrid, severityTone, TONE_TEXT, TONE_SOLID, TONE_HEX,
  fmtNum, fmtQty, fmtCur, fmtTime, cx,
} from '../../ui';
import { Select, Toggle } from './OpsField';
import {
  apiBase, jget, jpost, nsToMs, fmtBytes, eventSeverity, eventSummary,
  STREAM_LABELS, OPS_CADENCE, useControllable,
} from './opsApi';

/* ---- determinism diff ---------------------------------------------------- */

/**
 * Key-level comparison of the two event payloads. Deliberately one level deep:
 * these payloads are flat Pydantic models, and a recursive differ would bury the
 * one changed scalar under structural noise.
 */
function diffPayloads(a, b) {
  const A = a && typeof a === 'object' ? a : {};
  const B = b && typeof b === 'object' ? b : {};
  const keys = Array.from(new Set([...Object.keys(A), ...Object.keys(B)])).sort();
  return keys.map((key) => {
    const inA = key in A;
    const inB = key in B;
    const va = inA ? A[key] : undefined;
    const vb = inB ? B[key] : undefined;
    const same = inA && inB && JSON.stringify(va) === JSON.stringify(vb);
    return {
      key,
      a: inA ? va : undefined,
      b: inB ? vb : undefined,
      state: !inB ? 'only-journaled' : !inA ? 'only-replayed' : same ? 'same' : 'changed',
    };
  });
}

const DIFF_TONE = {
  same: 'neutral',
  changed: 'warn',
  'only-journaled': 'neg',
  'only-replayed': 'info',
};

const DIFF_LABEL = {
  same: 'match',
  changed: 'differs',
  'only-journaled': 'journal only',
  'only-replayed': 'replay only',
};

function scalar(v) {
  if (v === undefined) return '—';
  if (v === null) return 'null';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function DivergenceView({ divergence }) {
  const diff = useMemo(
    () => diffPayloads(divergence.journaled_event, divergence.replayed_event),
    [divergence],
  );
  const changed = diff.filter((d) => d.state !== 'same');

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2 p-2.5 rounded-md border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
        <Icon name="alert" size={14} className="text-hx-neg-400 shrink-0 mt-px" />
        <div className="min-w-0">
          <div className="text-hx-12 font-semibold text-hx-neg-300">
            Divergence in {STREAM_LABELS[divergence.stream] || divergence.stream}
          </div>
          <div className="text-hx-11 text-hx-neg-300/90 leading-relaxed mt-0.5">
            Streams first differ at index {divergence.first_diff_index}. The journal holds{' '}
            {fmtQty(divergence.journaled)} events, the replay produced {fmtQty(divergence.replayed)}.
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {[
          ['Journaled', divergence.journaled, 'neg'],
          ['Replayed', divergence.replayed, 'info'],
          ['First diff @', divergence.first_diff_index, 'warn'],
        ].map(([label, value, tone]) => (
          <div key={label} className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 py-2">
            <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{label}</div>
            <div className={cx('text-[18px] leading-6 font-semibold hx-mono hx-tnum', TONE_TEXT[tone])}>
              {fmtQty(value)}
            </div>
          </div>
        ))}
      </div>

      {/* Field-level diff. Unchanged keys stay visible but recede, so the
          changed rows read as a signal against a stable background. */}
      <div className="rounded-lg border border-hx-border-subtle overflow-hidden">
        <div className="grid grid-cols-[1.1fr_1fr_1fr_auto] gap-2 px-2 py-1.5 bg-white/[0.03] border-b border-hx-border-subtle">
          {['Field', 'Journaled', 'Replayed', 'State'].map((h) => (
            <span key={h} className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{h}</span>
          ))}
        </div>
        <div className="max-h-[320px] overflow-auto hx-scroll divide-y divide-hx-border-subtle">
          {diff.length === 0 && (
            <div className="px-2 py-3 text-hx-11 text-hx-text-dim">
              Both events are absent — the streams differ in length only.
            </div>
          )}
          {diff.map((d) => {
            const tone = DIFF_TONE[d.state];
            const isSame = d.state === 'same';
            return (
              <div
                key={d.key}
                className={cx(
                  'grid grid-cols-[1.1fr_1fr_1fr_auto] gap-2 px-2 py-1 items-center',
                  !isSame && 'bg-hx-warn-500/[0.12]',
                )}
              >
                <span className={cx('text-hx-11 hx-mono truncate', isSame ? 'text-hx-text-lo' : 'text-hx-text-hi')}>
                  {d.key}
                </span>
                <span className={cx('text-hx-11 hx-mono truncate', isSame ? 'text-hx-text-dim' : 'text-hx-neg-300')} title={scalar(d.a)}>
                  {scalar(d.a)}
                </span>
                <span className={cx('text-hx-11 hx-mono truncate', isSame ? 'text-hx-text-dim' : 'text-hx-info-400')} title={scalar(d.b)}>
                  {scalar(d.b)}
                </span>
                <Badge tone={tone} size="xs">{DIFF_LABEL[d.state]}</Badge>
              </div>
            );
          })}
        </div>
      </div>

      <div className="text-hx-10 text-hx-text-dim">
        {changed.length} of {diff.length} fields differ.
      </div>
    </div>
  );
}

/* ---- scrubbable timeline ------------------------------------------------- */

/**
 * Density strip: one tick per loaded event, positioned by time. Gives the scrub
 * bar a shape — clusters of activity are visible before you drag anything.
 */
function DensityStrip({ events, cursor, onSeek }) {
  const W = 600;
  const H = 26;

  const bounds = useMemo(() => {
    const ts = events.map((e) => nsToMs(e.ts_recorded)).filter((t) => t !== null);
    if (!ts.length) return null;
    const min = Math.min(...ts);
    const max = Math.max(...ts);
    return { min, max, span: max - min || 1 };
  }, [events]);

  if (!bounds) return null;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="w-full block cursor-pointer"
      style={{ height: H }}
      role="presentation"
      onClick={(e) => {
        // Map the click back to the nearest event by time, not by index — the
        // strip is time-scaled, so index-mapping would jump to the wrong event.
        const rect = e.currentTarget.getBoundingClientRect();
        const frac = (e.clientX - rect.left) / rect.width;
        const targetT = bounds.min + frac * bounds.span;
        let best = 0;
        let bestD = Infinity;
        events.forEach((ev, i) => {
          const t = nsToMs(ev.ts_recorded);
          if (t === null) return;
          const d = Math.abs(t - targetT);
          if (d < bestD) { bestD = d; best = i; }
        });
        onSeek(best);
      }}
    >
      <rect x="0" y="0" width={W} height={H} fill="rgba(255,255,255,0.02)" />
      {events.map((ev, i) => {
        const t = nsToMs(ev.ts_recorded);
        if (t === null) return null;
        const x = ((t - bounds.min) / bounds.span) * W;
        const sev = eventSeverity(ev);
        const color = sev === 'ok' ? 'rgba(255,255,255,0.22)' : TONE_HEX[severityTone(sev)];
        const tall = sev !== 'ok';
        return (
          <line
            key={i}
            x1={x}
            x2={x}
            y1={tall ? 3 : 8}
            y2={tall ? H - 3 : H - 8}
            stroke={color}
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        );
      })}
      {events[cursor] && nsToMs(events[cursor].ts_recorded) !== null && (
        <line
          x1={((nsToMs(events[cursor].ts_recorded) - bounds.min) / bounds.span) * W}
          x2={((nsToMs(events[cursor].ts_recorded) - bounds.min) / bounds.span) * W}
          y1="0"
          y2={H}
          stroke={TONE_HEX.accent}
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

function ScrubTimeline({ events, loading, error, onRetry }) {
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const timerRef = useRef(null);

  useEffect(() => { setCursor(0); setPlaying(false); }, [events]);

  useEffect(() => {
    if (!playing || !events.length) return undefined;
    timerRef.current = setInterval(() => {
      setCursor((c) => {
        if (c >= events.length - 1) { setPlaying(false); return c; }
        return c + 1;
      });
    }, 1000 / speed);
    return () => clearInterval(timerRef.current);
  }, [playing, speed, events.length]);

  if (loading) {
    return (
      <div className="space-y-2">
        <Skeleton h={26} />
        <Skeleton h={140} rounded="rounded-lg" />
      </div>
    );
  }

  if (error) {
    return (
      <EmptyState
        variant="error"
        title="Could not load timeline"
        hint={error.message}
        action={{ label: 'Retry', onClick: onRetry }}
      />
    );
  }

  if (!events.length) {
    return <EmptyState title="No events to scrub" hint="This journal window is empty." icon="replay" />;
  }

  // A window around the cursor keeps the DOM small on a 500-event page while
  // still giving the surrounding context that makes an event interpretable.
  const from = Math.max(0, cursor - 4);
  const window = events.slice(from, from + 9).map((ev, i) => ({
    id: `${ev.stream}-${ev.seq}-${from + i}`,
    at: nsToMs(ev.ts_recorded),
    title: eventSummary(ev),
    body: from + i === cursor ? `${STREAM_LABELS[ev.stream] || ev.stream} · ${ev.type}` : undefined,
    meta: from + i === cursor ? undefined : `${ev.type}`,
    severity: eventSeverity(ev),
    icon: from + i === cursor ? 'play' : undefined,
  }));

  const cur = events[cursor];

  return (
    <div className="flex flex-col gap-2.5">
      <DensityStrip events={events} cursor={cursor} onSeek={setCursor} />

      <input
        type="range"
        min={0}
        max={events.length - 1}
        value={cursor}
        onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }}
        aria-label="Scrub through journal events"
        aria-valuetext={`Event ${cursor + 1} of ${events.length}: ${eventSummary(cur)}`}
        className="hx-focus w-full accent-hx-accent-500 cursor-pointer"
      />

      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <Button
            size="xs"
            icon={playing ? 'pause' : 'play'}
            onClick={() => setPlaying((p) => !p)}
            disabled={cursor >= events.length - 1 && !playing}
          >
            {playing ? 'Pause' : 'Play'}
          </Button>
          <Button
            size="xs"
            variant="subtle"
            icon="chevron-left"
            iconOnly
            aria-label="Previous event"
            disabled={cursor === 0}
            onClick={() => { setPlaying(false); setCursor((c) => Math.max(0, c - 1)); }}
          />
          <Button
            size="xs"
            variant="subtle"
            icon="chevron-right"
            iconOnly
            aria-label="Next event"
            disabled={cursor >= events.length - 1}
            onClick={() => { setPlaying(false); setCursor((c) => Math.min(events.length - 1, c + 1)); }}
          />
          <ButtonGroup
            size="xs"
            options={[{ value: 2, label: '2×' }, { value: 4, label: '4×' }, { value: 16, label: '16×' }]}
            value={speed}
            onChange={setSpeed}
          />
        </div>
        <span className="text-hx-11 hx-mono hx-tnum text-hx-text-lo">
          {cursor + 1} / {events.length} · {fmtTime(nsToMs(cur.ts_recorded), { mode: 'datetime' })}
        </span>
      </div>

      <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-2">
        <Timeline events={window} dense />
      </div>
    </div>
  );
}

/* ---- module -------------------------------------------------------------- */

export function ReplayModule({
  journal,           // store: selected journal filename
  onJournalChange,   // store: publish journal selection
  className = '',
}) {
  const [name, setName] = useControllable(journal, onJournalChange, '');
  const [useModel, setUseModel] = useState(false);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const journalsUrl = `${apiBase()}/api/v1/dash/journals`;
  const journalsQ = useLivePoll(jget(journalsUrl), OPS_CADENCE.journals, [journalsUrl]);
  const journals = journalsQ.data?.journals || [];

  useEffect(() => {
    if (!name && journals.length) setName(journals[0].name);
  }, [name, journals, setName]);

  const current = journals.find((j) => j.name === name) || null;

  // Replaying a different journal invalidates the previous result — showing a
  // stale "match" against the wrong journal would be actively misleading.
  useEffect(() => { setResult(null); setError(null); }, [name, useModel]);

  const eventsUrl = name
    ? `${apiBase()}/api/v1/dash/journal/${encodeURIComponent(name)}/events?offset=0&limit=500`
    : null;
  const eventsQ = useLivePoll(
    eventsUrl ? jget(eventsUrl) : () => Promise.resolve(null),
    OPS_CADENCE.journals,
    [eventsUrl],
  );
  const events = eventsQ.data?.events || [];

  const run = useCallback(async () => {
    if (!name) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await jpost(
        `/api/v1/dash/journal/${encodeURIComponent(name)}/replay?use_model=${useModel ? 'true' : 'false'}`,
      );
      setResult(res);
    } catch (e) {
      setError(e.message || 'Replay failed');
    } finally {
      setRunning(false);
    }
  }, [name, useModel]);

  const summary = result?.replay_summary || null;

  const journalCols = useMemo(() => [
    { key: 'name', header: 'Journal', width: 'auto', mono: true },
    { key: 'records', header: 'Records', width: 90, numeric: true, render: (r) => fmtQty(r.records) },
    { key: 'size_bytes', header: 'Size', width: 84, numeric: true, render: (r) => fmtBytes(r.size_bytes) },
    {
      key: 'chain_ok',
      header: 'Hash chain',
      width: 150,
      sortable: false,
      render: (r) => (
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <Icon
            name={r.chain_ok ? 'check' : 'alert'}
            size={12}
            title={r.chain_ok ? 'Verified' : 'Failed'}
            className={cx('shrink-0', r.chain_ok ? 'text-hx-pos-400' : 'text-hx-neg-400')}
          />
          <span className={cx('text-hx-11 truncate', r.chain_ok ? 'text-hx-pos-400' : 'text-hx-neg-400')}>
            {r.chain_ok ? 'Verified' : 'Broken'}
          </span>
          {r.chain_reason && (
            <span className="text-hx-10 text-hx-text-dim truncate" title={r.chain_reason}>
              {r.chain_reason}
            </span>
          )}
        </span>
      ),
    },
  ], []);

  return (
    <Panel className={cx('h-full', className)}>
      <PanelHeader
        title="Incident replay"
        icon="replay"
        subtitle={current ? `${fmtQty(current.records)} records` : undefined}
        actions={
          current && (
            <StatusChip
              status={current.chain_ok ? 'connected' : 'error'}
              label={current.chain_ok ? 'Chain verified' : 'Chain broken'}
              showIcon
            />
          )
        }
      />

      <PanelToolbar>
        <div className="flex items-center gap-2 min-w-0">
          <Select
            label="Journal"
            value={name}
            onChange={setName}
            options={journals.map((j) => ({ value: j.name, label: j.name }))}
            disabled={!journals.length}
            placeholder={journals.length ? undefined : 'No journals'}
            className="max-w-[240px]"
          />
          <Toggle
            checked={useModel}
            onChange={setUseModel}
            label="GBDT model"
            hint="off = momentum reference"
          />
        </div>
        <Button
          size="xs"
          variant="primary"
          icon="play"
          loading={running}
          disabled={!name}
          onClick={run}
        >
          Run replay
        </Button>
      </PanelToolbar>

      <PanelBody className="flex flex-col gap-4">
        {journalsQ.loading && !journals.length && (
          <div className="space-y-2">
            <Skeleton h={26} />
            <Skeleton h={120} rounded="rounded-lg" />
          </div>
        )}

        {!journalsQ.loading && journalsQ.error && (
          <EmptyState
            variant="error"
            size="lg"
            title="Could not load journals"
            hint={journalsQ.error.message}
            action={{ label: 'Retry', onClick: journalsQ.refresh }}
          />
        )}

        {!journalsQ.loading && !journalsQ.error && !journals.length && (
          <EmptyState
            size="lg"
            title="No journals available"
            hint="Replay needs a recorded session. Run a paper session to produce one."
            icon="replay"
          />
        )}

        {journals.length > 0 && (
          <>
            {/* --- journal inventory + chain verification --- */}
            <section>
              <div className="flex items-center gap-1.5 mb-2">
                <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                  Journals — hash-chain verification
                </span>
                <InfoTip content="Each journal is a hash chain. A broken chain means the file was truncated or edited after the fact, and any replay of it is untrustworthy." />
              </div>
              <div className="rounded-lg border border-hx-border-subtle overflow-hidden">
                <DataGrid
                  columns={journalCols}
                  rows={journals}
                  rowKey={(r) => r.name}
                  onRowClick={(r) => setName(r.name)}
                  selectedKey={name}
                  maxHeight={180}
                  ariaLabel="Journals with chain verification status"
                />
              </div>
              {journalsQ.data?.journal_dir && (
                <div className="text-hx-10 text-hx-text-dim hx-mono mt-1 truncate">
                  {journalsQ.data.journal_dir}
                </div>
              )}
            </section>

            {/* --- determinism result --- */}
            <section className="flex flex-col gap-2">
              <div className="flex items-center gap-1.5">
                <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Determinism check</span>
                <InfoTip content="Re-runs the journaled bars through fresh components and diffs the regenerated signal.intents and exec.fills against what was recorded." />
              </div>

              {error && (
                <EmptyState
                  variant="error"
                  title="Replay failed"
                  hint={error}
                  action={{ label: 'Retry', onClick: run }}
                />
              )}

              {running && (
                <div className="flex items-center gap-2 p-3 rounded-lg border border-hx-border-subtle bg-hx-bg-raised">
                  <Icon name="refresh" size={14} className="text-hx-accent-400 animate-spin" />
                  <span className="text-hx-12 text-hx-text-mid">
                    Replaying {name} through a fresh pipeline…
                  </span>
                </div>
              )}

              {!running && !error && !result && (
                <div className="flex items-center justify-between gap-3 p-3 rounded-lg border border-hx-border-subtle bg-hx-bg-raised">
                  <span className="text-hx-11 text-hx-text-lo leading-relaxed max-w-[60ch]">
                    Run a replay to verify this session reproduces exactly. The replay bus has no
                    journal and no broker, so it is read-only and safe to run against live journals.
                  </span>
                  <Button size="sm" variant="primary" icon="play" onClick={run} disabled={!name}>
                    Run replay
                  </Button>
                </div>
              )}

              {result && (
                <>
                  <div
                    className={cx(
                      'flex items-start gap-2 p-2.5 rounded-md border',
                      result.match
                        ? 'border-hx-pos-500/30 bg-hx-pos-500/[0.12]'
                        : 'border-hx-neg-500/30 bg-hx-neg-500/[0.12]',
                    )}
                  >
                    <Icon
                      name={result.match ? 'check' : 'alert'}
                      size={14}
                      className={cx('shrink-0 mt-px', result.match ? 'text-hx-pos-400' : 'text-hx-neg-400')}
                    />
                    <div className="min-w-0">
                      <div className={cx('text-hx-12 font-semibold', result.match ? 'text-hx-pos-300' : 'text-hx-neg-300')}>
                        {result.match ? 'Deterministic — streams match exactly' : 'Non-deterministic — streams diverged'}
                      </div>
                      <div className={cx('text-hx-11 leading-relaxed mt-0.5', result.match ? 'text-hx-pos-300/90' : 'text-hx-neg-300/90')}>
                        {fmtQty(result.bars_replayed)} bars replayed with the{' '}
                        <span className="hx-mono">{result.strategy}</span> strategy.
                      </div>
                    </div>
                  </div>

                  {!result.match && result.divergence && <DivergenceView divergence={result.divergence} />}

                  {summary && (
                    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
                      {[
                        ['Bars', fmtQty(summary.bars)],
                        ['Intents', fmtQty(summary.intents)],
                        ['Verdicts', fmtQty(summary.verdicts)],
                        ['Approved', fmtQty(summary.approved)],
                        ['Rejected', fmtQty(summary.rejected)],
                        ['Orders', fmtQty(summary.orders)],
                        ['Fills', fmtQty(summary.fills)],
                        ['Realised P&L', fmtCur(summary.realized_pnl_total, { ccy: 'INR', signed: true })],
                      ].map(([label, value]) => (
                        <div key={label} className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-2.5 py-1.5 min-w-0">
                          <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo truncate">{label}</div>
                          <div className="text-hx-13 font-semibold hx-mono hx-tnum text-hx-text-hi truncate">{value}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {summary?.tier_counts && (
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Approval tiers</span>
                      {/* Keys are ints server-side and arrive as JSON strings. */}
                      {Object.entries(summary.tier_counts).map(([tier, n]) => (
                        <span key={tier} className="inline-flex items-center gap-1.5">
                          <span className={cx('h-2 w-2 rounded-full', TONE_SOLID[tier === '1' ? 'pos' : tier === '2' ? 'warn' : 'neg'])} aria-hidden="true" />
                          <span className="text-hx-11 text-hx-text-mid">Tier {tier}</span>
                          <span className="text-hx-11 hx-mono hx-tnum text-hx-text-hi">{fmtQty(n)}</span>
                        </span>
                      ))}
                    </div>
                  )}

                  {summary?.final_positions && Object.keys(summary.final_positions).length > 0 && (
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Final positions</span>
                      {Object.entries(summary.final_positions).map(([sym, qty]) => (
                        <span key={sym} className="inline-flex items-center gap-1.5">
                          <span className="text-hx-11 hx-mono text-hx-text-mid">{sym}</span>
                          <span className={cx('text-hx-11 hx-mono hx-tnum', Number(qty) === 0 ? 'text-hx-text-dim' : 'text-hx-text-hi')}>
                            {fmtQty(qty)}
                          </span>
                          {summary.last_prices?.[sym] !== undefined && (
                            <span className="text-hx-10 text-hx-text-dim hx-mono">@ {fmtNum(summary.last_prices[sym])}</span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </section>

            {/* --- scrubbable timeline --- */}
            <section className="flex flex-col gap-2">
              <div className="flex items-center gap-1.5">
                <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
                  Timeline — first 500 events
                </span>
                <InfoTip content="Scrub or play through the recorded session. Tall ticks on the density strip mark elevated-severity events." />
              </div>
              <ScrubTimeline
                events={events}
                loading={Boolean(name) && eventsQ.loading && !eventsQ.data}
                error={eventsQ.error}
                onRetry={eventsQ.refresh}
              />
            </section>
          </>
        )}
      </PanelBody>
    </Panel>
  );
}

export default ReplayModule;
