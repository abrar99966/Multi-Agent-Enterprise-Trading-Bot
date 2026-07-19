/**
 * Workspace shell — the application frame.
 *
 *   import { TopBar, Sidebar, WorkspaceLayout, CommandPalette, ShortcutHelp }
 *     from '../components/ws/shell';
 *
 * The shell owns no data. It reads the workspace store, routes the active
 * module, and hands every module its slice as controlled props — so modules
 * stay presentational and the store stays the single source of truth.
 */
export { TopBar, default as TopBarDefault } from './TopBar';
export { Sidebar, default as SidebarDefault } from './Sidebar';
export { WorkspaceLayout, default as WorkspaceLayoutDefault } from './WorkspaceLayout';
export { CommandPalette, default as CommandPaletteDefault } from './CommandPalette';
export { ShortcutHelp, default as ShortcutHelpDefault } from './ShortcutHelp';
