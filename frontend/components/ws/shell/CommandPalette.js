/**
 * CommandPalette — Ctrl+K. Jump to a module, scope the workspace to a symbol,
 * or fire a global action.
 *
 * Symbol entries come from the live watchlist rather than a hardcoded list, so
 * the palette can always reach whatever the desk is actually watching. If that
 * request fails the palette still works for modules and actions — a broken feed
 * must not take navigation down with it.
 *
 * Positioned `fixed` with no transformed ancestor (see WorkspaceLayout).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon, cx } from '../ui';
import { MODULES } from '../../../lib/ws/store';

/** Subsequence match — "prtf" finds "Portfolio". Returns null when it doesn't hit. */
function fuzzyScore(needle, haystack) {
  if (!needle) return 0;
  const n = needle.toLowerCase();
  const h = haystack.toLowerCase();
  const direct = h.indexOf(n);
  if (direct === 0) return 1000;          // prefix beats everything
  if (direct > 0) return 700 - direct;    // then substring, earlier is better
  let hi = 0;
  let score = 0;
  let streak = 0;
  for (let ni = 0; ni < n.length; ni += 1) {
    const found = h.indexOf(n[ni], hi);
    if (found === -1) return null;
    streak = found === hi ? streak + 1 : 0;
    score += 10 + streak * 4;
    hi = found + 1;
  }
  return score;
}

