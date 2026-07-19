/**
 * Ops modules — Analytics, Logs, Replay, Settings.
 *
 *   import { AnalyticsModule, LogsModule } from '../components/ws/modules/ops';
 *
 * Every module is a self-contained Panel that fills its container (h-full) and
 * owns its own polling. Shared selection is passed in as controlled props and
 * echoed back through the matching onChange, so the shell's store is the single
 * source of truth without any module reaching into a global.
 */

export { AnalyticsModule, default as Analytics } from './AnalyticsModule';
export { LogsModule, default as Logs } from './LogsModule';
export { ReplayModule, default as Replay } from './ReplayModule';
export { SettingsModule, SHORTCUTS, default as Settings } from './SettingsModule';
export { BrokersPanel } from './BrokersPanel';

// Shared controls — exported so sibling ops surfaces can match the chrome.
export { Select, SearchInput, Toggle, NumberInput, Section, Field, KeyCap } from './OpsField';

// Data helpers. Re-exported for the shell: `useControllable` is the store bridge
// and the time helpers encode the two timestamp conventions this backend ships.
export {
  apiBase,
  jget,
  jpost,
  jdel,
  nsToMs,
  parseUtc,
  fmtCountdown,
  fmtBytes,
  useControllable,
  eventSeverity,
  eventSummary,
  prettyJson,
  STREAM_LABELS,
  OPS_CADENCE,
} from './opsApi';
