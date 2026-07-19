/**
 * LogsModule — journal event console over GET /api/v1/dash/journal/{name}/events.
 *
 * Two honesty constraints drive this design:
 *
 *  1. The endpoint has NO search and NO severity concept. Search and severity
 *     filtering therefore run client-side over the LOADED PAGE only, and the UI
 *     says so — silently filtering a 200-row window while implying a whole-journal
 *     search is how people miss the event they were looking for.
 *  2. `stream` is an exact-equality filter server-side (no globs), so the stream
 *     picker is built from the platform projection's real stream→count map rather
 *     than a hardcoded list. Streams with zero events never appear as options.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelToolbar, PanelBody, PanelFooter,
  DataGrid, Drawer, Button, Badge, Icon, EmptyState, StatusChip,
  SEVERITY_META, severityTone, TONE_TEXT,
  fmtTime, fmtQty, cx,
} from '../../ui';
import { Select, SearchInput, Toggle } from './OpsField';
import {
  apiBase, jget, nsToMs, eventSeverity, eventSummary, prettyJson,
  STREAM_LABELS, OPS_CADENCE, useControllable,
} from './opsApi';

const PAGE_SIZES = [
  { value: 100, label: '100' },
  { value: 200, label: '200' },
  { value: 500, label: '500' },
];

const SEVERITY_ORDER = ['ok', 'elevated', 'high', 'critical'];

export function LogsModule({
  journal,            // store: selected journal filename (e.g. "real-jun11.jsonl")
  onJournalChange,    // store: publish journal selection
  className = '',
}) {
  const [name, setName] = useControllable(journal, onJournalChange, '');
  const [stream, setStream] = useState('');
  const [limit, setLimit] = useState(200);
  const [offset, setOffset] = useState(0);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState('');
  const [minSeverity, setMinSeverity] = useState('ok');
  const [detail, setDetail] = useState(null);

  /* ---- journals ---- */
  const journalsUrl = `${apiBase()}/api/v1/dash/journals`;
  const journalsQ = useLivePoll(jget(journalsUrl), OPS_CADENCE.journals, [journalsUrl]);
  const journals = journalsQ.data?.journals || [];

  // Default to the first journal once the list arrives, so the console is never
  // an empty shell waiting on a manual pick.
  useEffect(() => {
    if (!name && journals.length) setName(journals[0].name);
  }, [name, journals, setName]);

  const current = journals.find((j) => j.name === name) || null;

  /* ---- stream options from the platform projection ---- */
  const platformUrl = name ? `${apiBase()}/api/v1/dash/journal/${encodeURIComponent(name)}/platform` : null;
  const platformQ = useLivePoll(
    platformUrl ? jget(platformUrl) : () => Promise.resolve(null),
    OPS_CADENCE.journals,
    [platformUrl],
  );

  const streamOptions = useMemo(() => {
    const streams = platformQ.data?.streams || {};
    const opts = Object.entries(streams)
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([s, n]) => ({ value: s, label: `${STREAM_LABELS[s] || s} (${n})` }));
    return [{ value: '', label: 'All streams' }, ...opts];
  }, [platformQ.data]);

  /* ---- events ---- */
  const eventsUrl = name
    ? `${apiBase()}/api/v1/dash/journal/${encodeURIComponent(name)}/events?offset=${offset}&limit=${limit}${stream ? `&stream=${encodeURIComponent(stream)}` : ''}`
    : null;

  const eventsQ = useLivePoll(
    eventsUrl ? jget(eventsUrl) : () => Promise.resolve(null),
    follow ? OPS_CADENCE.eventsFollow : OPS_CADENCE.events,
    [eventsUrl, follow],
  );

  const total = eventsQ.data?.total ?? 0;

  // Reset paging whenever the window's identity changes — an offset valid for
  // one stream filter is meaningless for another.
  useEffect(() => { setOffset(0); }, [name, stream, limit]);

  /**
   * Follow-tail. The endpoint slices [offset:offset+limit] and gives no "tail"
   * mode, so the newest page is reached by pinning offset to total-limit. `total`
   * is only known from a response, hence this converges on the next poll rather
   * than immediately — correct, and cheaper than a probe request every cycle.
   */
  useEffect(() => {
    if (!follow || !eventsQ.data) return;
    const tail = Math.max(0, total - limit);
    if (tail !== offset) setOffset(tail);
  }, [follow, total, limit, offset, eventsQ.data]);

  /* ---- client-side page filtering ---- */
  const rows = useMemo(() => {
    const evts = eventsQ.data?.events || [];
    const minRank = SEVERITY_META[minSeverity]?.rank ?? 0;
    const q = query.trim().toLowerCase();

    return evts
      .map((ev) => {
        const severity = eventSeverity(ev);
        return { ...ev, severity, summary: eventSummary(ev) };
      })
      .filter((ev) => (SEVERITY_META[ev.severity]?.rank ?? 0) >= minRank)
      .filter((ev) => {
        if (!q) return true;
        // Search the rendered summary plus the raw payload, so a user can find a
        // fill by an id that never appears in the one-line summary.
        return (
          ev.summary.toLowerCase().includes(q)
          || String(ev.type).toLowerCase().includes(q)
          || String(ev.stream).toLowerCase().includes(q)
          || JSON.stringify(ev.payload || {}).toLowerCase().includes(q)
        );
      })
      .reverse(); // newest first — a console reads downward from the latest
  }, [eventsQ.data, minSeverity, query]);

  const filtering = Boolean(query.trim()) || minSeverity !== 'ok';

  const columns = useMemo(() => [
    {
      key: 'ts_recorded',
      header: 'Time',
      width: 96,
      mono: true,
      render: (r) => fmtTime(nsToMs(r.ts_recorded)),
    },
    {
      key: 'severity',
      header: 'Sev',
      width: 34,
      align: 'center',
      sortable: false,
      render: (r) => {
        const meta = SEVERITY_META[r.severity];
        const tone = severityTone(r.severity);
        // Icon + title carry severity; colour is the redundant channel.
        return (
          <Icon
            name={meta.icon}
            size={12}
            title={meta.label}
            className={cx('inline-block', TONE_TEXT[tone])}
          />
        );
      },
    },
    {
      key: 'stream',
      header: 'Stream',
      width: 132,
      render: (r) => (
        <span className="text-hx-11 text-hx-text-mid truncate">{STREAM_LABELS[r.stream] || r.stream}</span>
      ),
    },
    { key: 'type', header: 'Type', width: 128, mono: true },
    { key: 'seq', header: 'Seq', width: 62, numeric: true, render: (r) => fmtQty(r.seq) },
    {
      key: 'summary',
      header: 'Event',
      width: 'auto',
      sortable: false,
      render: (r) => <span className="hx-mono text-hx-text-hi truncate">{r.summary}</span>,
    },
  ], []);

  const pageStart = total ? offset + 1 : 0;
  const pageEnd = Math.min(offset + limit, total);
  const canPrev = offset > 0;
  const canNext = offset + limit < total;

  const step = useCallback((dir) => {
    setFollow(false);
    setOffset((o) => Math.max(0, Math.min(o + dir * limit, Math.max(0, total - 1))));
  }, [limit, total]);

  const loading = Boolean(name) && eventsQ.loading && !eventsQ.data;

  return (
    <Panel className={cx('h-full', className)} loading={loading}>
      <PanelHeader
        title="Event log"
        icon="logs"
        subtitle={current ? `${fmtQty(current.records)} records` : undefined}
        actions={
          <>
            {current && (
              <StatusChip
                status={current.chain_ok ? 'connected' : 'error'}
                label={current.chain_ok ? 'Chain OK' : 'Chain broken'}
                showIcon
              />
            )}
            <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Reload events" onClick={eventsQ.refresh} />
          </>
        }
      />

      <PanelToolbar>
        <div className="flex items-center gap-2 min-w-0">
          <Select
            label="Journal"
            value={name}
            onChange={setName}
            options={journals.map((j) => ({ value: j.name, label: j.name }))}
            placeholder={journals.length ? undefined : 'No journals'}
            disabled={!journals.length}
            className="max-w-[220px]"
          />
          <Select
            label="Stream"
            value={stream}
            onChange={setStream}
            options={streamOptions}
            className="max-w-[210px]"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Select
            label="Sev"
            value={minSeverity}
            onChange={setMinSeverity}
            size="xs"
            options={SEVERITY_ORDER.map((s) => ({ value: s, label: `${SEVERITY_META[s].label}+` }))}
          />
          <SearchInput value={query} onChange={setQuery} placeholder="Search page…" className="w-[200px]" />
          <Toggle checked={follow} onChange={setFollow} label="Follow" />
        </div>
      </PanelToolbar>

      <PanelBody pad={false} scroll={false} className="flex flex-col">
        {!name && !journalsQ.loading && (
          <EmptyState
            size="lg"
            title={journalsQ.error ? 'Could not load journals' : 'No journals found'}
            variant={journalsQ.error ? 'error' : 'default'}
            hint={
              journalsQ.error
                ? journalsQ.error.message
                : 'Run a paper session to produce a journal, then reload.'
            }
            icon="logs"
            action={{ label: 'Retry', onClick: journalsQ.refresh }}
          />
        )}

        {name && eventsQ.error && (
          <EmptyState
            size="lg"
            variant="error"
            title="Could not load events"
            hint={eventsQ.error.message}
            action={{ label: 'Retry', onClick: eventsQ.refresh }}
          />
        )}

        {name && !eventsQ.error && (
          <>
            {filtering && (
              <div className="flex items-center gap-1.5 px-3 py-1 shrink-0 border-b border-hx-border-subtle bg-hx-warn-500/[0.12]">
                <Icon name="filter" size={11} className="text-hx-warn-400 shrink-0" />
                <span className="text-hx-10 text-hx-warn-300">
                  Filters apply to the {fmtQty(eventsQ.data?.events?.length || 0)} events on this page,
                  not the full journal. Showing {fmtQty(rows.length)}.
                </span>
                <button
                  type="button"
                  onClick={() => { setQuery(''); setMinSeverity('ok'); }}
                  className="hx-focus text-hx-10 text-hx-warn-300 underline underline-offset-2 rounded ml-1"
                >
                  Clear
                </button>
              </div>
            )}

            <DataGrid
              className="flex-1 min-h-0"
              columns={columns}
              rows={rows}
              rowKey={(r, i) => `${r.stream}:${r.seq}:${i}`}
              loading={loading}
              onRowClick={setDetail}
              selectedKey={detail ? `${detail.stream}:${detail.seq}` : undefined}
              exportName={`events-${name.replace(/\.jsonl$/, '')}`}
              emptyTitle={filtering ? 'Nothing matches on this page' : 'No events'}
              emptyHint={
                filtering
                  ? 'Try clearing the filter, or page back through the journal.'
                  : 'This stream has no events in the current window.'
              }
              ariaLabel="Journal events"
            />
          </>
        )}
      </PanelBody>

      {name && !eventsQ.error && (
        <PanelFooter>
          <span className="hx-mono hx-tnum">
            {total ? `${fmtQty(pageStart)}–${fmtQty(pageEnd)} of ${fmtQty(total)}` : 'No events'}
            {stream ? ` · ${STREAM_LABELS[stream] || stream}` : ''}
          </span>
          <span className="flex items-center gap-1">
            <Button
              size="xs"
              variant="subtle"
              icon="chevrons-left"
              iconOnly
              aria-label="First page"
              disabled={!canPrev}
              onClick={() => { setFollow(false); setOffset(0); }}
            />
            <Button
              size="xs"
              variant="subtle"
              icon="chevron-left"
              iconOnly
              aria-label="Previous page"
              disabled={!canPrev}
              onClick={() => step(-1)}
            />
            <Button
              size="xs"
              variant="subtle"
              icon="chevron-right"
              iconOnly
              aria-label="Next page"
              disabled={!canNext}
              onClick={() => step(1)}
            />
            <Button
              size="xs"
              variant="subtle"
              icon="chevrons-right"
              iconOnly
              aria-label="Latest page"
              disabled={!canNext}
              onClick={() => setFollow(true)}
            />
            <Select
              value={limit}
              onChange={(v) => setLimit(Number(v))}
              options={PAGE_SIZES}
              size="xs"
              aria-label="Page size"
              className="ml-1"
            />
          </span>
        </PanelFooter>
      )}

      <Drawer
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        title={detail ? detail.type : ''}
        subtitle={detail ? `${STREAM_LABELS[detail.stream] || detail.stream} · seq ${detail.seq}` : ''}
        icon="logs"
        size={520}
      >
        {detail && (
          <div className="flex flex-col gap-3 p-3">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge tone={severityTone(detail.severity)} size="xs" icon={SEVERITY_META[detail.severity].icon}>
                {SEVERITY_META[detail.severity].label}
              </Badge>
              <Badge tone="neutral" size="xs">{detail.stream}</Badge>
            </div>

            <p className="text-hx-12 hx-mono text-hx-text-hi break-words">{detail.summary}</p>

            <div className="grid grid-cols-2 gap-2 pt-2 border-t border-hx-border-subtle">
              <div>
                <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Event time</div>
                <div className="text-hx-12 hx-mono hx-tnum text-hx-text-hi">
                  {fmtTime(nsToMs(detail.ts_event), { mode: 'datetime' })}
                </div>
                <div className="text-hx-10 text-hx-text-dim">when the fact was true</div>
              </div>
              <div>
                <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Recorded</div>
                <div className="text-hx-12 hx-mono hx-tnum text-hx-text-hi">
                  {fmtTime(nsToMs(detail.ts_recorded), { mode: 'datetime' })}
                </div>
                <div className="text-hx-10 text-hx-text-dim">when it was appended</div>
              </div>
            </div>

            <div className="pt-2 border-t border-hx-border-subtle">
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">Payload</span>
                <Button
                  size="xs"
                  variant="subtle"
                  icon="columns"
                  onClick={() => {
                    if (typeof navigator !== 'undefined' && navigator.clipboard) {
                      navigator.clipboard.writeText(prettyJson(detail.payload)).catch(() => {});
                    }
                  }}
                >
                  Copy
                </Button>
              </div>
              <pre className="text-hx-11 hx-mono text-hx-text-mid leading-relaxed whitespace-pre-wrap break-words p-2 rounded-md bg-hx-bg-base border border-hx-border-subtle max-h-[420px] overflow-auto hx-scroll">
                {prettyJson(detail.payload)}
              </pre>
            </div>
          </div>
        )}
      </Drawer>
    </Panel>
  );
}

export default LogsModule;
