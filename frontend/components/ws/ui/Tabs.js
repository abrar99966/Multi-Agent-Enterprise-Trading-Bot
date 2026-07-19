/**
 * Tabs — underline tab strip with full ARIA tablist semantics.
 *
 * Keyboard: ←/→ move between tabs, Home/End jump to first/last. Follows the
 * "automatic activation" pattern (arrow selects immediately) because every
 * workspace tab swaps an already-loaded panel — no expensive activation.
 *
 * Roving tabindex: only the selected tab is tabbable, so Tab moves past the
 * whole strip into the panel rather than through every tab.
 */
import React, { useId, useRef } from 'react';
import { Icon } from './Icon';
import { CountBadge } from './Badge';
import { cx } from './tokens';

export function Tabs({
  tabs = [],        // [{ id, label, icon?, count?, disabled? }]
  value,
  onChange,
  size = 'sm',      // sm (30px) | md (36px)
  className = '',
  right,            // right-aligned slot inside the strip (actions/filters)
  idPrefix,
}) {
  const auto = useId();
  const prefix = idPrefix || auto;
  const refs = useRef({});

  const enabled = tabs.filter((t) => !t.disabled);

  const onKeyDown = (e) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const i = enabled.findIndex((t) => t.id === value);
    let next;
    if (e.key === 'Home') next = enabled[0];
    else if (e.key === 'End') next = enabled[enabled.length - 1];
    else {
      const step = e.key === 'ArrowRight' ? 1 : -1;
      // Wrap around — expected for a tablist per WAI-ARIA APG.
      next = enabled[(i + step + enabled.length) % enabled.length];
    }
    if (next) {
      onChange && onChange(next.id);
      const el = refs.current[next.id];
      if (el) el.focus();
    }
  };

  const h = size === 'md' ? 'h-9' : 'h-[30px]';
  const text = size === 'md' ? 'text-hx-13' : 'text-hx-12';

  return (
    <div className={cx('flex items-center justify-between gap-3 border-b border-hx-border-subtle', className)}>
      <div role="tablist" aria-orientation="horizontal" onKeyDown={onKeyDown} className="flex items-stretch gap-0.5 min-w-0 overflow-x-auto hx-scroll">
        {tabs.map((t) => {
          const active = t.id === value;
          return (
            <button
              key={t.id}
              ref={(el) => { refs.current[t.id] = el; }}
              role="tab"
              id={`${prefix}-tab-${t.id}`}
              aria-controls={`${prefix}-panel-${t.id}`}
              aria-selected={active}
              // Roving tabindex: -1 on inactive tabs keeps the strip a single stop.
              tabIndex={active ? 0 : -1}
              disabled={t.disabled}
              onClick={() => onChange && onChange(t.id)}
              className={cx(
                'hx-focus-inset relative inline-flex items-center gap-1.5 px-3 whitespace-nowrap',
                'font-medium transition-colors duration-100 disabled:opacity-40 disabled:pointer-events-none',
                h,
                text,
                active ? 'text-hx-text-hi' : 'text-hx-text-lo hover:text-hx-text-mid',
              )}
            >
              {t.icon && <Icon name={t.icon} size={14} />}
              {t.label}
              {t.count ? <CountBadge count={t.count} tone={active ? 'accent' : 'neutral'} /> : null}
              {/* Underline sits on the container's border line, not below it. */}
              <span
                aria-hidden="true"
                className={cx(
                  'absolute left-0 right-0 -bottom-px h-[2px] rounded-full transition-opacity duration-100',
                  active ? 'bg-hx-accent-400 opacity-100' : 'opacity-0',
                )}
              />
            </button>
          );
        })}
      </div>
      {right && <div className="flex items-center gap-1.5 shrink-0 pr-2">{right}</div>}
    </div>
  );
}

/** TabPanel — pairs with Tabs; renders nothing unless its tab is selected. */
export function TabPanel({ id, value, idPrefix, children, className = '', keepMounted = false }) {
  const active = id === value;
  if (!active && !keepMounted) return null;
  return (
    <div
      role="tabpanel"
      id={`${idPrefix}-panel-${id}`}
      aria-labelledby={`${idPrefix}-tab-${id}`}
      // tabIndex=0 so keyboard users can scroll the panel after leaving the strip.
      tabIndex={0}
      hidden={!active}
      className={cx('hx-focus-inset min-h-0 min-w-0 flex-1', active && 'animate-hx-fade-in', className)}
    >
      {children}
    </div>
  );
}

export default Tabs;
