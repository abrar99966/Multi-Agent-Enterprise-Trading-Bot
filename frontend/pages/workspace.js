/**
 * /workspace — the Helios desk.
 *
 * A single-page desktop application: modules are swapped by state, never by
 * navigation, so the chart, Copilot and console keep their subscriptions and
 * scroll positions while the operator moves between views.
 *
 * Wiring contract: this file is the ONLY place that reads the workspace store
 * and hands slices down. Every module below is controlled — it renders what it
 * is given and publishes changes back through callbacks — which is what makes
 * "click a symbol anywhere, everything re-scopes" hold without modules
 * importing each other.
 *
 * The legacy multi-page UI (pages/index.js and friends) is untouched and still
 * served at its own routes.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import Head from 'next/head';
import ErrorBoundary from '../components/ErrorBoundary';
import { WorkspaceProvider, useWorkspace } from '../lib/ws/store';
import { useHotkeys } from '../lib/ws/useHotkeys';
import { CommandPalette, ShortcutHelp, Sidebar, TopBar, WorkspaceLayout } from '../components/ws/shell';
import { EmptyState } from '../components/ws/ui';

import DashboardModule from '../components/ws/modules/dashboard';
import { ChartWorkspace } from '../components/ws/modules/chart';
import { MarketsModule } from '../components/ws/modules/markets';
import { PortfolioModule } from '../components/ws/modules/portfolio';
import { OrdersModule } from '../components/ws/modules/orders';
import { RiskModule } from '../components/ws/modules/risk';
import { StrategiesModule, LearningModule } from '../components/ws/modules/strategies';
import { AnalyticsModule, LogsModule, ReplayModule, SettingsModule } from '../components/ws/modules/ops';
import { CopilotPanel, ContextPanels } from '../components/ws/modules/copilot';
import ConsoleDock from '../components/ws/modules/console';

/** Opening instrument. The index is the cheapest symbol to resolve upstream and
 *  is what a desk looks at first. */
const DEFAULT_SYMBOL = 'NIFTY';

