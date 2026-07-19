/**
 * ConsoleDock — the bottom monitoring console.
 *
 * Tabs behave like an operations console rather than a dashboard widget: each
 * one sorts, filters and exports, and every row publishes its selection upward
 * so the chart and Copilot re-scope to whatever the operator clicked.
 *
 * Pause (Space) freezes the visible rows without stopping the poll, so a live
 * stream can be read without it scrolling out from under the cursor.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget, parseUtc } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  DataGrid,
  Drawer,
  EmptyState,
  Icon,
  Tabs,
  TONE_TEXT,
  cx,
  deltaTone,
  fmtCur,
  fmtNum,
  fmtQty,
  fmtTime,
} from '../../ui';

const TABS = [
  { id: 'trades', label: 'Recent Trades' },
  { id: 'orders', label: 'Orders' },
  { id: 'stream', label: 'Event Stream' },
  { id: 'alerts', label: 'Risk Alerts' },
  { id: 'brokers', label: 'Broker Messages' },
];

/** Status → tone, so a REJECTED row is scannable without reading the word. */
function statusTone(s) {
  const v = String(s || '').toUpperCase();
  if (v === 'COMPLETE' || v === 'PLACED') return 'pos';
  if (v === 'REJECTED' || v === 'CANCELLED') return 'neg';
  if (v === 'SIMULATED') return 'info';
  return 'neutral';
}

/* ---- tabs --------------------------------------------------------------- */

function TradesTab({ onSelectSymbol, onOpenRow }) {
  const { data, loading, error, refresh } = useLivePoll(
    jget(`${apiBase()}/api/v1/trades/history`),
    CADENCE.history,
  );
  const rows = data?.trades || [];

  const columns = useMemo(
    () => [
      { key: 'executed_at', header: 'Time', width: 80, render: (r) => fmtTime(parseUtc(r.executed_at) || r.executed_at) },
      { key: 'symbol', header: 'Symbol', width: 96, render: (r) => <span className="hx-mono text-hx-text-hi">{r.symbol}</span> },
      {
        key: 'side',
        header: 'Side',
        width: 56,
        render: (r) => (
          <Badge tone={String(r.side).toUpperCase() === 'SELL' ? 'neg' : 'pos'} size="xs">
            {String(r.side || '--').toUpperCase()}
          </Badge>
        ),
      },
      { key: 'quantity', header: 'Qty', width: 70, align: 'right', render: (r) => fmtQty(r.quantity) },
      { key: 'executed_price', header: 'Price', width: 90, align: 'right', render: (r) => fmtNum(r.executed_price ?? r.placed_price) },
      {
        key: 'status',
        header: 'Status',
        width: 90,
        render: (r) => (
          <Badge tone={statusTone(r.status)} size="xs">
            {r.status}
          </Badge>
        ),
      },
      { key: 'broker_name', header: 'Broker', width: 90, render: (r) => r.broker_name || '--' },
      {
        key: 'is_paper',
        header: 'Mode',
        width: 64,
        render: (r) => <span className="text-hx-10 text-hx-text-dim">{r.is_paper ? 'paper' : 'live'}</span>,
      },
    ],
    [],
  );

  if (error) {
    return (
      <EmptyState
        variant="error"
        title="Trade history unavailable"
        hint={String(error.message || error)}
        action={<Button size="xs" variant="subtle" onClick={refresh}>Retry</Button>}
      />
    );
  }

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      loading={loading && !data}
      defaultSort={{ key: 'executed_at', dir: 'desc' }}
      exportName="trades"
      columnChooser
      emptyTitle="No trades yet"
      emptyHint="Approved recommendations appear here once the broker acknowledges them."
      ariaLabel="Recent trades"
      onRowClick={(r) => {
        if (r.symbol && onSelectSymbol) onSelectSymbol(r.symbol);
        if (onOpenRow) onOpenRow({ kind: 'trade', row: r });
      }}
    />
  );
}

