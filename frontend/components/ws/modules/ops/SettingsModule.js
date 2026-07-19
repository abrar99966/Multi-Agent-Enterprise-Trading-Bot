/**
 * SettingsModule — appearance, data sources, brokers, risk defaults, keyboard
 * reference and build info.
 *
 * Everything here that mutates server state (risk limits, kill switch, broker
 * connections) is explicit and confirmed. Nothing auto-saves: a control that
 * silently changes a live trading limit on blur is a hazard, not a convenience.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useLivePoll } from '../../../../lib/useLivePoll';
import {
  Panel, PanelHeader, PanelBody,
  Tabs, TabPanel, Button, ButtonGroup, Badge, StatusChip, Icon,
  EmptyState, Skeleton, RiskIndicator,
  fmtCur, fmtQty, fmtTime, cx,
} from '../../ui';
import { Section, Field, Toggle, NumberInput, KeyCap } from './OpsField';
import { BrokersPanel } from './BrokersPanel';
import { apiBase, jget, jpost, parseUtc, OPS_CADENCE, useControllable } from './opsApi';

const STORAGE_KEY = 'hx.workspace.appearance';

const DENSITIES = {
  compact: { label: 'Compact', rowH: 24 },
  default: { label: 'Default', rowH: 28 },
  relaxed: { label: 'Relaxed', rowH: 34 },
};

const FONT_SCALES = [
  { value: 90, label: '90%' },
  { value: 100, label: '100%' },
  { value: 110, label: '110%' },
  { value: 125, label: '125%' },
];

/**
 * Keyboard reference. Exported so the shell can bind against the same source of
 * truth rather than the two drifting apart — this module documents, the shell
 * dispatches.
 */
export const SHORTCUTS = [
  { group: 'Navigation', items: [
    { keys: ['g', 'd'], label: 'Go to dashboard' },
    { keys: ['g', 'a'], label: 'Go to analytics' },
    { keys: ['g', 'l'], label: 'Go to logs' },
    { keys: ['g', 'r'], label: 'Go to replay' },
    { keys: ['g', 's'], label: 'Go to settings' },
  ] },
  { group: 'Command', items: [
    { keys: ['Ctrl', 'K'], label: 'Command palette' },
    { keys: ['/'], label: 'Focus search' },
    { keys: ['Esc'], label: 'Close drawer or clear focus' },
  ] },
  { group: 'Grids', items: [
    { keys: ['↑', '↓'], label: 'Move between rows' },
    { keys: ['Enter'], label: 'Open the selected row' },
    { keys: ['←', '→'], label: 'Move between tabs' },
    { keys: ['Home', 'End'], label: 'First / last tab' },
  ] },
  { group: 'Risk', items: [
    { keys: ['Ctrl', 'Shift', 'K'], label: 'Engage kill switch' },
    { keys: ['R'], label: 'Refresh the active panel' },
  ] },
];

/* ---- appearance ---------------------------------------------------------- */

function readStored() {
  if (typeof window === 'undefined') return { fontScale: 100, density: 'default' };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const p = raw ? JSON.parse(raw) : null;
    return {
      fontScale: FONT_SCALES.some((f) => f.value === p?.fontScale) ? p.fontScale : 100,
      density: DENSITIES[p?.density] ? p.density : 'default',
    };
  } catch {
    return { fontScale: 100, density: 'default' };
  }
}

function AppearanceSection({ appearance, onChange }) {
  const { fontScale, density } = appearance;

  return (
    <Section
      title="Appearance"
      icon="settings"
      description="Applies immediately and persists to this browser. Scale is applied with CSS zoom on the document root, so it scales the whole workspace uniformly rather than only text."
    >
      <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 divide-y divide-hx-border-subtle">
        <Field
          label="Interface scale"
          hint="For 4K displays where 100% reads small"
          control={
            <ButtonGroup
              size="xs"
              options={FONT_SCALES}
              value={fontScale}
              onChange={(v) => onChange({ ...appearance, fontScale: Number(v) })}
            />
          }
        />
        <Field
          label="Table density"
          hint="Row rhythm for grids and lists"
          control={
            <ButtonGroup
              size="xs"
              options={Object.entries(DENSITIES).map(([value, d]) => ({ value, label: d.label }))}
              value={density}
              onChange={(v) => onChange({ ...appearance, density: v })}
            />
          }
        />
        <Field
          label="Reset"
          hint="Back to 100% and default density"
          control={
            <Button
              size="xs"
              icon="refresh"
              disabled={fontScale === 100 && density === 'default'}
              onClick={() => onChange({ fontScale: 100, density: 'default' })}
            >
              Reset
            </Button>
          }
        />
      </div>
    </Section>
  );
}

