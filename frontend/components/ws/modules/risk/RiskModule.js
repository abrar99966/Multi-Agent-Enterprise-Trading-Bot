/**
 * Risk — platform health at a glance, and the controls that stop it.
 *
 * The kill switch is the most consequential control in the product, so it is
 * gated behind a typed confirmation and states its blast radius in words before
 * it will arm. Resuming is equally explicit: nothing here toggles on one click.
 */
import React, { useCallback, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import { CADENCE, apiBase, jget, jpost } from '../../../../lib/ws/api';
import {
  Badge,
  Button,
  EmptyState,
  Icon,
  MetricCard,
  Panel,
  PanelBody,
  PanelHeader,
  RiskIndicator,
  Skeleton,
  StatusChip,
  TONE_TEXT,
  cx,
  deltaTone,
  fmtCur,
  fmtNum,
  fmtTime,
} from '../../ui';

/** Typed-confirmation modal shared by arm/resume. */
function ConfirmKill({ arming, onCancel, onConfirm, busy, error }) {
  const word = arming ? 'HALT' : 'RESUME';
  const [typed, setTyped] = useState('');
  const ok = typed.trim().toUpperCase() === word;

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-6" role="dialog" aria-modal="true">
      <div className="w-full max-w-md rounded-lg border border-hx-border-strong bg-hx-bg-overlay p-4 shadow-hx-pop">
        <h3 className="flex items-center gap-2 text-hx-13 font-semibold text-hx-text-hi">
          <Icon name="kill" size={15} className={arming ? 'text-hx-neg-400' : 'text-hx-pos-400'} />
          {arming ? 'Engage kill switch' : 'Resume trading'}
        </h3>
        <p className="mt-2 text-hx-12 leading-relaxed text-hx-text-mid">
          {arming
            ? 'This halts order release platform-wide. Open positions are NOT closed — the switch stops new orders only.'
            : 'This re-enables order release. Positions and limits are unchanged; the desk resumes under existing risk caps.'}
        </p>
        <label className="mt-3 block text-hx-11 text-hx-text-lo">
          Type <span className="font-hx-mono text-hx-text-hi">{word}</span> to confirm
          <input
            autoFocus
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            className="hx-focus mt-1 w-full rounded border border-hx-border-subtle bg-hx-bg-base px-2 py-1 font-hx-mono text-hx-12 text-hx-text-hi outline-none"
            placeholder={word}
          />
        </label>
        {error && (
          <p role="alert" className="mt-2 rounded border border-hx-neg-500/40 bg-hx-neg-500/10 p-2 text-hx-11 text-hx-neg-300">
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="subtle" size="sm" onClick={onCancel}>
            Cancel
          </Button>
          <Button
            variant={arming ? 'danger' : 'primary'}
            size="sm"
            disabled={!ok}
            loading={busy}
            onClick={onConfirm}
          >
            {arming ? 'Halt trading' : 'Resume trading'}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function RiskModule({ log }) {
  const { data, loading, error, refresh } = useLivePoll(
    jget(`${apiBase()}/api/v1/risk/limits`),
    CADENCE.risk,
  );
  const { data: health } = useLivePoll(jget(`${apiBase()}/api/v1/performance/health`), CADENCE.health);

  const [confirm, setConfirm] = useState(null); // 'kill' | 'resume'
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);

  const act = useCallback(async () => {
    const arming = confirm === 'kill';
    setBusy(true);
    setActionError(null);
    try {
      await jpost(arming ? '/api/v1/risk/kill' : '/api/v1/risk/resume');
      log && log(arming ? 'error' : 'info', arming ? 'Kill switch ENGAGED' : 'Trading resumed');
      setConfirm(null);
      refresh();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  }, [confirm, log, refresh]);

  if (loading && !data) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (error) {
    return (
      <EmptyState
        variant="error"
        title="Risk service unavailable"
        hint={String(error.message || error)}
        action={<Button size="xs" variant="subtle" onClick={refresh}>Retry</Button>}
      />
    );
  }

  const killed = data?.kill_switch === true;
  const pnl = data?.today_realized_pnl_inr ?? 0;
  const lossCap = data?.daily_max_loss_inr ?? 0;
  const lossUsed = Math.max(0, -pnl);
  const tradesUsed = data?.today_trade_count ?? 0;
  const tradesMax = data?.daily_max_trades ?? 0;

  return (
    <div className="hx-scroll h-full min-h-0 space-y-2 overflow-y-auto p-2">
      {/* state banner — the single most important fact on the screen */}
      <div
        role={killed ? 'alert' : undefined}
        className={cx(
          'flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5',
          killed ? 'border-hx-neg-500/40 bg-hx-neg-500/10' : 'border-hx-pos-500/30 bg-hx-pos-500/[0.07]',
        )}
      >
        <div className="flex items-center gap-2.5">
          <Icon name={killed ? 'kill' : 'check'} size={18} className={killed ? 'text-hx-neg-400' : 'text-hx-pos-400'} />
          <div>
            <p className={cx('text-hx-13 font-semibold', killed ? 'text-hx-neg-300' : 'text-hx-pos-300')}>
              {killed ? 'Trading halted' : 'Order release active'}
            </p>
            <p className="text-hx-11 text-hx-text-lo">
              {killed
                ? 'The gateway is rejecting all new orders. Open positions are untouched.'
                : 'Orders release within the limits below, subject to autonomy tiers.'}
            </p>
          </div>
        </div>
        <Button
          variant={killed ? 'primary' : 'danger'}
          size="sm"
          icon="kill"
          onClick={() => setConfirm(killed ? 'resume' : 'kill')}
        >
          {killed ? 'Resume trading' : 'Engage kill switch'}
        </Button>
      </div>

      {/* headline numbers */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MetricCard
          label="Realised P&L"
          value={fmtCur(pnl, { ccy: 'INR', signed: true })}
          raw={pnl}
          tone={deltaTone(pnl)}
          period="today"
          flash
        />
        <MetricCard label="Loss cap" value={fmtCur(lossCap, { ccy: 'INR', compact: true })} period="daily" />
        <MetricCard label="Per-trade cap" value={fmtCur(data?.per_trade_max_inr, { ccy: 'INR', compact: true })} period="per order" />
        <MetricCard
          label="Trades left"
          value={fmtNum(data?.today_remaining_trades, { dp: 0 })}
          period={`of ${fmtNum(tradesMax, { dp: 0 })}`}
        />
      </div>

      {/* utilisation */}
      <Panel>
        <PanelHeader icon="risk" title="Limit utilisation" subtitle="live consumption against daily caps" />
        <PanelBody className="space-y-3">
          <RiskIndicator
            label="Daily loss budget"
            value={lossUsed}
            max={lossCap || 1}
            valueText={`${fmtCur(lossUsed, { ccy: 'INR' })} / ${fmtCur(lossCap, { ccy: 'INR' })}`}
          />
          <RiskIndicator
            label="Trade count"
            value={tradesUsed}
            max={tradesMax || 1}
            valueText={`${tradesUsed} / ${tradesMax}`}
          />
          <p className="text-hx-10 text-hx-text-dim">
            Reset {data?.today_reset_at ? fmtTime(data.today_reset_at, { mode: 'datetime' }) : '--'} · updated{' '}
            {data?.updated_at ? fmtTime(data.updated_at, { mode: 'rel' }) : '--'}
          </p>
        </PanelBody>
      </Panel>

      {/* agent health — the platform's own vital signs */}
      <Panel>
        <PanelHeader
          icon="analytics"
          title="Component health"
          actions={
            <StatusChip
              status={health ? 'connected' : 'stale'}
              label={health ? `${(health.agents || []).length} components` : 'probing'}
            />
          }
        />
        <PanelBody pad={false}>
          {health?.agents?.length ? (
            <ul className="divide-y divide-hx-border-subtle">
              {health.agents.map((a) => (
                <li key={a.name} className="flex items-center justify-between gap-3 px-3 py-1.5">
                  <span className="flex items-center gap-2">
                    <Badge tone={a.ok ? 'pos' : 'neg'} size="xs" dot>
                      {a.ok ? 'ok' : 'fail'}
                    </Badge>
                    <span className="text-hx-12 text-hx-text-mid">{a.name}</span>
                  </span>
                  <span className="flex items-center gap-3">
                    {a.error && <span className="max-w-[280px] truncate text-hx-10 text-hx-neg-300">{a.error}</span>}
                    <span className="font-hx-mono text-hx-11 text-hx-text-lo">{fmtNum(a.latency_ms, { dp: 0 })}ms</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="info" title="No component telemetry" hint="The health probe returned no agents." />
          )}
        </PanelBody>
      </Panel>

      {confirm && (
        <ConfirmKill
          arming={confirm === 'kill'}
          busy={busy}
          error={actionError}
          onCancel={() => {
            setConfirm(null);
            setActionError(null);
          }}
          onConfirm={act}
        />
      )}
    </div>
  );
}

export default RiskModule;
