/**
 * Workspace store — one provider holding every piece of state that crosses a
 * module boundary, plus the module registry the shell routes from.
 *
 * WHY a provider and not per-module state: selection has to survive module
 * switches (click a symbol in Markets, switch to Portfolio, it follows) and the
 * command palette has to drive every module without importing any of them.
 *
 * SSR CONTRACT: initial state is a pure constant, identical on server and
 * client. Persisted values are adopted in an effect after mount, so the first
 * client render matches the server HTML exactly and React never has to discard
 * the tree over a hydration mismatch.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { DEFAULT_SYMBOLS } from '../../components/ws/modules/markets/marketsApi';

/* ---- module registry -----------------------------------------------------
   The single source of truth for what exists. Sidebar, command palette,
   shortcut help and the router in pages/workspace.js all read from this, so a
   new module is added in exactly one place. `key` is the digit that jumps to
   it under the Alt modifier — only the first nine get one, by design. */
export const MODULES = [
  { id: 'dashboard',  label: 'Dashboard',  icon: 'dashboard',  group: 'Trading',      key: '1', hint: 'Desk overview' },
  { id: 'markets',    label: 'Markets',    icon: 'markets',    group: 'Trading',      key: '2', hint: 'Watchlist, depth, movers' },
  { id: 'portfolio',  label: 'Portfolio',  icon: 'portfolio',  group: 'Trading',      key: '3', hint: 'Positions, exposure, P&L' },
  { id: 'orders',     label: 'Orders',     icon: 'orders',     group: 'Trading',      key: '4', hint: 'Recommendations & fills' },
  { id: 'strategies', label: 'Strategies', icon: 'strategies', group: 'Intelligence', key: '5', hint: 'Bandit allocator & arms' },
  { id: 'learning',   label: 'Learning',   icon: 'learning',   group: 'Intelligence', key: '6', hint: 'Training & calibration' },
  { id: 'analytics',  label: 'Analytics',  icon: 'analytics',  group: 'Intelligence', key: '7', hint: 'Transaction cost analysis' },
  { id: 'copilot',    label: 'Copilot',    icon: 'copilot',    group: 'Intelligence', key: '8', hint: 'Conversational desk agent' },
  { id: 'risk',       label: 'Risk',       icon: 'risk',       group: 'Control',      key: '9', hint: 'Limits, kill switch, surveillance' },
  { id: 'replay',     label: 'Replay',     icon: 'replay',     group: 'Control',                hint: 'Journal replay & determinism' },
  { id: 'logs',       label: 'Logs',       icon: 'logs',       group: 'Control',                hint: 'Journal event console' },
  { id: 'settings',   label: 'Settings',   icon: 'settings',   group: 'Control',                hint: 'Appearance, sources, brokers' },
];

export const MODULE_GROUPS = ['Trading', 'Intelligence', 'Control'];

export const MODULE_IDS = MODULES.map((m) => m.id);

export function moduleById(id) {
  return MODULES.find((m) => m.id === id) || MODULES[0];
}

/* ---- persistence ---------------------------------------------------------
   One key holds the whole slice so a partial write can't leave the workspace in
   a half-restored state. Everything here is UI preference, never data. */
const STORE_KEY = 'hx.ws.state';

const INITIAL = {
  moduleId: 'dashboard',
  symbol: null,
  // Seeded, not undefined: the universe has to exist before Markets is ever
  // opened, because the command palette's symbol search reads it from here. A
  // static constant keeps server and first client render identical (SSR
  // contract above); the persisted list is adopted in the effect below.
  symbols: DEFAULT_SYMBOLS,
  journal: '',
  db: '',
  settingsSection: 'appearance',
  appearance: { fontScale: 100, density: 'compact' },
  sidebarCollapsed: false,
  contextOpen: true,
  consoleOpen: false,
};

// Only these survive a reload. Selection is deliberately excluded: restoring a
// symbol the user picked days ago silently scopes every panel to stale context.
const PERSISTED = ['moduleId', 'symbols', 'journal', 'db', 'settingsSection', 'appearance', 'sidebarCollapsed', 'contextOpen'];

function readStored() {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    // Drop a module id from an older build — routing to a dead id blanks the app.
    if (parsed.moduleId && !MODULE_IDS.includes(parsed.moduleId)) delete parsed.moduleId;
    return parsed;
  } catch {
    return null;
  }
}