function OrdersTab({ onSelectSymbol, onOpenRow }) {
  const { data, loading, error, refresh } = useLivePoll(
    jget(`${apiBase()}/api/v1/trades/recommendations`),
    CADENCE.recommendations,
  );
  // This endpoint returns a bare array, not an envelope.
  const rows = Array.isArray(data) ? data : [];

  const columns = useMemo(
    () => [
      { key: 'created_at', header: 'Created', width: 80, render: (r) => fmtTime(parseUtc(r.created_at) || r.created_at) },
      { key: 'symbol', header: 'Symbol', width: 96, render: (r) => <span className="hx-mono text-hx-text-hi">{r.symbol}</span> },
      {
        key: 'side',
        header: 'Side',
        width: 56,
        render: (r) => (
          <Badge tone={r.side === 'sell' ? 'neg' : 'pos'} size="xs">
            {String(r.side || '').toUpperCase()}
          </Badge>
        ),
      },
      { key: 'entry_price', header: 'Entry', width: 84, align: 'right', render: (r) => fmtNum(r.entry_price) },
      { key: 'target_price', header: 'Target', width: 84, align: 'right', render: (r) => fmtNum(r.target_price) },
      { key: 'stop_loss', header: 'Stop', width: 84, align: 'right', render: (r) => fmtNum(r.stop_loss) },
      { key: 'quantity', header: 'Qty', width: 64, align: 'right', render: (r) => fmtQty(r.quantity) },
      {
        key: 'confidence_score',
        header: 'Conf',
        width: 60,
        align: 'right',
        render: (r) => <span className="hx-mono">{Math.round((r.confidence_score || 0) * 100)}%</span>,
      },
      { key: 'risk_reward_ratio', header: 'R:R', width: 56, align: 'right', render: (r) => `1:${fmtNum(r.risk_reward_ratio, { dp: 1 })}` },
      { key: 'status', header: 'Status', width: 120, render: (r) => <Badge tone="warn" size="xs">{r.status}</Badge> },
    ],
    [],
  );

  if (error) {
    return (
      <EmptyState
        variant="error"
        title="Recommendations unavailable"
        hint={String(error.message || error)}
        action={<Button size="xs" variant="subtle" onClick={refresh}>Retry</Button>}
      />
    );
  }

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      loading={loading && !data}
      exportName="recommendations"
      columnChooser
      emptyTitle="No pending recommendations"
      emptyHint="The engine publishes proposals here for approval."
      ariaLabel="Pending orders"
      onRowClick={(r) => {
        if (r.symbol && onSelectSymbol) onSelectSymbol(r.symbol);
        if (onOpenRow) onOpenRow({ kind: 'recommendation', row: r });
      }}
    />
  );
}

/** Client-side activity stream from the workspace store — see store.consoleLines. */
function StreamTab({ lines, paused, onClear, onOpenRow }) {
  const frozen = useRef(lines);
  if (!paused) frozen.current = lines;
  const rows = frozen.current;

  const columns = useMemo(
    () => [
      { key: 'at', header: 'Time', width: 80, render: (r) => fmtTime(r.at) },
      {
        key: 'level',
        header: 'Severity',
        width: 80,
        render: (r) => (
          <Badge tone={r.level === 'error' ? 'neg' : r.level === 'warn' ? 'warn' : 'info'} size="xs">
            {r.level}
          </Badge>
        ),
      },
      { key: 'message', header: 'Event', render: (r) => <span className="text-hx-text-mid">{r.message}</span> },
    ],
    [],
  );

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      defaultSort={{ key: 'at', dir: 'desc' }}
      exportName="event-stream"
      emptyTitle="No workspace events yet"
      emptyHint="Selections, approvals and errors are recorded here as you work."
      ariaLabel="Event stream"
      toolbar={
        rows.length ? (
          <Button size="xs" variant="subtle" onClick={onClear}>
            Clear
          </Button>
        ) : null
      }
      onRowClick={(r) => onOpenRow && onOpenRow({ kind: 'event', row: r })}
    />
  );
}

function AlertsTab({ onOpenRow }) {
  const { data, loading } = useLivePoll(jget(`${apiBase()}/api/v1/risk/limits`), CADENCE.risk);

  // Alerts are derived from live limit utilisation — the backend has no alert
  // feed, so these are computed thresholds, labelled as such.
  const rows = useMemo(() => {
    if (!data) return [];
    const out = [];
    const pnl = data.today_realized_pnl_inr ?? 0;
    const lossCap = data.daily_max_loss_inr ?? 0;
    const used = Math.max(0, -pnl);
    if (data.kill_switch) {
      out.push({ id: 'kill', severity: 'critical', title: 'Kill switch engaged', detail: 'Order release halted platform-wide.' });
    }
    if (lossCap > 0 && used / lossCap >= 0.75) {
      out.push({
        id: 'loss',
        severity: used / lossCap >= 0.9 ? 'critical' : 'high',
        title: 'Daily loss budget',
        detail: `${fmtCur(used, { ccy: 'INR' })} of ${fmtCur(lossCap, { ccy: 'INR' })} consumed.`,
      });
    }
    const left = data.today_remaining_trades ?? null;
    if (left != null && left <= 2) {
      out.push({
        id: 'trades',
        severity: left === 0 ? 'high' : 'medium',
        title: 'Trade budget nearly spent',
        detail: `${left} trade${left === 1 ? '' : 's'} remaining today.`,
      });
    }
    return out;
  }, [data]);

  const columns = useMemo(
    () => [
      {
        key: 'severity',
        header: 'Severity',
        width: 90,
        render: (r) => (
          <Badge tone={r.severity === 'critical' ? 'neg' : r.severity === 'high' ? 'warn' : 'info'} size="xs">
            {r.severity}
          </Badge>
        ),
      },
      { key: 'title', header: 'Alert', width: 200, render: (r) => <span className="text-hx-text-hi">{r.title}</span> },
      { key: 'detail', header: 'Detail', render: (r) => <span className="text-hx-text-mid">{r.detail}</span> },
    ],
    [],
  );

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      loading={loading && !data}
      emptyTitle="No active risk alerts"
      emptyHint="Thresholds are evaluated from live limit utilisation."
      ariaLabel="Risk alerts"
      onRowClick={(r) => onOpenRow && onOpenRow({ kind: 'alert', row: r })}
    />
  );
}

