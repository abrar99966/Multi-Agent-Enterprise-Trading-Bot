/**
 * Panel — the universal container. Every workspace surface is a Panel.
 *
 * Composition: <Panel><PanelHeader/><PanelToolbar/><PanelBody/></Panel>
 * Chrome is deliberately minimal: one hairline border, one header rule, no
 * gradient, no drop shadow beyond a 1px seat. Density comes from the header
 * being 30px tall, not from shrinking the content.
 *
 * `collapsible` keeps the header visible and hides only the body, so a user can
 * park a panel without losing its title row in a dashboard grid.
 */
import React, { createContext, useContext, useId, useState } from 'react';
import { Icon } from './Icon';
import { cx } from './tokens';

const PanelCtx = createContext(null);

export function Panel({
  children,
  className = '',
  as: Tag = 'section',
  flush = false,        // remove the outer border (for nested/embedded panels)
  collapsible = false,
  defaultCollapsed = false,
  loading = false,
  ...rest
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const bodyId = useId();

  return (
    <PanelCtx.Provider value={{ collapsible, collapsed, setCollapsed, bodyId }}>
      <Tag
        aria-busy={loading || undefined}
        className={cx(
          'flex flex-col min-h-0 min-w-0 rounded-lg bg-hx-panel',
          !flush && 'border border-hx-border-subtle shadow-hx-panel',
          className,
        )}
        {...rest}
      >
        {children}
      </Tag>
    </PanelCtx.Provider>
  );
}

/**
 * PanelHeader — title row. `actions` sits right-aligned; when the panel is
 * collapsible the whole title becomes a disclosure button.
 */
export function PanelHeader({ title, subtitle, icon, actions, className = '', children }) {
  const ctx = useContext(PanelCtx);
  const collapsible = ctx?.collapsible;

  const titleBlock = (
    <span className="flex items-center gap-2 min-w-0">
      {collapsible && (
        <Icon
          name={ctx.collapsed ? 'chevron-right' : 'chevron-down'}
          size={14}
          className="text-hx-text-dim shrink-0"
        />
      )}
      {icon && <Icon name={icon} size={14} className="text-hx-text-lo shrink-0" />}
      <span className="text-hx-12 font-semibold text-hx-text-hi uppercase tracking-wide truncate">
        {title}
      </span>
      {subtitle && (
        <span className="text-hx-11 text-hx-text-dim truncate font-normal normal-case">{subtitle}</span>
      )}
    </span>
  );

  return (
    <header
      className={cx(
        'flex items-center justify-between gap-3 h-[30px] px-3 shrink-0',
        'border-b border-hx-border-subtle',
        className,
      )}
    >
      {collapsible ? (
        <button
          type="button"
          onClick={() => ctx.setCollapsed((v) => !v)}
          aria-expanded={!ctx.collapsed}
          aria-controls={ctx.bodyId}
          className="hx-focus flex items-center gap-2 min-w-0 text-left rounded"
        >
          {titleBlock}
        </button>
      ) : (
        titleBlock
      )}
      {(actions || children) && (
        <div className="flex items-center gap-1.5 shrink-0">{actions || children}</div>
      )}
    </header>
  );
}

/**
 * PanelToolbar — secondary control strip below the header (filters, ranges,
 * search). Separate from PanelHeader so a panel can have controls without
 * crowding its title row.
 */
export function PanelToolbar({ children, className = '', justify = 'between' }) {
  const ctx = useContext(PanelCtx);
  if (ctx?.collapsed) return null;
  return (
    <div
      className={cx(
        'flex items-center gap-2 h-[32px] px-3 shrink-0',
        'border-b border-hx-border-subtle bg-white/[0.015]',
        justify === 'between' ? 'justify-between' : justify === 'end' ? 'justify-end' : 'justify-start',
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * PanelBody — scroll container. `pad` off for grids/tables that manage their
 * own edge-to-edge padding.
 */
export function PanelBody({ children, className = '', pad = true, scroll = true }) {
  const ctx = useContext(PanelCtx);
  if (ctx?.collapsed) return null;
  return (
    <div
      id={ctx?.bodyId}
      className={cx(
        'flex-1 min-h-0 min-w-0',
        // A non-scrolling body still has to CLIP. Without this, a child sized in
        // fixed pixels (a chart asked for 232px inside a 150px row) paints
        // straight through the panel and over whatever sits below it. Panels
        // must never bleed onto their siblings.
        scroll ? 'overflow-auto hx-scroll' : 'overflow-hidden',
        pad && 'p-3',
        className,
      )}
    >
      {children}
    </div>
  );
}

/** PanelFooter — totals row / pagination. Same rhythm as the header. */
export function PanelFooter({ children, className = '' }) {
  const ctx = useContext(PanelCtx);
  if (ctx?.collapsed) return null;
  return (
    <footer
      className={cx(
        'flex items-center justify-between gap-3 h-[28px] px-3 shrink-0',
        'border-t border-hx-border-subtle text-hx-11 text-hx-text-lo',
        className,
      )}
    >
      {children}
    </footer>
  );
}

export default Panel;