export function CommandPalette({ open, onClose, ws, symbols = [] }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const restoreRef = useRef(null);

  /* Build the flat command set. Symbols are entries too — "open RELIANCE"
     is the single most common thing anyone types into a trading palette. */
  const commands = useMemo(() => {
    const out = MODULES.map((m) => ({
      id: `mod:${m.id}`,
      kind: 'Module',
      label: m.label,
      hint: m.hint,
      icon: m.icon,
      keys: m.key ? ['Alt', m.key] : null,
      run: (w) => w.setModule(m.id),
    }));

    for (const s of symbols) {
      out.push({
        id: `sym:${s}`,
        kind: 'Symbol',
        label: s,
        hint: 'Scope workspace to this symbol',
        icon: 'markets',
        mono: true,
        run: (w) => w.openSymbol(s, 'markets'),
      });
    }

    out.push(
      {
        id: 'act:clear-symbol',
        kind: 'Action',
        label: 'Clear symbol selection',
        hint: 'Unscope every panel',
        icon: 'close',
        run: (w) => w.selectSymbol(null),
      },
      {
        id: 'act:console',
        kind: 'Action',
        label: 'Toggle console dock',
        hint: 'Workspace activity log',
        icon: 'logs',
        keys: ['Ctrl', 'J'],
        run: (w) => w.toggleConsole(),
      },
      {
        id: 'act:context',
        kind: 'Action',
        label: 'Toggle context panel',
        icon: 'columns',
        keys: [']'],
        run: (w) => w.toggleContext(),
      },
      {
        id: 'act:sidebar',
        kind: 'Action',
        label: 'Toggle sidebar',
        icon: 'chevrons-left',
        keys: ['['],
        run: (w) => w.toggleSidebar(),
      },
      {
        id: 'act:help',
        kind: 'Action',
        label: 'Keyboard shortcuts',
        icon: 'info',
        keys: ['?'],
        run: (w) => w.setHelpOpen(true),
      },
    );
    return out;
  }, [symbols]);

  const results = useMemo(() => {
    if (!query.trim()) {
      // Empty query: modules first — the palette's primary job is navigation.
      return commands.filter((c) => c.kind !== 'Symbol').slice(0, 24);
    }
    const scored = [];
    for (const c of commands) {
      const s = Math.max(
        fuzzyScore(query, c.label) ?? -1,
        (fuzzyScore(query, c.kind) ?? -1) - 400, // kind matches rank below label matches
      );
      if (s >= 0) scored.push({ c, s });
    }
    scored.sort((a, b) => b.s - a.s);
    return scored.slice(0, 24).map((x) => x.c);
  }, [commands, query]);

  /* Reset per open. Clamp `active` whenever results shrink, or Enter can fire
     a command that scrolled out from under the cursor. */
  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      restoreRef.current = typeof document !== 'undefined' ? document.activeElement : null;
      const id = requestAnimationFrame(() => inputRef.current?.focus());
      return () => cancelAnimationFrame(id);
    }
    // Return focus where it came from — losing focus to <body> strands keyboard users.
    const el = restoreRef.current;
    if (el && typeof el.focus === 'function') el.focus();
    return undefined;
  }, [open]);

  useEffect(() => {
    setActive((a) => (a >= results.length ? Math.max(0, results.length - 1) : a));
  }, [results.length]);

  useEffect(() => {
    const el = listRef.current?.querySelector('[data-active="true"]');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [active, results]);

  /* ESC + Tab bound to the document in capture phase, like Drawer and
     ShortcutHelp. Escape must not live on the input's onKeyDown: one Tab moves
     focus off the input and the palette becomes undismissable. The input is the
     only focusable node here (results are role=option divs), so Tab simply
     cycles back to it rather than escaping to the shell behind the backdrop. */
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose]);

  const runAt = useCallback(
    (i) => {
      const cmd = results[i];
      if (!cmd) return;
      cmd.run(ws);
      ws.log('info', `Command: ${cmd.label}`);
      onClose();
    },
    [results, ws, onClose],
  );

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => (a + 1) % Math.max(1, results.length));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => (a - 1 + Math.max(1, results.length)) % Math.max(1, results.length));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      runAt(active);
    }
    // Escape is handled on the document — see the focus-trap effect above.
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center pt-[12vh]" role="presentation">
      <div
        className="absolute inset-0 bg-black/60 animate-hx-fade-in"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="relative w-[560px] max-w-[92vw] overflow-hidden rounded-lg border border-hx-border-strong bg-hx-bg-overlay shadow-hx-pop animate-hx-slide-up"
      >
        <div className="flex items-center gap-2 border-b border-hx-border-subtle px-3">
          <Icon name="search" size={15} className="shrink-0 text-hx-text-dim" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="Search symbols, modules, actions"
            aria-label="Search commands"
            aria-controls="hx-palette-list"
            aria-activedescendant={results[active] ? `hx-cmd-${results[active].id}` : undefined}
            className="h-10 flex-1 bg-transparent text-hx-13 text-hx-text-hi placeholder:text-hx-text-dim focus:outline-none"
          />
          <kbd className="rounded border border-hx-border-subtle px-1 hx-mono text-hx-10 text-hx-text-dim">esc</kbd>
        </div>

        <div
          id="hx-palette-list"
          ref={listRef}
          role="listbox"
          aria-label="Commands"
          className="max-h-[46vh] overflow-y-auto hx-scroll p-1"
        >
          {results.length === 0 && (
            <div className="px-3 py-6 text-center text-hx-12 text-hx-text-dim">
              No matches for “{query}”
            </div>
          )}
          {results.map((c, i) => (
            <div
              key={c.id}
              id={`hx-cmd-${c.id}`}
              role="option"
              aria-selected={i === active}
              data-active={i === active}
              onMouseEnter={() => setActive(i)}
              onClick={() => runAt(i)}
              className={cx(
                'flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-1.5',
                i === active ? 'bg-hx-accent-500/12' : 'hover:bg-white/[0.03]',
              )}
            >
              <Icon
                name={c.icon}
                size={14}
                className={cx('shrink-0', i === active ? 'text-hx-accent-400' : 'text-hx-text-dim')}
              />
              <span
                className={cx(
                  'truncate text-hx-12',
                  c.mono && 'hx-mono font-semibold',
                  i === active ? 'text-hx-text-hi' : 'text-hx-text-mid',
                )}
              >
                {c.label}
              </span>
              {c.hint && <span className="truncate text-hx-11 text-hx-text-dim">{c.hint}</span>}
              <span className="ml-auto flex shrink-0 items-center gap-1">
                {c.keys?.map((k) => (
                  <kbd
                    key={k}
                    className="rounded border border-hx-border-subtle bg-hx-bg-raised px-1 hx-mono text-hx-10 text-hx-text-dim"
                  >
                    {k}
                  </kbd>
                ))}
                <span className="w-[52px] text-right text-hx-10 uppercase tracking-wider text-hx-text-dim">
                  {c.kind}
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