function Workspace() {
  const ws = useWorkspace();
  useHotkeys(ws);

  // Strategy selection and the Copilot seed are transient view state — they are
  // deliberately not persisted, because restoring them a day later would scope
  // the desk to context the operator has forgotten choosing.
  const [strategyId, setStrategyId] = useState(null);
  const [seedPrompt, setSeedPrompt] = useState(null);

  /* Seed a symbol on first paint. Selection is intentionally not persisted, so
     without this the chart, order book and context tabs all open empty — the
     centre of the desk would be a void until the operator clicks something.
     Done in an effect (never during render) to keep the server and client HTML
     identical. */
  useEffect(() => {
    if (!ws.symbol) ws.selectSymbol(DEFAULT_SYMBOL);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectSymbol = useCallback(
    (sym) => {
      // Deselection (null) is a real, supported transition — modules toggle the
      // active row off and drop selection when a symbol leaves the universe.
      // Only the log line is skipped for it, never the state write.
      ws.selectSymbol(sym);
      if (sym) ws.log('info', `Selected ${sym}`);
    },
    [ws],
  );

  const selectStrategy = useCallback(
    (id) => {
      setStrategyId(id);
      ws.log('info', `Selected strategy ${id}`);
    },
    [ws],
  );

  const askCopilot = useCallback(
    (prompt) => {
      // Make sure the dock is visible before the question lands in it.
      if (!ws.contextOpen) ws.toggleContext();
      setSeedPrompt(prompt);
    },
    [ws],
  );

  const chartSlot = useMemo(
    () => <ChartWorkspace symbol={ws.symbol} height={280} />,
    [ws.symbol],
  );

  const center = useMemo(() => {
    switch (ws.moduleId) {
      case 'dashboard':
        return (
          <DashboardModule
            symbol={ws.symbol}
            onSelectSymbol={selectSymbol}
            strategyId={strategyId}
            onSelectStrategy={selectStrategy}
          />
        );
      case 'markets':
        return (
          <MarketsModule
            symbol={ws.symbol}
            onSymbolChange={selectSymbol}
            symbols={ws.symbols}
            onSymbolsChange={ws.setSymbols}
            chartSlot={chartSlot}
            className="h-full"
          />
        );
      case 'portfolio':
        return <PortfolioModule selectedSymbol={ws.symbol} onSelectSymbol={selectSymbol} className="h-full" />;
      case 'orders':
        return <OrdersModule symbol={ws.symbol} onSelectSymbol={selectSymbol} log={ws.log} />;
      case 'strategies':
        return (
          <StrategiesModule
            strategyId={strategyId}
            onSelectStrategy={selectStrategy}
            onAskCopilot={askCopilot}
          />
        );
      case 'learning':
        return <LearningModule log={ws.log} />;
      case 'analytics':
        return <AnalyticsModule db={ws.db} onDbChange={ws.setDb} onSelectSymbol={selectSymbol} className="h-full" />;
      case 'copilot':
        // Full-width Copilot for long-form work; the dock keeps its own instance
        // so a conversation started here is not lost when the module changes.
        return (
          <div className="h-full min-h-0 p-2">
            <div className="h-full min-h-0 overflow-hidden rounded-lg border border-hx-border-subtle">
              <CopilotPanel symbol={ws.symbol} strategyId={strategyId} />
            </div>
          </div>
        );
      case 'risk':
        return <RiskModule log={ws.log} />;
      case 'replay':
        return <ReplayModule journal={ws.journal} onJournalChange={ws.setJournal} className="h-full" />;
      case 'logs':
        return <LogsModule journal={ws.journal} onJournalChange={ws.setJournal} className="h-full" />;
      case 'settings':
        return (
          <SettingsModule
            section={ws.settingsSection}
            onSectionChange={ws.setSettingsSection}
            appearance={ws.appearance}
            onAppearanceChange={ws.setAppearance}
            className="h-full"
          />
        );
      default:
        return <EmptyState icon="dashboard" title="Unknown module" hint="Pick a module from the sidebar." />;
    }
  }, [ws, strategyId, selectSymbol, selectStrategy, askCopilot, chartSlot]);

  return (
    <div
      className="hx-root flex h-screen w-screen flex-col overflow-hidden bg-hx-bg-base text-hx-text-hi antialiased"
      style={{ fontSize: `${(ws.appearance?.fontScale || 100) / 100 * 13}px` }}
    >
      <TopBar
        module={ws.module}
        symbol={ws.symbol}
        onOpenPalette={() => ws.setPaletteOpen(true)}
        onOpenHelp={() => ws.setHelpOpen(true)}
        onToggleContext={ws.toggleContext}
        onToggleConsole={ws.toggleConsole}
        contextOpen={ws.contextOpen}
        consoleOpen={ws.consoleOpen}
      />

      <WorkspaceLayout
        sidebar={
          <Sidebar
            moduleId={ws.moduleId}
            onSelect={ws.setModule}
            collapsed={ws.sidebarCollapsed}
            onToggle={ws.toggleSidebar}
          />
        }
        center={center}
        rightOpen={ws.contextOpen}
        onExpandRight={ws.toggleContext}
        bottomOpen={ws.consoleOpen}
        onExpandBottom={ws.toggleConsole}
        right={
          <div className="flex h-full min-h-0 flex-col">
            <div className="min-h-0 flex-1">
              <CopilotPanel
                symbol={ws.symbol}
                strategyId={strategyId}
                seedPrompt={seedPrompt}
                onSeedConsumed={() => setSeedPrompt(null)}
              />
            </div>
            <div className="h-[46%] min-h-0">
              <ContextPanels symbol={ws.symbol} strategyId={strategyId} />
            </div>
          </div>
        }
        bottom={
          <ConsoleDock
            onSelectSymbol={selectSymbol}
            consoleLines={ws.consoleLines}
            onClearConsole={ws.clearConsole}
          />
        }
      />

      <CommandPalette
        open={ws.paletteOpen}
        onClose={() => ws.setPaletteOpen(false)}
        ws={ws}
        symbols={ws.symbols}
      />
      <ShortcutHelp open={ws.helpOpen} onClose={() => ws.setHelpOpen(false)} />
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <>
      <Head>
        <title>Helios Capital — Desk</title>
        <meta name="description" content="Institutional AI trading workspace" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>
      <ErrorBoundary>
        <WorkspaceProvider>
          <Workspace />
        </WorkspaceProvider>
      </ErrorBoundary>
    </>
  );
}
