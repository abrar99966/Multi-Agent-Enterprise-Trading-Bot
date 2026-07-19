/**
 * Sidebar — module rail, grouped, collapsible to icons.
 *
 * Module switching is state only: these are <button>s, not links. A router
 * navigation would remount every module and throw away the poll caches, which
 * is the whole reason the workspace is one page.
 */
import React from 'react';
import { Icon, cx } from '../ui';
import { MODULES, MODULE_GROUPS } from '../../../lib/ws/store';

function NavItem({ mod, active, collapsed, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(mod.id)}
      aria-current={active ? 'page' : undefined}
      title={collapsed ? `${mod.label} — ${mod.hint}` : undefined}
      className={cx(
        'group relative flex w-full items-center gap-2.5 rounded-md text-hx-12 font-medium transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-hx-accent-400/70',
        collapsed ? 'justify-center px-0 py-2' : 'px-2.5 py-1.5',
        active
          ? 'bg-hx-accent-500/12 text-hx-text-hi'
          : 'text-hx-text-lo hover:bg-white/[0.04] hover:text-hx-text-mid',
      )}
    >
      {/* Active marker is a bar, not just colour — survives greyscale and
          colour-blind rendering where cyan-on-navy alone would not. */}
      <span
        aria-hidden="true"
        className={cx(
          'absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full transition-opacity',
          active ? 'bg-hx-accent-400 opacity-100' : 'opacity-0',
        )}
      />
      <Icon
        name={mod.icon}
        size={16}
        className={cx('shrink-0', active ? 'text-hx-accent-400' : 'text-hx-text-dim group-hover:text-hx-text-lo')}
      />
      {!collapsed && <span className="truncate">{mod.label}</span>}
      {!collapsed && mod.key && (
        <span className="ml-auto font-hx-mono text-hx-10 text-hx-text-dim tabular-nums">{mod.key}</span>
      )}
    </button>
  );
}

export function Sidebar({ moduleId, onSelect, collapsed, onToggle }) {
  return (
    <nav
      aria-label="Workspace modules"
      className={cx(
        'flex h-full flex-col border-r border-hx-border-subtle bg-hx-bg-sunken transition-[width] duration-150',
        collapsed ? 'w-[52px]' : 'w-[188px]',
      )}
    >
      <div className="flex-1 overflow-y-auto hx-scroll py-2">
        {MODULE_GROUPS.map((group) => {
          const items = MODULES.filter((m) => m.group === group);
          if (!items.length) return null;
          return (
            <div key={group} className="mb-1 px-2">
              {!collapsed && (
                <div className="px-1.5 pb-1 pt-2 text-hx-10 font-semibold uppercase tracking-wider text-hx-text-dim">
                  {group}
                </div>
              )}
              {collapsed && <div aria-hidden="true" className="mx-2 my-2 border-t border-hx-border-subtle" />}
              <div className="flex flex-col gap-0.5">
                {items.map((m) => (
                  <NavItem
                    key={m.id}
                    mod={m}
                    active={m.id === moduleId}
                    collapsed={collapsed}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>

      <div className="border-t border-hx-border-subtle p-2">
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cx(
            'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-hx-11 text-hx-text-dim',
            'hover:bg-white/[0.04] hover:text-hx-text-mid',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-hx-accent-400/70',
            collapsed && 'justify-center',
          )}
        >
          <Icon name={collapsed ? 'chevrons-right' : 'chevrons-left'} size={14} />
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </nav>
  );
}

export default Sidebar;
