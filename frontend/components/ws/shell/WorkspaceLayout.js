/**
 * WorkspaceLayout — the docking frame.
 *
 *   sidebar | [ center            ] | right dock
 *           | [ bottom dock       ] |
 *
 * WHY CSS variables instead of React state for the pane sizes: a resize drag
 * fires on every pointer move. Routing that through setState would re-render the
 * entire module tree (charts, grids, canvases) 60 times a second. The splitter
 * writes straight to a CSS custom property on the grid element, so a drag costs
 * one style recalc and nothing above it re-renders. The final size is committed
 * to localStorage on pointer-up only.
 */
import React, { useCallback, useEffect, useRef } from 'react';
import { Icon } from '../ui';

const SIZES_KEY = 'hx.ws.panes';

const RIGHT = { min: 280, max: 640, def: 372, var: '--hx-right' };
const BOTTOM = { min: 120, max: 560, def: 232, var: '--hx-bottom' };

function readSizes() {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(SIZES_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const clamp = (v, { min, max }) => Math.min(max, Math.max(min, v));

/**
 * One draggable divider. `axis` picks which pointer delta counts and which
 * cursor to show; `invert` is for edges that grow as the pointer moves toward
 * the origin (the right dock widens as the pointer moves left).
 */
function Splitter({ axis, onDelta, onCommit, label }) {
  const dragging = useRef(false);
  const last = useRef(0);

  const onPointerDown = useCallback(
    (e) => {
      dragging.current = true;
      last.current = axis === 'x' ? e.clientX : e.clientY;
      e.currentTarget.setPointerCapture(e.pointerId);
      // Kill text selection + iframe pointer stealing for the duration.
      document.body.style.userSelect = 'none';
    },
    [axis],
  );

  const onPointerMove = useCallback(
    (e) => {
      if (!dragging.current) return;
      const now = axis === 'x' ? e.clientX : e.clientY;
      const delta = now - last.current;
      last.current = now;
      onDelta(delta);
    },
    [axis, onDelta],
  );

  const end = useCallback(
    (e) => {
      if (!dragging.current) return;
      dragging.current = false;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* capture may already be gone if the pointer left the window */
      }
      document.body.style.userSelect = '';
      onCommit();
    },
    [onCommit],
  );

  // Keyboard resize: the divider is focusable so a pane can be sized without a
  // pointer. 16px per press, matching the grid rhythm.
  const onKeyDown = useCallback(
    (e) => {
      const step = e.shiftKey ? 48 : 16;
      const back = axis === 'x' ? 'ArrowLeft' : 'ArrowUp';
      const fwd = axis === 'x' ? 'ArrowRight' : 'ArrowDown';
      if (e.key === back) {
        e.preventDefault();
        onDelta(-step);
        onCommit();
      } else if (e.key === fwd) {
        e.preventDefault();
        onDelta(step);
        onCommit();
      }
    },
    [axis, onDelta, onCommit],
  );

  return (
    <div
      role="separator"
      aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={end}
      onPointerCancel={end}
      onKeyDown={onKeyDown}
      className={
        axis === 'x'
          ? 'group relative z-10 w-px shrink-0 cursor-col-resize bg-hx-border-subtle outline-none transition-colors hover:bg-hx-accent-500/60 focus-visible:bg-hx-accent-400'
          : 'group relative z-10 h-px shrink-0 cursor-row-resize bg-hx-border-subtle outline-none transition-colors hover:bg-hx-accent-500/60 focus-visible:bg-hx-accent-400'
      }
    >
      {/* Invisible fat hit area — a 1px target is unusable, but a visible 8px
          divider wastes screen. */}
      <span
        aria-hidden="true"
        className={
          axis === 'x'
            ? 'absolute inset-y-0 -left-1 -right-1 block'
            : 'absolute inset-x-0 -top-1 -bottom-1 block'
        }
      />
    </div>
  );
}

/** Collapsed dock rail — keeps the affordance visible when a pane is hidden. */
function DockRail({ side, label, onExpand }) {
  const vertical = side === 'right';
  return (
    <button
      type="button"
      onClick={onExpand}
      title={`Show ${label}`}
      aria-label={`Show ${label}`}
      className={
        vertical
          ? 'hx-focus flex w-7 shrink-0 flex-col items-center gap-2 border-l border-hx-border-subtle bg-hx-bg-sunken py-2 text-hx-text-dim hover:text-hx-text-mid'
          : 'hx-focus flex h-6 shrink-0 items-center gap-2 border-t border-hx-border-subtle bg-hx-bg-sunken px-3 text-hx-text-dim hover:text-hx-text-mid'
      }
    >
      <Icon name={vertical ? 'chevron-left' : 'chevron-up'} size={12} />
      <span
        className="text-hx-10 uppercase tracking-wider"
        style={vertical ? { writingMode: 'vertical-rl' } : undefined}
      >
        {label}
      </span>
    </button>
  );
}

export function WorkspaceLayout({
  sidebar,
  center,
  right,
  bottom,
  rightOpen = true,
  bottomOpen = false,
  onExpandRight,
  onExpandBottom,
  rightLabel = 'Copilot',
  bottomLabel = 'Console',
}) {
  const gridRef = useRef(null);
  const sizes = useRef({ right: RIGHT.def, bottom: BOTTOM.def });

  // Adopt persisted sizes after mount — never during render, so server and
  // client first paint agree (Next.js pages router hydration).
  useEffect(() => {
    const stored = readSizes();
    if (stored) {
      sizes.current = {
        right: clamp(Number(stored.right) || RIGHT.def, RIGHT),
        bottom: clamp(Number(stored.bottom) || BOTTOM.def, BOTTOM),
      };
    }
    const el = gridRef.current;
    if (el) {
      el.style.setProperty(RIGHT.var, `${sizes.current.right}px`);
      el.style.setProperty(BOTTOM.var, `${sizes.current.bottom}px`);
    }
  }, []);

  const commit = useCallback(() => {
    try {
      window.localStorage.setItem(SIZES_KEY, JSON.stringify(sizes.current));
    } catch {
      /* private mode — sizes just won't persist */
    }
  }, []);

  // The right dock grows as the pointer moves left, hence the negated delta.
  const dragRight = useCallback((delta) => {
    const next = clamp(sizes.current.right - delta, RIGHT);
    sizes.current.right = next;
    gridRef.current?.style.setProperty(RIGHT.var, `${next}px`);
  }, []);

  const dragBottom = useCallback((delta) => {
    const next = clamp(sizes.current.bottom - delta, BOTTOM);
    sizes.current.bottom = next;
    gridRef.current?.style.setProperty(BOTTOM.var, `${next}px`);
  }, []);

  return (
    <div ref={gridRef} className="flex min-h-0 flex-1 overflow-hidden">
      {sidebar}

      {/* centre column: workspace above, console dock below */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <main className="min-h-0 min-w-0 flex-1 overflow-hidden bg-hx-bg-base">{center}</main>

        {bottomOpen ? (
          <>
            <Splitter axis="y" onDelta={dragBottom} onCommit={commit} label="Resize console dock" />
            <section
              aria-label={bottomLabel}
              className="min-h-0 shrink-0 overflow-hidden bg-hx-bg-sunken"
              style={{ height: `var(${BOTTOM.var}, ${BOTTOM.def}px)` }}
            >
              {bottom}
            </section>
          </>
        ) : (
          <DockRail side="bottom" label={bottomLabel} onExpand={onExpandBottom} />
        )}
      </div>

      {rightOpen ? (
        <>
          <Splitter axis="x" onDelta={dragRight} onCommit={commit} label="Resize context dock" />
          <aside
            aria-label={rightLabel}
            className="flex min-h-0 shrink-0 flex-col overflow-hidden bg-hx-bg-sunken"
            style={{ width: `var(${RIGHT.var}, ${RIGHT.def}px)` }}
          >
            {right}
          </aside>
        </>
      ) : (
        <DockRail side="right" label={rightLabel} onExpand={onExpandRight} />
      )}
    </div>
  );
}

export default WorkspaceLayout;
