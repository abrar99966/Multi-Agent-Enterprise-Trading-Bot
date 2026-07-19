/**
 * Tooltip — hover + focus, zero dependencies, no portal.
 *
 * WHY no portal: the workspace has no transformed ancestors on panel content,
 * so an absolutely-positioned bubble inside a `relative` wrapper positions
 * correctly and costs nothing at mount. Panels that clip must pass side="right".
 *
 * Accessibility: the trigger gets aria-describedby, the bubble role="tooltip".
 * Opens on focus-visible as well as hover, and ESC dismisses — both required by
 * WCAG 1.4.13 (content on hover or focus).
 */
import React, { useId, useRef, useState, useCallback, useEffect } from 'react';
import { cx } from './tokens';

const SIDES = {
  top: 'bottom-full left-1/2 -translate-x-1/2 mb-1.5',
  bottom: 'top-full left-1/2 -translate-x-1/2 mt-1.5',
  left: 'right-full top-1/2 -translate-y-1/2 mr-1.5',
  right: 'left-full top-1/2 -translate-y-1/2 ml-1.5',
};

export function Tooltip({
  content,
  children,
  side = 'top',
  delay = 250,
  className = '',
  wrapperClassName = '',
  disabled = false,
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timer = useRef(null);

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const show = useCallback(() => {
    if (disabled || !content) return;
    clear();
    timer.current = setTimeout(() => setOpen(true), delay);
  }, [clear, delay, disabled, content]);

  const hide = useCallback(() => {
    clear();
    setOpen(false);
  }, [clear]);

  // Timer must not fire after unmount (React 18 strict mode double-invokes).
  useEffect(() => clear, [clear]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') hide(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, hide]);

  if (!content) return children;

  return (
    <span
      className={cx('relative inline-flex', wrapperClassName)}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocusCapture={() => !disabled && setOpen(true)}
      onBlurCapture={hide}
    >
      {React.isValidElement(children)
        ? React.cloneElement(children, { 'aria-describedby': open ? id : undefined })
        : children}

      {open && (
        <span
          role="tooltip"
          id={id}
          className={cx(
            'absolute z-50 pointer-events-none animate-hx-fade-in',
            'max-w-[280px] w-max px-2 py-1 rounded-md',
            'bg-hx-bg-overlay border border-hx-border-strong shadow-hx-pop',
            'text-hx-11 leading-snug text-hx-text-hi text-left font-normal normal-case',
            SIDES[side] || SIDES.top,
            className,
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}

/** InfoTip — the common "ⓘ next to a label" case, pre-wired. */
export function InfoTip({ content, side = 'top', className = '' }) {
  return (
    <Tooltip content={content} side={side}>
      <button
        type="button"
        // Focusable so keyboard users can reach the explanation at all.
        aria-label="More information"
        className={cx(
          'hx-focus inline-flex items-center justify-center h-3.5 w-3.5 rounded-full',
          'border border-hx-border-strong text-hx-text-dim text-[9px] leading-none font-bold',
          'hover:text-hx-text-mid hover:border-hx-text-dim transition-colors',
          className,
        )}
      >
        i
      </button>
    </Tooltip>
  );
}

export default Tooltip;