/* ---- data sources -------------------------------------------------------- */

/**
 * The enrichment payload is MIXED-TYPE: three booleans and `openfigi`, which is
 * always the string 'keyless-ok (key raises rate limit)'. Rendering all four as
 * booleans would show OpenFIGI as a meaningless "true".
 */
function EnrichmentRow({ label, value, hint }) {
  const isBool = typeof value === 'boolean';
  const on = isBool ? value : true;

  return (
    <Field
      label={label}
      hint={hint}
      control={
        isBool ? (
          <StatusChip status={on ? 'connected' : 'paused'} label={on ? 'Enabled' : 'No key'} showIcon />
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <Icon name="info" size={12} className="text-hx-info-400 shrink-0" />
            <span className="text-hx-11 text-hx-info-400">{String(value)}</span>
          </span>
        )
      }
    />
  );
}

function DataSourcesSection() {
  const enrichUrl = `${apiBase()}/api/v1/slowpath/enrichment/status`;
  const provUrl = `${apiBase()}/api/v1/market-data/providers`;

  const enrichQ = useLivePoll(jget(enrichUrl), OPS_CADENCE.enrichment, [enrichUrl]);
  // Provider probing is a DB round-trip plus a live probe per 30s cache cycle —
  // deliberately fetched at a slow cadence, never tightly polled.
  const provQ = useLivePoll(jget(provUrl), 300000, [provUrl]);

  const e = enrichQ.data;
  const active = provQ.data?.active || [];
  const blocked = provQ.data?.blocked || [];

  return (
    <Section
      title="Data sources"
      icon="markets"
      description="Which enrichment providers are configured, and which brokers can serve market data directly."
      actions={
        <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Reload data sources" onClick={enrichQ.refresh} />
      }
    >
      <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 divide-y divide-hx-border-subtle">
        {enrichQ.loading && !e && (
          <div className="py-3 space-y-2">
            <Skeleton h={12} className="w-2/3" />
            <Skeleton h={12} className="w-1/2" />
          </div>
        )}

        {enrichQ.error && !e && (
          <div className="py-2">
            <EmptyState
              variant="error"
              title="Enrichment status unavailable"
              hint={enrichQ.error.message}
              action={{ label: 'Retry', onClick: enrichQ.refresh }}
            />
          </div>
        )}

        {e && (
          <>
            <EnrichmentRow label="Treasury yield curve" value={e.treasury_yield_curve} hint="Keyless — always available" />
            <EnrichmentRow label="FRED" value={e.fred} hint="Macro series, incl. VIX" />
            <EnrichmentRow label="Finnhub" value={e.finnhub} hint="Alternate quote provider" />
            <EnrichmentRow label="OpenFIGI" value={e.openfigi} hint="Symbology mapping" />
          </>
        )}
      </div>

      <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-hx-11 font-medium text-hx-text-mid">Market data routing</span>
          {provQ.loading && !provQ.data && <Skeleton h={12} className="w-20" />}
        </div>

        {provQ.data && active.length === 0 && blocked.length === 0 && (
          <div className="flex items-start gap-2">
            <Icon name="info" size={13} className="text-hx-info-400 shrink-0 mt-px" />
            <p className="text-hx-11 text-hx-text-lo leading-relaxed">
              No broker data API is connected, so every quote is served by the Yahoo Finance
              fallback — free, and roughly 15 minutes delayed for NSE and BSE. This is the normal
              unconfigured state, not an error.
            </p>
          </div>
        )}

        {active.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {active.map((p) => (
              <div key={p.broker_name} className="flex items-center justify-between gap-2 min-w-0">
                <span className="flex items-center gap-2 min-w-0">
                  <StatusChip status="connected" label={p.spec_name || p.broker_name} />
                  <span className="text-hx-11 text-hx-text-lo truncate">{p.covers}</span>
                </span>
                <Badge tone="info" size="xs">{p.region}</Badge>
              </div>
            ))}
          </div>
        )}

        {blocked.length > 0 && (
          <div className="flex flex-col gap-1.5 mt-2 pt-2 border-t border-hx-border-subtle">
            <span className="text-hx-10 uppercase tracking-wider text-hx-text-lo">
              Connected but no data API
            </span>
            {blocked.map((p) => (
              <div key={p.broker_name} className="flex items-center justify-between gap-2 min-w-0">
                <span className="flex items-center gap-2 min-w-0">
                  <StatusChip status="degraded" label={p.spec_name || p.broker_name} showIcon />
                  <span className="text-hx-11 text-hx-text-lo truncate">
                    Order routing only — quotes fall back to Yahoo
                  </span>
                </span>
                <Badge tone="warn" size="xs">{p.region}</Badge>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  );
}

/* ---- risk defaults ------------------------------------------------------- */

function RiskSection() {
  const url = `${apiBase()}/api/v1/risk/limits`;
  const { data, error, loading, refresh } = useLivePoll(jget(url), OPS_CADENCE.risk, [url]);

  const [override, setOverride] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(null);
  const [saveError, setSaveError] = useState(null);
  const [confirmKill, setConfirmKill] = useState(false);

  const limits = override || data;

  // Seed the draft once, then leave it alone — re-seeding on every 60s poll would
  // wipe whatever the user is mid-way through typing.
  useEffect(() => {
    if (limits && draft === null) {
      setDraft({
        per_trade_max_inr: limits.per_trade_max_inr,
        daily_max_loss_inr: limits.daily_max_loss_inr,
        daily_max_trades: limits.daily_max_trades,
      });
    }
  }, [limits, draft]);

  const dirty = useMemo(() => {
    if (!limits || !draft) return false;
    return (
      Number(draft.per_trade_max_inr) !== Number(limits.per_trade_max_inr)
      || Number(draft.daily_max_loss_inr) !== Number(limits.daily_max_loss_inr)
      || Number(draft.daily_max_trades) !== Number(limits.daily_max_trades)
    );
  }, [draft, limits]);

  const save = async () => {
    setBusy('save');
    setSaveError(null);
    try {
      const res = await jpost('/api/v1/risk/limits', {
        per_trade_max_inr: Number(draft.per_trade_max_inr),
        daily_max_loss_inr: Number(draft.daily_max_loss_inr),
        daily_max_trades: Number(draft.daily_max_trades),
      });
      // The write path returns the full limits object, so state is updated from
      // the response rather than refetching.
      setOverride(res);
      setDraft({
        per_trade_max_inr: res.per_trade_max_inr,
        daily_max_loss_inr: res.daily_max_loss_inr,
        daily_max_trades: res.daily_max_trades,
      });
    } catch (e) {
      setSaveError(e.message || 'Could not save limits');
    } finally {
      setBusy(null);
    }
  };

  const toggleKill = async (engage) => {
    setBusy('kill');
    setSaveError(null);
    try {
      setOverride(await jpost(engage ? '/api/v1/risk/kill' : '/api/v1/risk/resume'));
    } catch (e) {
      setSaveError(e.message || 'Could not change the kill switch');
    } finally {
      setBusy(null);
      setConfirmKill(false);
    }
  };

  if (loading && !limits) {
    return (
      <Section title="Risk defaults" icon="risk">
        <Skeleton h={140} rounded="rounded-lg" />
      </Section>
    );
  }

  if (error && !limits) {
    return (
      <Section title="Risk defaults" icon="risk">
        <EmptyState
          variant="error"
          title="Could not load risk limits"
          hint={error.message}
          action={{ label: 'Retry', onClick: refresh }}
        />
      </Section>
    );
  }

  if (!limits || !draft) return null;

  const tradesUsed = Number(limits.daily_max_trades) - Number(limits.today_remaining_trades);
  const lossBuffer = Number(limits.today_remaining_loss_buffer_inr);
  const lossUsed = Number(limits.daily_max_loss_inr) - lossBuffer;

  return (
    <Section
      title="Risk defaults"
      icon="risk"
      description="Applied to every order before it reaches a broker. Counters reset at 06:00 IST."
      actions={
        <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Reload risk limits" onClick={refresh} />
      }
    >
      {/* Kill switch first: it is the single most consequential control here. */}
      <div
        className={cx(
          'flex items-center justify-between gap-3 p-3 rounded-lg border',
          limits.kill_switch
            ? 'border-hx-neg-500/40 bg-hx-neg-500/[0.12]'
            : 'border-hx-border-subtle bg-hx-bg-raised',
        )}
      >
        <div className="flex items-start gap-2 min-w-0">
          <Icon
            name="kill"
            size={16}
            className={cx('shrink-0 mt-px', limits.kill_switch ? 'text-hx-neg-400' : 'text-hx-text-lo')}
          />
          <div className="min-w-0">
            <div className={cx('text-hx-12 font-semibold', limits.kill_switch ? 'text-hx-neg-300' : 'text-hx-text-hi')}>
              {limits.kill_switch ? 'Kill switch ENGAGED — all trading halted' : 'Kill switch released'}
            </div>
            <div className="text-hx-11 text-hx-text-lo leading-relaxed mt-0.5">
              {limits.kill_switch
                ? 'No orders will be placed until this is released.'
                : 'Releasing does not clear other gates — daily trade count and loss limits still apply.'}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {limits.kill_switch ? (
            <Button size="sm" icon="play" loading={busy === 'kill'} onClick={() => toggleKill(false)}>
              Resume
            </Button>
          ) : confirmKill ? (
            <>
              <Button size="sm" variant="subtle" onClick={() => setConfirmKill(false)}>Cancel</Button>
              <Button size="sm" variant="danger" loading={busy === 'kill'} onClick={() => toggleKill(true)}>
                Confirm halt
              </Button>
            </>
          ) : (
            <Button size="sm" variant="danger" icon="kill" onClick={() => setConfirmKill(true)}>
              Engage
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3 flex flex-col gap-3">
          <RiskIndicator
            label="Daily trades used"
            value={tradesUsed}
            max={limits.daily_max_trades}
            valueText={`${fmtQty(tradesUsed)} / ${fmtQty(limits.daily_max_trades)}`}
          />
          <RiskIndicator
            label="Daily loss budget used"
            value={lossUsed > 0 ? lossUsed : 0}
            max={limits.daily_max_loss_inr}
            valueText={`${fmtCur(lossUsed > 0 ? lossUsed : 0, { ccy: 'INR', compact: true })} / ${fmtCur(limits.daily_max_loss_inr, { ccy: 'INR', compact: true })}`}
          />
          <div className="flex items-center justify-between gap-2 pt-1 border-t border-hx-border-subtle">
            <span className="text-hx-11 text-hx-text-lo">Realised today</span>
            <span
              className={cx(
                'text-hx-12 hx-mono hx-tnum',
                Number(limits.today_realized_pnl_inr) < 0 ? 'text-hx-neg-400' : 'text-hx-pos-400',
              )}
            >
              {fmtCur(limits.today_realized_pnl_inr, { ccy: 'INR', signed: true })}
            </span>
          </div>
        </div>

        <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 divide-y divide-hx-border-subtle">
          <Field
            label="Max per trade"
            hint="Rejects any single order above this notional"
            control={
              <NumberInput
                value={draft.per_trade_max_inr}
                min={0}
                step={500}
                suffix="INR"
                ariaLabel="Maximum notional per trade in rupees"
                className="w-[150px]"
                onChange={(v) => setDraft((d) => ({ ...d, per_trade_max_inr: v }))}
              />
            }
          />
          <Field
            label="Max daily loss"
            hint="Halts trading once breached"
            control={
              <NumberInput
                value={draft.daily_max_loss_inr}
                min={0}
                step={500}
                suffix="INR"
                ariaLabel="Maximum daily loss in rupees"
                className="w-[150px]"
                onChange={(v) => setDraft((d) => ({ ...d, daily_max_loss_inr: v }))}
              />
            }
          />
          <Field
            label="Max daily trades"
            hint="0 to 1000"
            control={
              <NumberInput
                value={draft.daily_max_trades}
                min={0}
                max={1000}
                step={1}
                ariaLabel="Maximum trades per day"
                className="w-[150px]"
                onChange={(v) => setDraft((d) => ({ ...d, daily_max_trades: v }))}
              />
            }
          />
          <div className="flex items-center justify-between gap-2 py-2">
            <span className="text-hx-10 text-hx-text-dim">
              {limits.updated_at
                ? `Updated ${fmtTime(parseUtc(limits.updated_at), { mode: 'rel' })}`
                : 'Never updated'}
            </span>
            <span className="flex items-center gap-1.5">
              <Button
                size="xs"
                variant="subtle"
                disabled={!dirty || busy === 'save'}
                onClick={() => setDraft({
                  per_trade_max_inr: limits.per_trade_max_inr,
                  daily_max_loss_inr: limits.daily_max_loss_inr,
                  daily_max_trades: limits.daily_max_trades,
                })}
              >
                Discard
              </Button>
              <Button
                size="xs"
                variant="primary"
                icon="check"
                disabled={!dirty}
                loading={busy === 'save'}
                onClick={save}
              >
                Save limits
              </Button>
            </span>
          </div>
        </div>
      </div>

      {saveError && (
        <div role="alert" className="flex items-start gap-2 p-2 rounded-md border border-hx-neg-500/30 bg-hx-neg-500/[0.12]">
          <Icon name="alert" size={13} className="text-hx-neg-400 shrink-0 mt-px" />
          <span className="text-hx-11 text-hx-neg-300">{saveError}</span>
        </div>
      )}
    </Section>
  );
}

/* ---- shortcuts + about --------------------------------------------------- */

function ShortcutsSection() {
  return (
    <Section
      title="Keyboard shortcuts"
      icon="columns"
      description="Reference for the workspace bindings. Grid and tab navigation is built into the components and always available; the rest is dispatched by the workspace shell."
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
        {SHORTCUTS.map((g) => (
          <div key={g.group} className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised p-3">
            <div className="text-hx-10 uppercase tracking-wider text-hx-text-lo mb-2">{g.group}</div>
            <div className="flex flex-col gap-1.5">
              {g.items.map((s) => (
                <div key={s.label} className="flex items-center justify-between gap-3 min-w-0">
                  <span className="text-hx-11 text-hx-text-mid truncate">{s.label}</span>
                  <span className="flex items-center gap-1 shrink-0">
                    {s.keys.map((k, i) => (
                      <React.Fragment key={k}>
                        {i > 0 && <span className="text-hx-10 text-hx-text-dim">then</span>}
                        <KeyCap>{k}</KeyCap>
                      </React.Fragment>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function AboutSection() {
  const url = `${apiBase()}/api/v1/slowpath/dashboard`;
  const { data, error, loading, refresh } = useLivePoll(jget(url), OPS_CADENCE.about, [url]);

  const rows = [
    ['API endpoint', apiBase(), true],
    ['Slow-path provider', data?.provider ?? (loading ? '…' : '--'), true],
    ['Model', data?.model || (data ? 'not configured' : '--'), true],
    ['Orchestrator', data ? (data.initialized ? 'Initialised' : 'Not initialised') : '--', false],
    ['Registered agents', data ? String(data.total_agents ?? 0) : '--', true],
  ];

  return (
    <Section
      title="About"
      icon="info"
      description="Helios Capital workspace — an institutional front end over the Helios trading platform."
      actions={
        <Button size="xs" variant="subtle" icon="refresh" iconOnly aria-label="Recheck backend" onClick={refresh} />
      }
    >
      <div className="rounded-lg border border-hx-border-subtle bg-hx-bg-raised px-3 divide-y divide-hx-border-subtle">
        {rows.map(([label, value, mono]) => (
          <Field
            key={label}
            label={label}
            control={
              <span className={cx('text-hx-11 text-hx-text-hi truncate max-w-[280px] inline-block', mono && 'hx-mono')}>
                {value}
              </span>
            }
          />
        ))}
        <Field
          label="Backend"
          control={
            error
              ? <StatusChip status="offline" label="Unreachable" showIcon />
              : loading && !data
                ? <StatusChip status="stale" label="Checking" showIcon />
                : <StatusChip status="connected" label="Reachable" showIcon />
          }
        />
      </div>
      {error && (
        <p className="text-hx-10 text-hx-text-dim">{error.message}</p>
      )}
    </Section>
  );
}

/* ---- module -------------------------------------------------------------- */

const TABS = [
  { id: 'appearance', label: 'Appearance', icon: 'settings' },
  { id: 'data', label: 'Data sources', icon: 'markets' },
  { id: 'brokers', label: 'Brokers', icon: 'portfolio' },
  { id: 'risk', label: 'Risk', icon: 'risk' },
  { id: 'keys', label: 'Shortcuts', icon: 'columns' },
  { id: 'about', label: 'About', icon: 'info' },
];

export function SettingsModule({
  section,              // store: active settings section
  onSectionChange,      // store: publish section change
  appearance,           // store: { fontScale, density } — controlled when provided
  onAppearanceChange,   // store: publish appearance changes
  className = '',
}) {
  const [tab, setTab] = useControllable(section, onSectionChange, 'appearance');
  const [localAppearance, setLocalAppearance] = useState(readStored);

  const current = appearance || localAppearance;

  const applyAppearance = useCallback((next) => {
    setLocalAppearance(next);
    if (onAppearanceChange) onAppearanceChange(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* private mode / quota — the setting still applies for this session */
    }
  }, [onAppearanceChange]);

  /**
   * Scale is applied with `zoom` on the document root rather than a transform:
   * a transform on any ancestor would create a containing block and break every
   * position:fixed overlay in the workspace (Drawer, notifications). `zoom` does
   * not, so overlays keep resolving against the viewport.
   */
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    root.style.zoom = current.fontScale === 100 ? '' : `${current.fontScale}%`;
    root.style.setProperty('--hx-row-h', `${(DENSITIES[current.density] || DENSITIES.default).rowH}px`);
  }, [current.fontScale, current.density]);

  return (
    <Panel className={cx('h-full', className)}>
      <PanelHeader title="Settings" icon="settings" />

      <Tabs tabs={TABS} value={tab} onChange={setTab} idPrefix="hx-settings" className="px-2 shrink-0" />

      <PanelBody className="max-w-[1100px]">
        <TabPanel id="appearance" value={tab} idPrefix="hx-settings">
          <AppearanceSection appearance={current} onChange={applyAppearance} />
        </TabPanel>

        <TabPanel id="data" value={tab} idPrefix="hx-settings">
          <DataSourcesSection />
        </TabPanel>

        <TabPanel id="brokers" value={tab} idPrefix="hx-settings">
          <Section
            title="Broker connections"
            icon="portfolio"
            description="Credentials are encrypted server-side and never returned to the browser — only a masked key is shown. Indian broker sessions expire daily at 06:00 IST under SEBI rules."
          >
            <BrokersPanel embedded />
          </Section>
        </TabPanel>

        <TabPanel id="risk" value={tab} idPrefix="hx-settings">
          <RiskSection />
        </TabPanel>

        <TabPanel id="keys" value={tab} idPrefix="hx-settings">
          <ShortcutsSection />
        </TabPanel>

        <TabPanel id="about" value={tab} idPrefix="hx-settings">
          <AboutSection />
        </TabPanel>
      </PanelBody>
    </Panel>
  );
}

export default SettingsModule;
