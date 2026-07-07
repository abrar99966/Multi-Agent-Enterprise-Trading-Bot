import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

const API = (typeof window !== 'undefined' && window.__API__) || 'http://127.0.0.1:8000';

function useHiddenMarker() {
  useEffect(() => {
    const set = () => document.documentElement.dataset.hidden = String(document.hidden);
    set();
    document.addEventListener('visibilitychange', set);
    return () => document.removeEventListener('visibilitychange', set);
  }, []);
}

const REGION_BADGE = {
  IN: { label: 'India', cls: 'bg-amber-400/10 border-amber-400/30 text-amber-200' },
  US: { label: 'US', cls: 'bg-sky-400/10 border-sky-400/30 text-sky-200' },
  GLOBAL: { label: 'Global', cls: 'bg-violet-400/10 border-violet-400/30 text-violet-200' },
};

const STATUS_BADGE = {
  connected: { label: 'Connected', cls: 'bg-emerald-400/10 border-emerald-400/30 text-emerald-300', dot: 'bg-emerald-400' },
  disconnected: { label: 'Disconnected', cls: 'bg-white/5 border-white/10 text-white/60', dot: 'bg-white/40' },
  error: { label: 'Error', cls: 'bg-rose-400/10 border-rose-400/30 text-rose-300', dot: 'bg-rose-400' },
  expired: { label: 'Token expired', cls: 'bg-amber-400/10 border-amber-400/30 text-amber-200', dot: 'bg-amber-300' },
};

const BROKER_TOKEN_PAGE = {
  dhan: 'https://web.dhan.co/profile',
  zerodha: 'https://kite.zerodha.com/connect/login',
  upstox: 'https://api.upstox.com/v2/login/authorization/dialog',
  angelone: 'https://smartapi.angelbroking.com/',
  icici_breeze: 'https://api.icicidirect.com/apiuser/home',
};

function formatRemaining(sec) {
  if (sec == null) return null;
  if (sec <= 0) return 'expired';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h >= 1) return `${h}h ${m}m`;
  if (m >= 1) return `${m}m`;
  return `${sec}s`;
}

