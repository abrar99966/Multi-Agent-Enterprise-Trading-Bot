/**
 * Drawer — right or bottom slide-over with backdrop, ESC-to-close, focus trap.
 *
 * Used for order tickets, position detail, agent reasoning, log inspection.
 *
 * WHY no portal: react-dom is intentionally not imported here (the primitive
 * layer depends on React only). The drawer is `position: fixed` at z-50, which
 * escapes normal stacking as long as no ancestor creates a containing block via
 * transform/filter/perspective — the workspace shell guarantees that.
 *
 * Focus management follows the WAI-ARIA dialog pattern: focus moves in on open,
 * Tab cycles within, and focus returns to the invoking element on close.
 */
import React, { useCallback, useEffect, useId, useRef } from 'react';
import { Icon } from './Icon';
import { cx } from './tokens';

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

const SIDES = {
  right: {
    box: 'top-0 right-0 h-full border-l',
    size: (s) => ({ width: s }),
    anim: 'animate-hx-fade-in',
  },
  bottom: {
    box: 'bottom-0 left-0 w-full border-t',
    size: (s) => ({ height: s }),
    anim: 'animate-hx-slide-up',
  },
  left: {
    box: 'top-0 left-0 h-full border-r',
    size: (s) => ({ width: s }),
    anim: 'animate-hx-fade-in',
  },
};

export function Drawer({
  open,
  onClose,
  side = 'right',
  size = 480,             // px width (right/left) or height (bottom)
  title,
  subtitle,
  icon,
  actions,                // header-right slot
  footer,
  children,
  closeOnBackdrop = true,
  className = '',
  labelledBy,
}) {
  const panelRef = useRef(null);
  const restoreRef = useRef(null);
  const autoId = useId();
  const titleId = labelledBy || `${autoId}-title`;
  const cfg = SIDES[side] || SIDES.right;

  const close = useCallback(() => { onClose && onClose(); }, [onClose]);

  // Remember the trigger before the drawer steals focus, restore on unmount.
  useEffect(() => {
    if (!open) return undefined;
    restoreRef.current = typeof document !== 'undefined' ? document.activeElement : null;

    const node = panelRef.current;
    if (node) {
      const first = node.querySelector(FOCUSABLE);
      (first || node).focus({ preventScroll: true });
    }

    // Background must not scroll behind a modal surface.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.body.style.overflow = prevOverflow;
      const el = restoreRef.current;
      if (el && typeof el.focus === 'function') el.focus({ preventScroll: true });
    };
  }, [open]);

  // ESC + Tab trapping. Bound to the document so it works no matter where the
  // user's focus drifted (e.g. into an iframe-adjacent element).
  useEffect(() => {
    if (!open) return undefined;

    const onKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        close();
        return;
      }
      if (e.key !== 'Tab') return;
      const node = panelRef.current;
      if (!node) return;
      const items = Array.from(node.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );
      if (items.length === 0) {
        e.preventDefault();
        node.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      // Wrap at both ends so focus can never escape to the page behind.
      if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [open, close]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <div
        className="absolute inset-0 bg-black/60 animate-hx-fade-in"
        onClick={closeOnBackdrop ? close : undefined}
        aria-hidden="true"
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        style={cfg.size(size)}
        className={cx(
          'absolute flex flex-col max-w-full max-h-full outline-none',
          'bg-hx-bg-raised border-hx-border-strong shadow-hx-pop',
          cfg.box,
          cfg.anim,
          side === 'bottom' && 'rounded-t-lg',
          className,
        )}
      >
        {(title || actions) && (
          <header className="flex items-center justify-between gap-3 h-10 px-3 shrink-0 border-b border-hx-border-subtle">
            <div className="flex items-center gap-2 min-w-0">
              {icon && <Icon name={icon} size={16} className="text-hx-text-lo shrink-0" />}
              <div className="min-w-0">
                <div id={titleId} className="text-hx-13 font-semibold text-hx-text-hi truncate">
                  {title}
                </div>
                {subtitle && <div className="text-hx-10 text-hx-text-dim truncate">{subtitle}</div>}
              </div>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {actions}
              <button
                type="button"
                onClick={close}
                aria-label="Close panel"
                className="hx-focus h-6 w-6 inline-flex items-center justify-center rounded text-hx-text-lo hover:text-hx-text-hi hover:bg-white/[0.06] transition-colors"
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          </header>
        )}

        <div className="flex-1 min-h-0 overflow-auto hx-scroll">{children}</div>

        {footer && (
          <footer className="flex items-center justify-end gap-2 h-12 px-3 shrink-0 border-t border-hx-border-subtle bg-white/[0.015]">
            {footer}
          </footer>
        )}
      </div>
    </div>
  );
}

export default Drawer;
