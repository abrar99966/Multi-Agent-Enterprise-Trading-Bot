/**
 * Global workspace hotkeys.
 *
 * WHY one listener instead of per-component handlers: shortcuts have to work no
 * matter what is focused, and a dozen competing window listeners is how you get
 * a shortcut that fires twice or stops working once a drawer opens.
 *
 * Typing is sacred — every binding is suppressed while focus sits in a text
 * field, except the ones that are explicitly escape hatches (Escape, Ctrl+K).
 */
import { useEffect } from 'react';
import { MODULES } from './store';

/** True when the event target is a text-entry surface. */
function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return el.isContentEditable === true;
}

export function useHotkeys(ws) {
  const {
    setModule, setPaletteOpen, setHelpOpen, toggleSidebar, toggleContext, toggleConsole,
  } = ws;

  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      const typing = isTyping(e.target);

      // Command palette — deliberately works while typing, it's the escape hatch.
      if (mod && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        return;
      }

      // Console dock. Ctrl+` matches the editor convention people already have.
      if (mod && (e.key === '`' || e.key === 'j' || e.key === 'J')) {
        e.preventDefault();
        toggleConsole();
        return;
      }

      if (typing) return;

      // Alt+digit jumps modules. Alt (not bare digit) so a digit typed into a
      // grid's quick-filter can never teleport the user out of their view.
      if (e.altKey && /^[1-9]$/.test(e.key)) {
        const target = MODULES.find((m) => m.key === e.key);
        if (target) {
          e.preventDefault();
          setModule(target.id);
        }
        return;
      }

      if (e.key === '?') {
        e.preventDefault();
        setHelpOpen((v) => !v);
        return;
      }
      if (e.key === '[') {
        e.preventDefault();
        toggleSidebar();
        return;
      }
      if (e.key === ']') {
        e.preventDefault();
        toggleContext();
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setModule, setPaletteOpen, setHelpOpen, toggleSidebar, toggleContext, toggleConsole]);
}

/** Bindings rendered by ShortcutHelp — kept beside the handler so they can't drift. */
export const HOTKEYS = [
  { group: 'Navigation', items: [
    { keys: ['Alt', '1-9'], label: 'Jump to module' },
    { keys: ['Ctrl', 'K'], label: 'Command palette' },
    { keys: ['['], label: 'Toggle sidebar' },
    { keys: [']'], label: 'Toggle context panel' },
    { keys: ['Ctrl', 'J'], label: 'Toggle console dock' },
  ] },
  { group: 'General', items: [
    { keys: ['?'], label: 'Keyboard shortcuts' },
    { keys: ['Esc'], label: 'Close overlay' },
    { keys: ['↑', '↓'], label: 'Move between grid rows' },
    { keys: ['Enter'], label: 'Open focused row' },
  ] },
];