const fmtMoney = (v, ccy = 'INR') => {
  if (v === null || v === undefined || Number.isNaN(v)) return '--';
  const symbol = ccy === 'INR' ? '₹' : ccy === 'USD' ? '$' : `${ccy} `;
  return `${symbol}${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
};

const fmtRelative = (iso) => {
  if (!iso) return 'never';
  const d = new Date(iso);
  const s = Math.max(1, Math.round((Date.now() - d.getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return d.toLocaleDateString();
};

const BROKER_LOGO = (slug) => {
  // Simple stylized monogram tile; avoids shipping logo assets we don't own.
  const seed = slug.charCodeAt(0) + slug.charCodeAt(slug.length - 1);
  const hues = ['#e6c181', '#10d995', '#60a5fa', '#a78bfa', '#f43f5e', '#34d399', '#f59e0b'];
  const c = hues[seed % hues.length];
  const initials = slug.slice(0, 2).toUpperCase();
  return (
    <div
      className="w-11 h-11 rounded-xl flex items-center justify-center font-bold text-ink-900 tracking-tight"
      style={{ background: `linear-gradient(135deg, ${c}, ${c}aa)` }}
    >
      {initials}
    </div>
  );
};

function ConnectModal({ broker, onClose, onConnected }) {
  // Build initial form from the broker's declared field schema.
  const fields = Array.isArray(broker.fields) && broker.fields.length
    ? broker.fields
    : [
        { key: 'api_key',      label: 'API Key',      secret: false, required: true },
        { key: 'api_secret',   label: 'API Secret',   secret: true,  required: true },
        ...(broker.requires_access_token
          ? [{ key: 'access_token', label: 'Access Token', secret: true, required: true,
               hint: 'Daily OAuth token from the broker login redirect.' }]
          : []),
        { key: 'account_id',   label: 'Client / Account ID', required: false },
      ];

  const initial = useMemo(() => {
    const obj = { label: '', is_paper: !broker.live };  // live adapters default to LIVE mode
    fields.forEach((f) => { obj[f.key] = ''; });
    return obj;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [broker.slug]);

  const [form, setForm] = useState(initial);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const update = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true); setError(null);
    try {
      const res = await fetch(`${API}/api/v1/brokers/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ broker_name: broker.slug, ...form }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || 'Connection failed');
      onConnected(body);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm fade-in">
      <div className="glass rounded-2xl shadow-card w-full max-w-lg overflow-hidden">
        <header className="px-6 py-5 border-b border-white/5 flex items-center gap-3">
          {BROKER_LOGO(broker.slug)}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <div className="text-base font-semibold text-white truncate">Connect {broker.name}</div>
              {broker.live && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-400/15 border border-emerald-400/30 text-emerald-300 uppercase tracking-wider">Live</span>
              )}
              {broker.streams_market_data && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-sky-400/15 border border-sky-400/30 text-sky-300 uppercase tracking-wider">Data</span>
              )}
            </div>
            <div className="text-xs text-white/50 truncate">{broker.region} · {broker.asset_classes.join(' · ')}</div>
          </div>
          <button onClick={onClose} className="text-white/40 hover:text-white text-xl leading-none px-2">×</button>
        </header>

        <form onSubmit={submit} className="px-6 py-5 space-y-4">
          <div className="text-xs text-white/55 leading-relaxed border-l-2 border-gold-500/40 pl-3 italic">
            {broker.notes}
            <a href={broker.docs_url} target="_blank" rel="noreferrer" className="block mt-1 text-gold-300 hover:text-gold-200 not-italic">
              View {broker.name} docs →
            </a>
          </div>

          <Field label="Nickname (optional)" value={form.label} onChange={update('label')} placeholder={`My ${broker.name} account`} />
          {fields.map((f) => (
            <Field
              key={f.key}
              label={f.label}
              value={form[f.key] || ''}
              onChange={update(f.key)}
              placeholder={f.placeholder}
              required={f.required}
              secret={f.secret}
              hint={f.hint}
              mono
            />
          ))}

          {broker.live ? (
            <label className="flex items-start gap-2 text-xs text-white/70 cursor-pointer select-none p-2 rounded-lg bg-amber-400/5 border border-amber-400/20">
              <input type="checkbox" checked={form.is_paper} onChange={update('is_paper')}
                     className="w-4 h-4 accent-gold-500 mt-0.5" />
              <span>
                <span className="block font-medium text-amber-200">Paper mode (read-only)</span>
                <span className="block text-white/45">Uncheck to enable LIVE order routing. Account data + market data still fetch live either way.</span>
              </span>
            </label>
          ) : (
            <label className="flex items-center gap-2 text-sm text-white/75 cursor-pointer select-none">
              <input type="checkbox" checked={form.is_paper} onChange={update('is_paper')}
                     className="w-4 h-4 accent-gold-500" />
              Paper / sandbox mode (no real orders)
            </label>
          )}

          {error && (
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 px-3 py-2 text-sm text-rose-200">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 rounded-lg text-sm text-white/70 border border-white/10 hover:bg-white/5 transition">
              Cancel
            </button>
            <button type="submit" disabled={submitting}
              className="px-4 py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 disabled:opacity-50 shadow-glow transition">
              {submitting ? 'Testing connection…' : 'Test & Connect'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const Field = ({ label, value, onChange, placeholder, required, mono, secret, hint }) => (
  <label className="block">
    <span className="block text-[11px] uppercase tracking-wider text-white/55 mb-1">{label}{required && <span className="text-rose-400 ml-1">*</span>}</span>
    <input
      type={secret ? 'password' : 'text'}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      required={required}
      autoComplete="off"
      spellCheck={false}
      className={`w-full bg-white/[0.04] border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none focus:border-gold-500/60 transition ${mono ? 'font-mono' : ''}`}
    />
    {hint && <span className="block text-[11px] text-white/40 mt-1">{hint}</span>}
  </label>
);

function RefreshTokenInline({ acc, onDone, onCancel }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const r = await fetch(`${API}/api/v1/brokers/accounts/${acc.id}/refresh-token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || 'Token rejected');
      onDone(body);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  };

  const tokenPage = BROKER_TOKEN_PAGE[acc.broker_name];

  return (
    <form onSubmit={submit} className="mt-3 rounded-lg bg-white/[0.03] border border-white/10 p-3 space-y-2 fade-in">
      <div className="text-xs text-white/65">
        Paste a fresh access token from{' '}
        {tokenPage ? (
          <a href={tokenPage} target="_blank" rel="noreferrer"
             className="text-gold-300 hover:text-gold-200 underline underline-offset-2">
            {acc.broker_name} → Profile / API
          </a>
        ) : 'your broker portal'}
        . The old token will be replaced once the new one is verified.
      </div>
      <input
        type="password" value={token} onChange={(e) => setToken(e.target.value)}
        autoFocus required spellCheck={false} autoComplete="off"
        placeholder="Paste new access token…"
        className="w-full bg-ink-900/60 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-white/30 outline-none focus:border-gold-500/60 font-mono transition" />
      {error && <div className="text-xs text-rose-300">{error}</div>}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} disabled={busy}
          className="px-3 py-1.5 rounded-lg text-xs text-white/70 border border-white/10 hover:bg-white/5 transition">Cancel</button>
        <button type="submit" disabled={busy || !token.trim()}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 disabled:opacity-50 transition">
          {busy ? 'Verifying…' : 'Verify & Save'}
        </button>
      </div>
    </form>
  );
}

const AccountCard = memo(function AccountCard({ acc, onRefresh, onDisconnect, onTokenUpdated, busy }) {
  const status = STATUS_BADGE[acc.status] || STATUS_BADGE.disconnected;
  const [confirming, setConfirming] = useState(false);
  const [renewing, setRenewing] = useState(false);
  const timerRef = useRef(null);

  // Local countdown — re-renders this card every 30s, no other tree affected.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  // Reference 'tick' so React keeps the effect deps clean — we just want the re-render.
  void tick;

  useEffect(() => () => clearTimeout(timerRef.current), []);
  const askDisconnect = () => {
    setConfirming(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setConfirming(false), 4000);
  };
  const doDisconnect = () => { setConfirming(false); onDisconnect(acc); };

  // Compute live remaining seconds from the absolute expiry timestamp
  const remainingSec = useMemo(() => {
    if (!acc.token_expires_at) return null;
    return Math.max(0, Math.round((new Date(acc.token_expires_at).getTime() - Date.now()) / 1000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [acc.token_expires_at, tick]);

  const expired = remainingSec === 0;
  const expiringSoon = remainingSec != null && remainingSec > 0 && remainingSec < 3600; // <1h
  const tokenLabel = formatRemaining(remainingSec);

  return (
    <div className={`glass rounded-2xl p-5 shadow-card fade-in ${expired ? 'ring-1 ring-amber-400/30' : ''}`}>
      <div className="flex items-start gap-4 flex-wrap">
        {BROKER_LOGO(acc.broker_name)}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-semibold text-white truncate">{acc.label || acc.broker_name}</span>
            <span className={`flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border ${status.cls}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${status.dot}`} /> {status.label}
            </span>
            {acc.is_paper && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-blue-400/10 border border-blue-400/30 text-blue-200">Paper</span>
            )}
            {tokenLabel && (
              <span className={`text-[11px] px-2 py-0.5 rounded-full border tabular ${
                expired
                  ? 'bg-rose-400/10 border-rose-400/30 text-rose-200'
                  : expiringSoon
                  ? 'bg-amber-400/10 border-amber-400/30 text-amber-200'
                  : 'bg-white/5 border-white/10 text-white/55'
              }`}>
                token {expired ? 'expired — refresh now' : `expires in ${tokenLabel}`}
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-white/45 tabular truncate">
            {acc.account_id || '—'} · key {acc.api_key_masked || '—'} · synced {fmtRelative(acc.last_synced_at)}
          </div>
        </div>
        <div className="flex gap-2 ml-auto flex-wrap">
          <button onClick={() => setRenewing((r) => !r)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              expired || expiringSoon
                ? 'bg-gold-500 text-ink-900 hover:bg-gold-400 shadow-glow'
                : 'text-white/75 border border-white/10 hover:bg-white/5'
            }`}>
            {renewing ? 'Close' : 'Refresh Token'}
          </button>
          <button onClick={() => onRefresh(acc)} disabled={busy}
            className="px-3 py-1.5 rounded-lg text-xs text-white/75 border border-white/10 hover:bg-white/5 disabled:opacity-50 transition">
            Sync
          </button>
          {confirming ? (
            <button onClick={doDisconnect} disabled={busy}
              className="px-3 py-1.5 rounded-lg text-xs text-white font-semibold bg-rose-500 hover:bg-rose-400 disabled:opacity-50 transition">
              Confirm?
            </button>
          ) : (
            <button onClick={askDisconnect} disabled={busy}
              className="px-3 py-1.5 rounded-lg text-xs text-rose-300 border border-rose-400/30 hover:bg-rose-400/10 disabled:opacity-50 transition">
              Disconnect
            </button>
          )}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3 text-sm tabular">
        <Stat label="Balance" value={fmtMoney(acc.balance, acc.currency)} />
        <Stat label="Equity" value={fmtMoney(acc.equity, acc.currency)} tone={acc.equity >= acc.balance ? 'pos' : 'neg'} />
        <Stat label="Margin Avail." value={fmtMoney(acc.margin_available, acc.currency)} />
      </div>

      {renewing && (
        <RefreshTokenInline acc={acc}
          onDone={(updated) => { setRenewing(false); onTokenUpdated(updated); }}
          onCancel={() => setRenewing(false)} />
      )}

      {acc.last_error && !renewing && (
        <div className="mt-3 text-xs text-rose-300 bg-rose-500/5 border border-rose-500/20 rounded-lg px-3 py-2">
          {acc.last_error}
        </div>
      )}
    </div>
  );
});

const Stat = ({ label, value, tone }) => (
  <div className={`rounded-lg px-3 py-2 border ${
    tone === 'pos' ? 'bg-emerald-400/5 border-emerald-400/15' :
    tone === 'neg' ? 'bg-rose-400/5 border-rose-400/15' :
    'bg-white/[0.03] border-white/5'
  }`}>
    <div className="text-[10px] uppercase tracking-wider text-white/45">{label}</div>
    <div className={`${tone === 'pos' ? 'text-emerald-300' : tone === 'neg' ? 'text-rose-300' : 'text-white'}`}>{value}</div>
  </div>
);

export default function BrokersPage() {
  useHiddenMarker();
  const [supported, setSupported] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [providers, setProviders] = useState(null);
  const abortRef = useRef(null);

  useEffect(() => {
    const t = setInterval(() => {
      fetch(`${API}/api/v1/market-data/providers`).then((r) => r.json()).then(setProviders).catch(() => {});
    }, 30_000);
    fetch(`${API}/api/v1/market-data/providers`).then((r) => r.json()).then(setProviders).catch(() => {});
    return () => clearInterval(t);
  }, []);

  const dataIssues = providers?.blocked || [];

  const loadAll = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    try {
      const [s, a] = await Promise.all([
        fetch(`${API}/api/v1/brokers/supported`, { signal: ctrl.signal }).then((r) => r.json()),
        fetch(`${API}/api/v1/brokers/accounts`, { signal: ctrl.signal }).then((r) => r.json()),
      ]);
      setSupported(s.brokers || []);
      setAccounts(a.accounts || []);
    } catch (e) {
      if (e?.name !== 'AbortError') {
        // soft-fail to empty state
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
    return () => { if (abortRef.current) abortRef.current.abort(); };
  }, [loadAll]);

  const onRefresh = useCallback(async (acc) => {
    setBusyId(acc.id);
    try {
      const r = await fetch(`${API}/api/v1/brokers/accounts/${acc.id}/refresh`, { method: 'POST' });
      const updated = await r.json();
      if (r.ok) setAccounts((xs) => xs.map((x) => (x.id === acc.id ? updated : x)));
    } finally { setBusyId(null); }
  }, []);

  const onDisconnect = useCallback(async (acc) => {
    setBusyId(acc.id);
    try {
      const r = await fetch(`${API}/api/v1/brokers/accounts/${acc.id}`, { method: 'DELETE' });
      if (r.ok) setAccounts((xs) => xs.filter((x) => x.id !== acc.id));
    } finally { setBusyId(null); }
  }, []);

  const onTokenUpdated = useCallback((updated) => {
    setAccounts((xs) => xs.map((x) => (x.id === updated.id ? updated : x)));
  }, []);

  const visible = useMemo(
    () => supported.filter((b) => filter === 'all' || b.region === filter),
    [supported, filter]
  );

  const totals = useMemo(() => {
    const byCcy = {};
    let connected = 0;
    accounts.forEach((a) => {
      if (a.status === 'connected') connected += 1;
      const k = a.currency || 'INR';
      byCcy[k] = (byCcy[k] || 0) + (a.balance || 0);
    });
    return { connected, byCcy };
  }, [accounts]);

  return (
    <div className="min-h-screen text-white">
      <header className="sticky top-0 z-30 glass-blur">
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 py-3 flex items-center gap-4 sm:gap-6">
          <Link href="/" className="flex items-center gap-3 group min-w-0">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center shadow-glow shrink-0">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0e1a" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 17l6-6 4 4 8-8" /><path d="M14 7h7v7" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="text-sm font-semibold tracking-tight text-white group-hover:text-gold-300 transition truncate">Helios Capital</div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-white/45 truncate">AI Trading Desk</div>
            </div>
          </Link>
          <nav className="hidden sm:flex items-center gap-1 text-xs">
            <Link href="/" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Dashboard</Link>
            <span className="px-3 py-1.5 rounded-lg text-white bg-white/5 border border-white/10">Brokers</span>
            <Link href="/training" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Training</Link>
            <Link href="/screener" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Screener</Link>
            <Link href="/monitor" className="px-3 py-1.5 rounded-lg text-white/60 hover:text-white hover:bg-white/5 transition">Monitor</Link>
          </nav>
          <div className="ml-auto text-xs text-white/55 tabular truncate">
            {totals.connected} connected · {Object.entries(totals.byCcy).map(([c, v]) => fmtMoney(v, c)).join(' · ') || '—'}
          </div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-6 py-8 space-y-10">
        {/* Connected accounts */}
        <section>
          <div className="flex items-end justify-between mb-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Connected accounts</h1>
              <p className="text-sm text-white/55 mt-1">
                Your AI desk will only route orders to accounts you've explicitly approved here.
              </p>
            </div>
            <button onClick={loadAll}
              className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/[0.08] transition">
              Reload
            </button>
          </div>

          {loading && <div className="text-white/50 text-sm">Loading…</div>}

          {!loading && accounts.length === 0 && (
            <div className="glass rounded-2xl px-6 py-12 text-center">
              <div className="text-white/65 text-sm">No broker accounts connected yet.</div>
              <div className="text-white/40 text-xs mt-1">Pick a broker below to wire up live data and order routing.</div>
            </div>
          )}

          {dataIssues.length > 0 && (
            <div className="rounded-2xl px-5 py-4 mb-4 border border-amber-400/30 bg-amber-400/5">
              <div className="flex items-start gap-3">
                <span className="text-amber-300 text-lg leading-none">⚠</span>
                <div className="text-sm text-amber-100">
                  <div className="font-semibold mb-1">
                    Connected, but Data API plan is not active on {dataIssues.map((p) => p.spec_name).join(', ')}.
                  </div>
                  <div className="text-xs text-amber-100/80 leading-relaxed">
                    {providers?.blocked_note || 'Market quotes are routing through Yahoo (15-min delayed) until the Data API plan is enabled.'}
                  </div>
                  {dataIssues.some((p) => p.broker_name === 'dhan') && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      <a href="https://web.dhan.co/api-subscription" target="_blank" rel="noreferrer"
                         className="text-xs px-3 py-1.5 rounded-lg bg-amber-400/15 border border-amber-400/30 text-amber-200 hover:bg-amber-400/25 transition">
                        Subscribe to Dhan Data API →
                      </a>
                      <a href="https://upstox.com/developer/api-documentation" target="_blank" rel="noreferrer"
                         className="text-xs px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/15 text-white/75 hover:bg-white/[0.08] transition">
                        Or use Upstox (free real-time data)
                      </a>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-4">
            {accounts.map((a) => (
              <AccountCard key={a.id} acc={a} onRefresh={onRefresh} onDisconnect={onDisconnect}
                onTokenUpdated={onTokenUpdated} busy={busyId === a.id} />
            ))}
          </div>
        </section>

        {/* Supported brokers catalog */}
        <section>
          <div className="flex items-end justify-between mb-4 flex-wrap gap-3">
            <div>
              <h2 className="text-xl font-semibold tracking-tight">Add a broker</h2>
              <p className="text-sm text-white/55 mt-1">Indian and international brokers supported out of the box.</p>
            </div>
            <div className="flex gap-1.5">
              {['all', 'IN', 'US', 'GLOBAL'].map((r) => (
                <button key={r} onClick={() => setFilter(r)}
                  className={`text-xs px-3 py-1.5 rounded-lg border transition ${
                    filter === r
                      ? 'bg-gold-500 text-ink-900 border-gold-500'
                      : 'bg-white/[0.03] text-white/65 border-white/10 hover:bg-white/[0.08]'
                  }`}>
                  {r === 'all' ? 'All regions' : (REGION_BADGE[r]?.label || r)}
                </button>
              ))}
            </div>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {visible
              .slice()
              .sort((a, b) => Number(b.live) - Number(a.live)) // live adapters first
              .map((b) => {
                const region = REGION_BADGE[b.region] || REGION_BADGE.GLOBAL;
                return (
                  <div key={b.slug} className={`glass rounded-2xl p-5 shadow-card flex flex-col ${b.live ? 'ring-1 ring-emerald-400/20' : ''}`}>
                    <div className="flex items-start gap-3">
                      {BROKER_LOGO(b.slug)}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-base font-semibold text-white truncate">{b.name}</span>
                          <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full border ${region.cls}`}>
                            {region.label}
                          </span>
                          {b.live && (
                            <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-400/15 border border-emerald-400/30 text-emerald-300">Live SDK</span>
                          )}
                          {b.streams_market_data && (
                            <span className="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-sky-400/15 border border-sky-400/30 text-sky-300">Live data</span>
                          )}
                        </div>
                        <div className="mt-1 text-[11px] text-white/45 uppercase tracking-wider">
                          {b.asset_classes.join(' · ')}
                        </div>
                      </div>
                    </div>
                    <p className="mt-3 text-xs text-white/55 leading-relaxed flex-1">{b.notes}</p>
                    <button onClick={() => setModal(b)}
                      className="mt-4 w-full py-2 rounded-lg text-sm font-semibold text-ink-900 bg-gold-500 hover:bg-gold-400 shadow-glow transition">
                      Connect
                    </button>
                  </div>
                );
              })}
          </div>
        </section>

        <section className="glass rounded-2xl p-5">
          <div className="text-sm font-semibold text-white mb-2">Security & next steps</div>
          <ul className="text-xs text-white/55 leading-relaxed space-y-1.5 list-disc list-inside">
            <li>Credentials are encrypted at rest with Fernet (AES-128-CBC + HMAC). Set <code className="text-gold-300">BROKER_ENC_KEY</code> in your environment for production.</li>
            <li>The shipped adapters run in <strong>sandbox</strong> — they validate credentials and simulate balances. Swap the body of each adapter in <code className="text-gold-300">backend/app/services/broker_adapters.py</code> with the broker's SDK to route real orders.</li>
            <li>No trade is ever placed without an explicit Approve in the dashboard.</li>
          </ul>
        </section>
      </main>

      {modal && (
        <ConnectModal
          broker={modal}
          onClose={() => setModal(null)}
          onConnected={(acc) => { setAccounts((xs) => [acc, ...xs]); setModal(null); }}
        />
      )}
    </div>
  );
}
