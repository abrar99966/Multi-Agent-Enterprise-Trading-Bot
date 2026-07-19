/**
 * Orders — the recommendation approval surface.
 *
 * This is the one module that can move real money, so the flow is deliberately
 * three steps: select → preview (server-priced, shows broker + paper/live) →
 * typed confirmation. A live order requires the operator to type APPROVE; a
 * single mis-click can never reach a broker.
 */
import React, { useCallback, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget, jpost } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  DataGrid,
  Drawer,
  EmptyState,
  Icon,
  Panel,
  PanelBody,
  PanelHeader,
  Timeline,
  TONE_TEXT,
  cx,
  deltaTone,
  fmtCur,
  fmtNum,
  fmtQty,
  fmtTime,
} from '../../ui';

function KV({ k, v, tone }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className="text-hx-11 text-hx-text-lo">{k}</span>
      <span className={cx('hx-mono text-hx-11 tabular-nums', tone ? TONE_TEXT[tone] : 'text-hx-text-hi')}>{v}</span>
    </div>
  );
}

/** Preview + typed confirmation. Nothing here fires without an explicit act. */
function ApprovalDrawer({ rec, onClose, onDone, log }) {
  const [preview, setPreview] = useState(null);
  const [stage, setStage] = useState('idle'); // idle | previewing | ready | sending | done
  const [typed, setTyped] = useState('');
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const loadPreview = useCallback(async () => {
    setStage('previewing');
    setError(null);
    try {
      const p = await jget(`${apiBase()}/api/v1/trades/${rec.id}/preview`)(undefined);
      setPreview(p);
      setStage('ready');
    } catch (e) {
      setError(e.message);
      setStage('idle');
    }
  }, [rec.id]);

  const isLive = preview ? preview.is_paper === false : true;
  const confirmWord = isLive ? 'APPROVE' : 'OK';
  const canSend = stage === 'ready' && typed.trim().toUpperCase() === confirmWord;

  const send = useCallback(async () => {
    if (!canSend) return;
    setStage('sending');
    setError(null);
    try {
      const r = await jpost(`/api/v1/trades/${rec.id}/approve`);
      setResult(r);
      setStage('done');
      log && log('info', `Order approved · ${rec.symbol} · ${r.status} · ${r.order_id || 'no id'}`);
      onDone && onDone();
    } catch (e) {
      setError(e.message);
      setStage('ready');
    }
  }, [canSend, rec.id, rec.symbol, log, onDone]);

  const reject = useCallback(async () => {
    setStage('sending');
    try {
      await jpost(`/api/v1/trades/${rec.id}/reject`);
      log && log('warn', `Recommendation rejected · ${rec.symbol}`);
      onDone && onDone();
      onClose();
    } catch (e) {
      setError(e.message);
      setStage('ready');
    }
  }, [rec.id, rec.symbol, log, onDone, onClose]);

  return (
    <Drawer
      open
      onClose={onClose}
      side="right"
      size={440}
      title={`${String(rec.side || '').toUpperCase()} ${rec.symbol}`}
      subtitle={`recommendation #${rec.id}`}
      footer={
        stage === 'done' ? (
          <Button variant="subtle" onClick={onClose}>
            Close
          </Button>
        ) : (
          <div className="flex w-full items-center justify-between gap-2">
            <Button variant="danger" size="sm" onClick={reject} disabled={stage === 'sending'}>
              Reject
            </Button>
            {stage === 'ready' ? (
              <Button variant="primary" size="sm" onClick={send} disabled={!canSend} loading={stage === 'sending'}>
                Send order
              </Button>
            ) : (
              <Button variant="primary" size="sm" onClick={loadPreview} loading={stage === 'previewing'}>
                Preview
              </Button>
            )}
          </div>
        )
      }
    >
      <div className="space-y-3">
        <section>
          <KV k="Entry" v={fmtNum(rec.entry_price)} />
          <KV k="Target" v={fmtNum(rec.target_price)} tone="pos" />
          <KV k="Stop loss" v={fmtNum(rec.stop_loss)} tone="neg" />
          <KV k="Quantity" v={fmtQty(rec.quantity)} />
          <KV k="Risk / reward" v={`1:${fmtNum(rec.risk_reward_ratio, { dp: 2 })}`} />
          <KV k="Confidence" v={`${Math.round((rec.confidence_score || 0) * 100)}%`} />
          <KV k="Expires" v={rec.expires_at ? fmtTime(rec.expires_at, { mode: 'datetime' }) : '--'} />
        </section>

        {rec.reasoning && (
          <section>
            <h4 className="mb-1 text-hx-10 uppercase tracking-wider text-hx-text-dim">Reasoning</h4>
            <p className="whitespace-pre-wrap text-hx-11 leading-relaxed text-hx-text-mid">{rec.reasoning}</p>
          </section>
        )}

        {preview && (
          <section className="rounded border border-hx-border-subtle bg-hx-bg-sunken p-2">
            <h4 className="mb-1 text-hx-10 uppercase tracking-wider text-hx-text-dim">Server preview</h4>
            <KV k="Broker" v={preview.broker_label || preview.broker} />
            <KV k="Mode" v={preview.is_paper ? 'paper' : 'LIVE'} tone={preview.is_paper ? 'neutral' : 'neg'} />
            <KV k="Order type" v={`${preview.order?.order_type} ${preview.order?.product}`} />
            <KV k="Price" v={fmtNum(preview.order?.price)} />
            <KV k="Estimated cost" v={fmtCur(preview.estimated_cost, { ccy: 'INR' })} />
            {preview.warning && (
              <p className="mt-1.5 flex items-start gap-1.5 text-hx-10 text-hx-warn-300">
                <Icon name="alert" size={12} className="mt-0.5 shrink-0" />
                {preview.warning}
              </p>
            )}
          </section>
        )}

        {stage === 'ready' && (
          <section
            className={cx(
              'rounded border p-2',
              isLive ? 'border-hx-neg-500/40 bg-hx-neg-500/10' : 'border-hx-border-subtle bg-hx-bg-sunken',
            )}
          >
            <p className="text-hx-11 text-hx-text-mid">
              {isLive
                ? 'This will place a LIVE order with real capital.'
                : 'This will place a simulated (paper) order.'}{' '}
              Type <span className="hx-mono text-hx-text-hi">{confirmWord}</span> to confirm.
            </p>
            <input
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              aria-label={`Type ${confirmWord} to confirm`}
              className="hx-focus mt-1.5 w-full rounded border border-hx-border-subtle bg-hx-bg-base px-2 py-1 hx-mono text-hx-12 text-hx-text-hi outline-none"
              placeholder={confirmWord}
            />
          </section>
        )}

        {result && (
          <section className="rounded border border-hx-pos-500/40 bg-hx-pos-500/10 p-2">
            <KV k="Status" v={result.status} tone="pos" />
            <KV k="Broker order" v={result.order_id || '--'} />
            <KV k="Trade id" v={result.trade_id ?? '--'} />
          </section>
        )}

        {error && (
          <p role="alert" className="rounded border border-hx-neg-500/40 bg-hx-neg-500/10 p-2 text-hx-11 text-hx-neg-300">
            {error}
          </p>
        )}
      </div>
    </Drawer>
  );
}

