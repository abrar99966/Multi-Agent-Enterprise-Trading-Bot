/**
 * ShortcutHelp — the '?' overlay.
 *
 * Reads HOTKEYS from lib/ws/useHotkeys so the cheatsheet and the handler can
 * never disagree: adding a binding in one place publishes it in both.
 */
import React, { useEffect, useRef } from 'react';
import { HOTKEYS } from '../../../lib/ws/useHotkeys';
import { Icon } from '../ui';

function KeyCap({ children }) {
  return (
    <kbd className="inline-flex min-w-[20px] items-center justify-center rounded border border-hx-border-strong bg-hx-bg-overlay px-1.5 py-0.5 font-hx-mono text-hx-10 text-hx-text-mid">
      {children}
    </kbd>
  );
}

export function ShortcutHelp({ open, onClose }) {
  const panelRef = useRef(null);
  const restoreTo = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    restoreTo.current = document.activeElement;
    panelRef.current?.focus();

    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
        return;
      }
      // Minimal focus trap — the dialog holds one focusable node, so Tab simply
      // cycles back to it rather than escaping to the page behind the backdrop.
      if (e.key === 'Tab') {
        e.preventDefault();
        panelRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      // Return focus where the user left it, or the overlay strands the keyboard.
      const el = restoreTo.current;
      if (el && typeof el.focus === 'function') el.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 animate-hx-fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        tabIndex={-1}
        className="hx-focus w-full max-w-2xl overflow-hidden rounded-lg border border-hx-border-strong bg-hx-bg-overlay shadow-hx-pop outline-none"
      >
        <header className="flex items-center justify-between border-b border-hx-border-subtle px-4 py-2.5">
          <h2 className="text-hx-12 font-semibold text-hx-text-hi">Keyboard shortcuts</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close shortcuts"
            className="hx-focus rounded p-1 text-hx-text-dim hover:text-hx-text-hi"
          >
            <Icon name="close" size={14} />
          </button>
        </header>

        <div className="grid gap-x-8 gap-y-5 p-4 sm:grid-cols-2">
          {HOTKEYS.map((group) => (
            <section key={group.group}>
              <h3 className="mb-2 text-hx-10 uppercase tracking-wider text-hx-text-dim">
                {group.group}
              </h3>
              <ul className="space-y-1.5">
                {group.items.map((item) => (
                  <li key={item.label} className="flex items-center justify-between gap-4">
                    <span className="text-hx-12 text-hx-text-mid">{item.label}</span>
                    <span className="flex shrink-0 items-center gap-1">
                      {item.keys.map((k, i) => (
                        <React.Fragment key={k}>
                          {i > 0 && <span className="text-hx-10 text-hx-text-dim">+</span>}
                          <KeyCap>{k}</KeyCap>
                        </React.Fragment>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <footer className="border-t border-hx-border-subtle px-4 py-2 text-hx-10 text-hx-text-dim">
          Shortcuts are suppressed while typing in a field, except{' '}
          <KeyCap>Ctrl</KeyCap> <KeyCap>K</KeyCap> and <KeyCap>Esc</KeyCap>.
        </footer>
      </div>
    </div>
  );
}

export default ShortcutHelp;
