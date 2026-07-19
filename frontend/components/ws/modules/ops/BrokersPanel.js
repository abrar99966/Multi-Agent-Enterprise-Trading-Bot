/**
 * BrokersPanel — broker connection cards: status, balances, live token-expiry
 * countdown, reconnect / rotate-token / disconnect, and a connect form.
 *
 * Carries over the capability of pages/brokers.js without importing or mutating
 * it. Credentials are write-only: the form posts them and forgets them; the only
 * key ever rendered is the server's `api_key_masked`.
 *
 * SEBI mandates daily broker-token expiry at 06:00 IST (00:30 UTC) for Indian
 * brokers, which is why a countdown is first-class chrome here rather than a
 * detail — a trader needs to know the session dies before it does, not after.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelBody,
  Button, Badge, StatusChip, Icon, Drawer, EmptyState, Skeleton,
  fmtCur, fmtTime, cx,
} from '../../ui';
import { apiBase, jget, jpost, jdel, parseUtc, fmtCountdown, OPS_CADENCE } from './opsApi';

/** Backend status → StatusChip key. Server already folds expiry into `status`. */
const STATUS_CHIP = {
  connected: 'connected',
  expired: 'stale',
  error: 'error',
  disconnected: 'offline',
};

/** Re-render on an interval so countdowns tick without refetching. */
function useTick(active, ms = 1000) {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => bump((n) => n + 1), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}

/**
 * Seconds until token expiry, recomputed from the absolute timestamp on every
 * tick. `token_seconds_remaining` is only a snapshot from fetch time, so using
 * it directly would freeze the countdown between 60s polls.
 */
function secondsLeft(acc) {
  const exp = parseUtc(acc?.token_expires_at);
  if (exp) return Math.max(0, Math.round((exp.getTime() - Date.now()) / 1000));
  const snap = acc?.token_seconds_remaining;
  return Number.isFinite(Number(snap)) ? Number(snap) : null;
}

function TokenCountdown({ acc }) {
  const left = secondsLeft(acc);
  useTick(left !== null);

  // Non-IN brokers (ibkr/alpaca/binance) have no computed expiry at all.
  if (left === null) {
    return <span className="text-hx-11 text-hx-text-dim">No session expiry</span>;
  }

  const expired = left <= 0;
  const urgent = left > 0 && left < 3600;
  const tone = expired ? 'text-hx-neg-400' : urgent ? 'text-hx-warn-400' : 'text-hx-text-mid';

  return (
    <span className="inline-flex items-center gap-1.5 min-w-0">
      <Icon
        name={expired ? 'alert' : 'clock'}
        size={12}
        className={cx('shrink-0', expired ? 'text-hx-neg-400' : urgent ? 'text-hx-warn-400' : 'text-hx-text-dim')}
      />
      <span className={cx('text-hx-11 hx-mono hx-tnum', tone)}>
        {expired ? 'Token expired' : fmtCountdown(left)}
      </span>
      {!expired && <span className="text-hx-10 text-hx-text-dim">to 06:00 IST</span>}
    </span>
  );
}

/* ---- connect form -------------------------------------------------------- */

/**
 * Form is driven entirely off spec.fields[]. Per the API contract neither
 * `auth_kind` nor `requires_access_token` reliably predicts which inputs a
 * broker needs (dhan/upstox expose access_token while reporting
 * requires_access_token:false), so fields[] is the only correct source.
 */
