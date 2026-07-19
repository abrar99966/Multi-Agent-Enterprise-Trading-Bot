/**
 * Notification — toast with tone, title, body, dismiss and auto-timeout.
 *
 * Notification       — a single toast (controlled)
 * NotificationStack  — fixed bottom-right container
 * useNotifications   — tiny local store: push(), dismiss(), list
 *
 * WHY a hook instead of context: most views need one or two toasts and owning
 * the array locally avoids a provider wrapping the whole workspace. A view that
 * needs app-wide toasts can lift `useNotifications` into its own context.
 *
 * Critical toasts never auto-dismiss — a rejected order or a tripped kill-switch
 * must be acknowledged, not missed.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { TONE_TEXT, TONE_BORDER, TONE_SOLID, cx } from './tokens';

const TONE_ICON = {
  neutral: 'info',
  accent: 'spark',
  pos: 'check',
  neg: 'alert',
  warn: 'alert',
  info: 'info',
};

export function Notification({
  id,
  tone = 'neutral',
  title,
  body,
  icon,
  action,               // { label, onClick }
  timeout = 6000,       // ms; 0 or null disables auto-dismiss
  onDismiss,
  className = '',
}) {
  const timer = useRef(null);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (!timeout || paused) return undefined;
    timer.current = setTimeout(() => onDismiss && onDismiss(id), timeout);
    return () => clearTimeout(timer.current);
    // Restarting on `paused` gives the user the full window back after hovering.
  }, [timeout, paused, id, onDismiss]);

  return (
    <div
      // Errors interrupt; everything else waits for a pause in AT output.
      role={tone === 'neg' ? 'alert' : 'status'}
      aria-live={tone === 'neg' ? 'assertive' : 'polite'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
      className={cx(
        'pointer-events-auto relative flex gap-2.5 w-[320px] p-2.5 rounded-lg',
        'bg-hx-bg-overlay border shadow-hx-pop animate-hx-slide-up',
        TONE_BORDER[tone] || TONE_BORDER.neutral,
        className,
      )}
    >
      {/* tone rail — a second, non-textual channel for severity */}
      <span
        aria-hidden="true"
        className={cx('absolute left-0 top-2 bottom-2 w-[2px] rounded-full', TONE_SOLID[tone])}
      />

      <Icon name={icon || TONE_ICON[tone] || 'info'} size={16} className={cx('mt-px shrink-0', TONE_TEXT[tone])} />

      <div className="flex-1 min-w-0">
        <div className="text-hx-12 font-semibold text-hx-text-hi leading-snug">{title}</div>
        {body && <div className="text-hx-11 text-hx-text-lo leading-relaxed mt-0.5 break-words">{body}</div>}
        {action && (
          <button
            type="button"
            onClick={() => { action.onClick && action.onClick(); onDismiss && onDismiss(id); }}
            className={cx('hx-focus mt-1.5 text-hx-11 font-semibold rounded hover:underline', TONE_TEXT[tone])}
          >
            {action.label}
          </button>
        )}
      </div>

      <button
        type="button"
        onClick={() => onDismiss && onDismiss(id)}
        aria-label="Dismiss notification"
        className="hx-focus shrink-0 h-5 w-5 -mt-0.5 -mr-0.5 inline-flex items-center justify-center rounded text-hx-text-dim hover:text-hx-text-hi hover:bg-white/[0.06] transition-colors"
      >
        <Icon name="close" size={12} />
      </button>
    </div>
  );
}

/** Fixed container. Newest toast renders at the bottom, nearest the cursor. */
export function NotificationStack({ items = [], onDismiss, position = 'bottom-right', className = '' }) {
  const pos = {
    'bottom-right': 'bottom-4 right-4 items-end',
    'bottom-left': 'bottom-4 left-4 items-start',
    'top-right': 'top-4 right-4 items-end',
  }[position] || 'bottom-4 right-4 items-end';

  return (
    <div
      className={cx('fixed z-[60] flex flex-col gap-2 pointer-events-none', pos, className)}
      // The region is a landmark so users can jump to it; individual toasts
      // carry their own live semantics.
      role="region"
      aria-label="Notifications"
    >
      {items.map((n) => (
        <Notification key={n.id} {...n} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

/** Local toast store. `push` returns the id so callers can dismiss early. */
export function useNotifications({ max = 4 } = {}) {
  const [items, setItems] = useState([]);
  const seq = useRef(0);

  const dismiss = useCallback((id) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = useCallback(
    (n) => {
      const id = `n${++seq.current}`;
      const item = {
        id,
        tone: 'neutral',
        // Critical alerts stay until acknowledged.
        timeout: n.tone === 'neg' ? 0 : 6000,
        ...n,
      };
      // Cap the stack so a burst of poll errors can't cover the viewport.
      setItems((xs) => [...xs, item].slice(-max));
      return id;
    },
    [max],
  );

  const clear = useCallback(() => setItems([]), []);

  return { items, push, dismiss, clear };
}

export default Notification;