const WorkspaceCtx = createContext(null);

export function useWorkspace() {
  const ctx = useContext(WorkspaceCtx);
  if (!ctx) throw new Error('useWorkspace must be used inside <WorkspaceProvider>');
  return ctx;
}

let _seq = 0;

export function WorkspaceProvider({ children }) {
  const [state, setState] = useState(INITIAL);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  /* Client-side activity log. This is the workspace's own record of what the
     user did — it is NOT the backend journal (that's the Logs module). Capped,
     because an unbounded array behind a 20s poll is a slow memory leak. */
  const [consoleLines, setConsoleLines] = useState([]);
  const log = useCallback((level, message, meta) => {
    setConsoleLines((prev) => {
      const next = prev.concat({ id: ++_seq, at: Date.now(), level, message, meta });
      return next.length > 300 ? next.slice(next.length - 300) : next;
    });
  }, []);
  const clearConsole = useCallback(() => setConsoleLines([]), []);

  /* Adopt persisted prefs after mount — see SSR contract above. */
  const hydrated = useRef(false);
  useEffect(() => {
    const stored = readStored();
    if (stored) {
      setState((s) => {
        const next = { ...s };
        for (const k of PERSISTED) if (stored[k] !== undefined) next[k] = stored[k];
        return next;
      });
    }
    hydrated.current = true;
  }, []);

  /* Write back. Skipped until hydration so the initial constant can't overwrite
     a real stored value in the moment between mount and adoption. */
  useEffect(() => {
    if (!hydrated.current) return;
    try {
      const slice = {};
      for (const k of PERSISTED) slice[k] = state[k];
      window.localStorage.setItem(STORE_KEY, JSON.stringify(slice));
    } catch {
      /* private mode / quota — prefs just won't survive the session */
    }
  }, [state]);

  const patch = useCallback((p) => setState((s) => ({ ...s, ...p })), []);

  const setModule = useCallback(
    (id) => {
      if (!MODULE_IDS.includes(id)) return;
      setState((s) => (s.moduleId === id ? s : { ...s, moduleId: id }));
    },
    [],
  );

  const selectSymbol = useCallback((sym) => setState((s) => ({ ...s, symbol: sym || null })), []);
  const setSymbols = useCallback((next) => setState((s) => ({ ...s, symbols: next })), []);
  const setJournal = useCallback((j) => setState((s) => ({ ...s, journal: j })), []);
  const setDb = useCallback((d) => setState((s) => ({ ...s, db: d })), []);
  const setSettingsSection = useCallback((sec) => setState((s) => ({ ...s, settingsSection: sec })), []);
  const setAppearance = useCallback((a) => setState((s) => ({ ...s, appearance: a })), []);
  const toggleSidebar = useCallback(() => setState((s) => ({ ...s, sidebarCollapsed: !s.sidebarCollapsed })), []);
  const toggleContext = useCallback(() => setState((s) => ({ ...s, contextOpen: !s.contextOpen })), []);
  const toggleConsole = useCallback(() => setState((s) => ({ ...s, consoleOpen: !s.consoleOpen })), []);

  /* Open a symbol in a module in one gesture — the palette and every
     cross-module drill-down go through this so the two writes can't desync. */
  const openSymbol = useCallback((sym, moduleId) => {
    setState((s) => ({ ...s, symbol: sym || null, moduleId: moduleId && MODULE_IDS.includes(moduleId) ? moduleId : s.moduleId }));
  }, []);

  const value = useMemo(
    () => ({
      ...state,
      module: moduleById(state.moduleId),
      patch,
      setModule,
      selectSymbol,
      setSymbols,
      setJournal,
      setDb,
      setSettingsSection,
      setAppearance,
      toggleSidebar,
      toggleContext,
      toggleConsole,
      openSymbol,
      paletteOpen,
      setPaletteOpen,
      helpOpen,
      setHelpOpen,
      consoleLines,
      log,
      clearConsole,
    }),
    [
      state, patch, setModule, selectSymbol, setSymbols, setJournal, setDb,
      setSettingsSection, setAppearance, toggleSidebar, toggleContext,
      toggleConsole, openSymbol, paletteOpen, helpOpen, consoleLines, log, clearConsole,
    ],
  );

  return <WorkspaceCtx.Provider value={value}>{children}</WorkspaceCtx.Provider>;
}