function ConnectForm({ spec, onDone, onCancel }) {
  const [values, setValues] = useState({});
  // Paper is the safe default. The wire schema defaults is_paper to FALSE (live),
  // so this is always sent explicitly rather than relying on the server default.
  const [isPaper, setIsPaper] = useState(true);
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const fields = spec?.fields || [];
  const missing = fields.filter((f) => f.required && !String(values[f.key] || '').trim());

  const submit = async (e) => {
    e.preventDefault();
    if (missing.length) return;
    setBusy(true);
    setError(null);
    try {
      const body = { broker_name: spec.slug, is_paper: isPaper };
      fields.forEach((f) => {
        const v = String(values[f.key] ?? '').trim();
        if (v) body[f.key] = v;
      });
      if (label.trim()) body.label = label.trim();
      const account = await jpost('/api/v1/brokers/connect', body);
      // Credentials are dropped the instant the request resolves — never stored.
      setValues({});
      onDone(account);
    } catch (err) {
      setError(err.message || 'Connection failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 p-3">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone="info" size="xs">{spec.region}</Badge>
        {spec.live ? (
          <Badge tone="pos" size="xs" icon="check">Live adapter</Badge>
        ) : (
          <Badge tone="warn" size="xs" icon="alert">Sandbox only</Badge>
        )}
        {(spec.asset_classes || []).map((a) => (
          <Badge key={a} tone="neutral" size="xs">{a}</Badge>
        ))}
      </div>

      {spec.notes && <p className="text-hx-11 text-hx-text-lo leading-relaxed">{spec.notes}</p>}

      {fields.map((f) => (
        <label key={f.key} className="flex flex-col gap-1">
          <span className="text-hx-11 text-hx-text-mid">
            {f.label || f.key}
            {f.required && <span className="text-hx-neg-400 ml-1" aria-hidden="true">*</span>}
          </span>
          <input
            type={f.secret ? 'password' : 'text'}
            required={f.required}
            autoComplete={f.secret ? 'new-password' : 'off'}
            spellCheck={false}
            placeholder={f.placeholder || ''}
            value={values[f.key] || ''}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            className="hx-focus h-8 px-2 rounded-md bg-hx-bg-base border border-hx-border-subtle text-hx-12 text-hx-text-hi hx-mono placeholder:text-hx-text-dim"
          />
          {f.hint && <span className="text-hx-10 text-hx-text-dim">{f.hint}</span>}
        </label>
      ))}

      <label className="flex flex-col gap-1">
        <span className="text-hx-11 text-hx-text-mid">Label (optional)</span>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder={spec.name}
          className="hx-focus h-8 px-2 rounded-md bg-hx-bg-base border border-hx-border-subtle text-hx-12 text-hx-text-hi placeholder:text-hx-text-dim"
        />
      </label>

      <label className="flex items-center gap-2 py-1 cursor-pointer">
        <input
          type="checkbox"
          checked={isPaper}
          onChange={(e) => setIsPaper(e.target.checked)}
          className="hx-focus h-3.5 w-3.5 accent-hx-accent-500"
        />
        <span className="text-hx-12 text-hx-text-mid">Paper trading (simulated fills)</span>
      </label>

      {!isPaper && (
        <div className="flex items-start gap-2 p-2 rounded-md border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
          <Icon name="alert" size={14} className="text-hx-neg-400 shrink-0 mt-px" />
          <span className="text-hx-11 text-hx-neg-300 leading-relaxed">
            Live mode places real orders against real capital.
          </span>
        </div>
      )}

      {error && (
        <div role="alert" className="flex items-start gap-2 p-2 rounded-md border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
          <Icon name="alert" size={14} className="text-hx-neg-400 shrink-0 mt-px" />
          <span className="text-hx-11 text-hx-neg-300 leading-relaxed">{error}</span>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <Button variant="subtle" onClick={onCancel} type="button">Cancel</Button>
        <Button variant="primary" type="submit" loading={busy} disabled={missing.length > 0} icon="plus">
          Connect
        </Button>
      </div>
    </form>
  );
}

/* ---- token rotation ------------------------------------------------------ */

function RotateTokenForm({ acc, onDone, onCancel }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await jpost(`/api/v1/brokers/accounts/${acc.id}/refresh-token`, {
        access_token: token.trim(),
      });
      setToken('');
      onDone(updated);
    } catch (err) {
      setError(err.message || 'Token rejected');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 p-3">
      <p className="text-hx-11 text-hx-text-lo leading-relaxed">
        The new token is validated against {acc.broker_name} before it is persisted, so a
        rejected token leaves the current session intact.
      </p>
      <label className="flex flex-col gap-1">
        <span className="text-hx-11 text-hx-text-mid">
          Access token<span className="text-hx-neg-400 ml-1" aria-hidden="true">*</span>
        </span>
        <input
          type="password"
          required
          minLength={10}
          autoComplete="new-password"
          spellCheck={false}
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="hx-focus h-8 px-2 rounded-md bg-hx-bg-base border border-hx-border-subtle text-hx-12 text-hx-text-hi hx-mono"
        />
      </label>
      {error && (
        <div role="alert" className="flex items-start gap-2 p-2 rounded-md border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
          <Icon name="alert" size={14} className="text-hx-neg-400 shrink-0 mt-px" />
          <span className="text-hx-11 text-hx-neg-300 leading-relaxed">{error}</span>
        </div>
      )}
      <div className="flex items-center justify-end gap-2 pt-1">
        <Button variant="subtle" onClick={onCancel} type="button">Cancel</Button>
        <Button variant="primary" type="submit" loading={busy} disabled={token.trim().length < 10} icon="refresh">
          Rotate token
        </Button>
      </div>
    </form>
  );
}

/* ---- account card -------------------------------------------------------- */

function AccountCard({ acc, busy, onRefresh, onRotate, onDisconnect }) {
  const [confirming, setConfirming] = useState(false);
  const ccy = acc.currency || 'INR';

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3">
      <div className="flex items-start justify-between gap-2 min-w-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-hx-13 font-semibold text-hx-text-hi truncate">
              {acc.label || acc.broker_name}
            </span>
            <Badge tone={acc.is_paper ? 'info' : 'warn'} size="xs">
              {acc.is_paper ? 'Paper' : 'Live'}
            </Badge>
          </div>
          <div className="text-hx-10 text-hx-text-dim hx-mono truncate mt-0.5">
            {acc.broker_name}
            {acc.account_id ? ` · ${acc.account_id}` : ''}
          </div>
        </div>
        <StatusChip status={STATUS_CHIP[acc.status] || 'offline'} label={acc.status} showIcon />
      </div>

      <div className="grid grid-cols-3 gap-2">
        {[
          ['Balance', acc.balance],
          ['Equity', acc.equity],
          ['Margin', acc.margin_available],
        ].map(([label, v]) => (
          <div key={label} className="min-w-0">
            <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo">{label}</div>
            <div className="text-hx-12 hx-mono hx-tnum text-hx-text-hi truncate">
              {fmtCur(v, { ccy })}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between gap-2 flex-wrap pt-1 border-t border-hx-border-subtle">
        <TokenCountdown acc={acc} />
        <span className="text-hx-10 text-hx-text-dim">
          {/* Naive-UTC string: parseUtc forces the zone before formatting. */}
          Synced {acc.last_synced_at ? fmtTime(parseUtc(acc.last_synced_at), { mode: 'rel' }) : '--'}
        </span>
      </div>

      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-hx-10 text-hx-text-lo shrink-0">Key</span>
        <span className="text-hx-11 hx-mono text-hx-text-mid truncate">
          {acc.api_key_masked || (
            <span className="text-hx-warn-400">unreadable — re-connect required</span>
          )}
        </span>
      </div>

      {acc.last_error && (
        <div role="alert" className="flex items-start gap-2 p-2 rounded border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
          <Icon name="alert" size={12} className="text-hx-neg-400 shrink-0 mt-px" />
          <span className="text-hx-10 text-hx-neg-300 leading-relaxed break-words">{acc.last_error}</span>
        </div>
      )}

      <div className="flex items-center gap-1.5 flex-wrap">
        <Button size="xs" icon="refresh" loading={busy === 'refresh'} onClick={() => onRefresh(acc)}>
          Refresh
        </Button>
        <Button size="xs" icon="clock" onClick={() => onRotate(acc)}>
          Rotate token
        </Button>
        <div className="flex-1" />
        {confirming ? (
          <>
            <span className="text-hx-10 text-hx-warn-400">Deletes stored credentials.</span>
            <Button size="xs" variant="subtle" onClick={() => setConfirming(false)}>Cancel</Button>
            <Button
              size="xs"
              variant="danger"
              loading={busy === 'delete'}
              onClick={() => { setConfirming(false); onDisconnect(acc); }}
            >
              Confirm
            </Button>
          </>
        ) : (
          <Button size="xs" variant="danger" icon="close" onClick={() => setConfirming(true)}>
            Disconnect
          </Button>
        )}
      </div>
    </div>
  );
}

/* ---- panel --------------------------------------------------------------- */

export function BrokersPanel({ className = '', embedded = false }) {
  const accountsUrl = `${apiBase()}/api/v1/brokers/accounts`;
  const supportedUrl = `${apiBase()}/api/v1/brokers/supported`;

  const accountsQ = useLivePoll(jget(accountsUrl), OPS_CADENCE.accounts, [accountsUrl]);
  // Static catalog — fetched once on mount at a long cadence, never tightly polled.
  const supportedQ = useLivePoll(jget(supportedUrl), 600000, [supportedUrl]);

  const [overrides, setOverrides] = useState({});
  const [removed, setRemoved] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [connectSpec, setConnectSpec] = useState(null);
  const [rotateAcc, setRotateAcc] = useState(null);

  /**
   * Mutations return the updated account, so results are merged locally rather
   * than triggering a refetch — the 60s poll reconciles anything we missed.
   */
  const merge = useCallback((acc) => {
    if (acc && acc.id != null) setOverrides((m) => ({ ...m, [acc.id]: acc }));
  }, []);

  const accounts = useMemo(() => {
    const base = accountsQ.data?.accounts || [];
    return base
      .filter((a) => !removed.includes(a.id))
      .map((a) => overrides[a.id] || a);
  }, [accountsQ.data, overrides, removed]);

  const specs = supportedQ.data?.brokers || [];

  const onRefresh = useCallback(async (acc) => {
    setBusyId(`${acc.id}:refresh`);
    try {
      // Returns 200 even on adapter failure, with status:'error' in the body —
      // so success is read from the payload, not the HTTP code.
      merge(await jpost(`/api/v1/brokers/accounts/${acc.id}/refresh`));
    } catch {
      /* transport failure: the poll will resurface real state */
    } finally {
      setBusyId(null);
    }
  }, [merge]);

  const onDisconnect = useCallback(async (acc) => {
    setBusyId(`${acc.id}:delete`);
    try {
      await jdel(`/api/v1/brokers/accounts/${acc.id}`);
      // DELETE hard-deletes the row despite reporting status:'disconnected'.
      setRemoved((xs) => [...xs, acc.id]);
    } catch {
      /* leave the card in place; poll reconciles */
    } finally {
      setBusyId(null);
    }
  }, []);

  const loading = accountsQ.loading && !accountsQ.data;
  const connected = accounts.filter((a) => a.status === 'connected').length;

  const body = (
    <>
      {loading && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
          {[0, 1].map((i) => (
            <div key={i} className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3 space-y-2">
              <Skeleton h={14} className="w-1/3" />
              <Skeleton h={28} className="w-full" />
              <Skeleton h={10} className="w-1/2" />
            </div>
          ))}
        </div>
      )}

      {!loading && accountsQ.error && (
        <EmptyState
          variant="error"
          title="Could not load broker accounts"
          hint={accountsQ.error.message}
          action={{ label: 'Retry', onClick: accountsQ.refresh }}
        />
      )}

      {!loading && !accountsQ.error && accounts.length === 0 && (
        <EmptyState
          title="No brokers connected"
          hint="Connect a broker to route orders. Until then the platform runs on Yahoo market data in paper mode."
          icon="portfolio"
        />
      )}

      {!loading && accounts.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-2">
          {accounts.map((acc) => (
            <AccountCard
              key={acc.id}
              acc={acc}
              busy={busyId === `${acc.id}:refresh` ? 'refresh' : busyId === `${acc.id}:delete` ? 'delete' : null}
              onRefresh={onRefresh}
              onRotate={setRotateAcc}
              onDisconnect={onDisconnect}
            />
          ))}
        </div>
      )}

      {specs.length > 0 && (
        <div className="mt-3 pt-3 border-t border-hx-border-subtle">
          <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo mb-2">
            Add a connection
          </div>
          <div className="flex flex-wrap gap-1.5">
            {specs.map((s) => (
              <Button key={s.slug} size="xs" icon="plus" onClick={() => setConnectSpec(s)}>
                {s.name}
              </Button>
            ))}
          </div>
        </div>
      )}

      <Drawer
        open={Boolean(connectSpec)}
        onClose={() => setConnectSpec(null)}
        title={connectSpec ? `Connect ${connectSpec.name}` : ''}
        subtitle="Credentials are encrypted server-side and never returned"
        icon="portfolio"
        size={440}
      >
        {connectSpec && (
          <ConnectForm
            spec={connectSpec}
            onCancel={() => setConnectSpec(null)}
            onDone={(acc) => { merge(acc); setConnectSpec(null); accountsQ.refresh(); }}
          />
        )}
      </Drawer>

      <Drawer
        open={Boolean(rotateAcc)}
        onClose={() => setRotateAcc(null)}
        title={rotateAcc ? `Rotate token — ${rotateAcc.label || rotateAcc.broker_name}` : ''}
        subtitle="Daily SEBI session renewal"
        icon="clock"
        size={420}
      >
        {rotateAcc && (
          <RotateTokenForm
            acc={rotateAcc}
            onCancel={() => setRotateAcc(null)}
            onDone={(acc) => { merge(acc); setRotateAcc(null); }}
          />
        )}
      </Drawer>
    </>
  );

  if (embedded) return <div className={className}>{body}</div>;

  return (
    <Panel className={cx('h-full', className)} loading={loading}>
      <PanelHeader
        title="Brokers"
        icon="portfolio"
        subtitle={accounts.length ? `${connected}/${accounts.length} connected` : undefined}
        actions={
          <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Reload accounts" onClick={accountsQ.refresh} />
        }
      />
      <PanelBody>{body}</PanelBody>
    </Panel>
  );
}

export default BrokersPanel;