function BrokersTab() {
  const { data, loading } = useLivePoll(jget(`${apiBase()}/api/v1/brokers/accounts`), CADENCE.accounts);
  const rows = Array.isArray(data) ? data : data?.accounts || [];

  const columns = useMemo(
    () => [
      { key: 'broker_name', header: 'Broker', width: 120, render: (r) => <span className="text-hx-text-hi">{r.broker_name}</span> },
      { key: 'status', header: 'Status', width: 110, render: (r) => <Badge tone={String(r.status).toUpperCase() === 'CONNECTED' ? 'pos' : 'warn'} size="xs">{r.status}</Badge> },
      { key: 'account_id', header: 'Account', width: 140, render: (r) => <span className="hx-mono text-hx-text-mid">{r.account_id || '--'}</span> },
      { key: 'is_paper', header: 'Mode', width: 70, render: (r) => (r.is_paper ? 'paper' : 'live') },
      { key: 'last_error', header: 'Last message', render: (r) => <span className="text-hx-text-lo">{r.last_error || '--'}</span> },
    ],
    [],
  );

  return (
    <DataGrid
      columns={columns}
      rows={rows}
      loading={loading && !data}
      emptyTitle="No brokers connected"
      emptyHint="Connect a broker from Settings to route orders."
      ariaLabel="Broker messages"
    />
  );
}

/* ---- dock --------------------------------------------------------------- */

export function ConsoleDock({ onSelectSymbol, consoleLines = [], onClearConsole }) {
  const [tab, setTab] = useState('trades');
  const [paused, setPaused] = useState(false);
  const [detail, setDetail] = useState(null);

  // Space pauses the stream — but only while the console has focus, so it can
  // never swallow a space typed into the Copilot composer.
  const rootRef = useRef(null);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return undefined;
    const onKey = (e) => {
      if (e.key !== ' ') return;
      const t = e.target;
      const tag = t?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || t?.isContentEditable) return;
      e.preventDefault();
      setPaused((v) => !v);
    };
    el.addEventListener('keydown', onKey);
    return () => el.removeEventListener('keydown', onKey);
  }, []);

  const openRow = useCallback((d) => setDetail(d), []);

  const body = {
    trades: <TradesTab onSelectSymbol={onSelectSymbol} onOpenRow={openRow} />,
    orders: <OrdersTab onSelectSymbol={onSelectSymbol} onOpenRow={openRow} />,
    stream: <StreamTab lines={consoleLines} paused={paused} onClear={onClearConsole} onOpenRow={openRow} />,
    alerts: <AlertsTab onOpenRow={openRow} />,
    brokers: <BrokersTab />,
  }[tab];

  return (
    <div ref={rootRef} tabIndex={-1} className="flex h-full min-h-0 flex-col outline-none">
      <Tabs
        tabs={TABS}
        value={tab}
        onChange={setTab}
        idPrefix="console"
        right={
          <div className="flex items-center gap-1.5 pr-2">
            {tab === 'stream' && (
              <button
                type="button"
                onClick={() => setPaused((v) => !v)}
                aria-pressed={paused}
                title={paused ? 'Resume stream (Space)' : 'Pause stream (Space)'}
                className={cx(
                  'hx-focus inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-hx-10',
                  paused ? 'bg-hx-warn-500/15 text-hx-warn-300' : 'text-hx-text-dim hover:text-hx-text-mid',
                )}
              >
                <Icon name={paused ? 'play' : 'pause'} size={11} />
                {paused ? 'Paused' : 'Live'}
              </button>
            )}
          </div>
        }
      />
      <div className="hx-scroll min-h-0 flex-1 overflow-auto">{body}</div>

      <Drawer
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        side="right"
        title={detail ? `${detail.kind} detail` : ''}
      >
        {detail && (
          <pre className="hx-scroll overflow-auto whitespace-pre-wrap break-words hx-mono text-hx-10 text-hx-text-lo">
            {JSON.stringify(detail.row, null, 2)}
          </pre>
        )}
      </Drawer>
    </div>
  );
}

export default ConsoleDock;