export function OrdersModule({ symbol, onSelectSymbol, log }) {
  const [openRec, setOpenRec] = useState(null);
  const { data, loading, error, refresh } = useLivePoll(
    jget(`${apiBase()}/api/v1/trades/recommendations`),
    CADENCE.recommendations,
  );
  const rows = Array.isArray(data) ? data : [];

  const columns = useMemo(
    () => [
      { key: 'symbol', header: 'Symbol', width: 100, render: (r) => <span className="hx-mono text-hx-text-hi">{r.symbol}</span> },
      {
        key: 'side',
        header: 'Side',
        width: 60,
        render: (r) => <Badge tone={r.side === 'sell' ? 'neg' : 'pos'} size="xs">{String(r.side || '').toUpperCase()}</Badge>,
      },
      { key: 'entry_price', header: 'Entry', width: 90, align: 'right', render: (r) => fmtNum(r.entry_price) },
      { key: 'target_price', header: 'Target', width: 90, align: 'right', render: (r) => fmtNum(r.target_price) },
      { key: 'stop_loss', header: 'Stop', width: 90, align: 'right', render: (r) => fmtNum(r.stop_loss) },
      { key: 'quantity', header: 'Qty', width: 70, align: 'right', render: (r) => fmtQty(r.quantity) },
      {
        key: 'confidence_score',
        header: 'Conf',
        width: 64,
        align: 'right',
        render: (r) => <span className="hx-mono">{Math.round((r.confidence_score || 0) * 100)}%</span>,
      },
      { key: 'risk_reward_ratio', header: 'R:R', width: 60, align: 'right', render: (r) => `1:${fmtNum(r.risk_reward_ratio, { dp: 1 })}` },
      { key: 'created_at', header: 'Created', width: 90, render: (r) => fmtTime(r.created_at) },
      {
        key: 'act',
        header: '',
        width: 84,
        sortable: false,
        render: (r) => (
          <Button
            size="xs"
            variant="primary"
            onClick={(e) => {
              e.stopPropagation();
              setOpenRec(r);
            }}
          >
            Review
          </Button>
        ),
      },
    ],
    [],
  );

  return (
    <div className="h-full min-h-0 p-2">
      <Panel className="flex h-full min-h-0 flex-col">
        <PanelHeader
          icon="orders"
          title="Pending recommendations"
          subtitle="every release passes the risk gateway"
          actions={
            <Button size="xs" variant="subtle" icon="refresh" onClick={refresh}>
              Refresh
            </Button>
          }
        />
        <PanelBody pad={false} className="min-h-0 flex-1">
          {error ? (
            <EmptyState
              variant="error"
              title="Recommendations unavailable"
              hint={String(error.message || error)}
              action={<Button size="xs" variant="subtle" onClick={refresh}>Retry</Button>}
            />
          ) : (
            <DataGrid
              columns={columns}
              rows={rows}
              loading={loading && !data}
              selectedKey={rows.find((r) => r.symbol === symbol)?.id}
              exportName="recommendations"
              columnChooser
              emptyTitle="Nothing awaiting approval"
              emptyHint="The engine publishes proposals here as they are generated."
              ariaLabel="Pending recommendations"
              onRowClick={(r) => onSelectSymbol && onSelectSymbol(r.symbol)}
            />
          )}
        </PanelBody>
      </Panel>

      {openRec && (
        <ApprovalDrawer
          rec={openRec}
          onClose={() => setOpenRec(null)}
          onDone={refresh}
          log={log}
        />
      )}
    </div>
  );
}

export default OrdersModule;
