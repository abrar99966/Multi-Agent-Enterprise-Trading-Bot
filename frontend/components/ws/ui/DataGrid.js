/**
 * DataGrid — the dense table every workspace view renders its rows into.
 *
 * Column def:
 *   { key, header, width, align, sortable, numeric, mono, hidden,
 *     accessor(row) -> sortable/exportable primitive,
 *     render(row, i) -> ReactNode,
 *     headerTitle, className }
 *
 * Behaviour: sticky header, sortable headers (click or Enter/Space), subtle row
 * hover (no zebra — stripes fight with the live-flash tint), right-aligned
 * numerics, keyboard row navigation (↑/↓/Enter), empty + loading states, and
 * optional column chooser + CSV export.
 *
 * WHY a real <table>: screen readers announce row/column position for free, and
 * `table-layout: fixed` with a <colgroup> keeps 5,000 rows from re-measuring on
 * every poll. Sorting is uncontrolled by default but can be lifted via
 * `sort`/`onSortChange`.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { Icon } from './Icon';
import { Button } from './Button';
import { EmptyState } from './EmptyState';
import { SkeletonRows } from './Skeleton';
import { cx } from './tokens';

/* ---- helpers (exported: views build export buttons from these) ----------- */

/** Read a column's sortable/exportable primitive from a row. */
export function cellValue(col, row) {
  if (typeof col.accessor === 'function') return col.accessor(row);
  return row ? row[col.key] : undefined;
}

/** Rows + visible columns → RFC-4180 CSV. Quotes only where needed. */
export function toCSV(columns, rows) {
  const cols = columns.filter((c) => !c.hidden && c.exportable !== false);
  const esc = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = cols.map((c) => esc(c.header ?? c.key)).join(',');
  const body = rows.map((r) => cols.map((c) => esc(cellValue(c, r))).join(',')).join('\r\n');
  return `${head}\r\n${body}`;
}

/** Trigger a client-side CSV download. No-op during SSR. */
export function downloadCSV(filename, columns, rows) {
  if (typeof document === 'undefined') return;
  // BOM so Excel opens UTF-8 symbols (₹, €) correctly.
  const blob = new Blob(['﻿', toCSV(columns, rows)], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoke on the next tick — immediate revoke cancels the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Comparator that keeps nulls last in both directions. */
function compare(a, b, dir) {
  const nilA = a === null || a === undefined || a === '';
  const nilB = b === null || b === undefined || b === '';
  if (nilA && nilB) return 0;
  if (nilA) return 1;
  if (nilB) return -1;
  const na = Number(a);
  const nb = Number(b);
  const numeric = Number.isFinite(na) && Number.isFinite(nb);
  const r = numeric ? na - nb : String(a).localeCompare(String(b), undefined, { numeric: true });
  return dir === 'desc' ? -r : r;
}

/* ---- column chooser ------------------------------------------------------ */

function ColumnChooser({ columns, hiddenKeys, onToggle }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close when focus leaves the whole popover (covers click-away and Tab-away).
  const onBlur = (e) => {
    if (ref.current && !ref.current.contains(e.relatedTarget)) setOpen(false);
  };

  return (
    <div className="relative" ref={ref} onBlur={onBlur}>
      <Button
        size="xs"
        variant="subtle"
        icon="columns"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        Columns
      </Button>
      {open && (
        <div
          role="menu"
          onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
          className="absolute right-0 top-full mt-1 z-30 min-w-[180px] max-h-[280px] overflow-auto hx-scroll p-1 rounded-md bg-hx-bg-overlay border border-hx-border-strong shadow-hx-pop animate-hx-fade-in"
        >
          {columns.map((c) => {
            const visible = !hiddenKeys.includes(c.key);
            return (
              <label
                key={c.key}
                className="hx-focus flex items-center gap-2 px-2 py-1 rounded text-hx-11 text-hx-text-mid hover:bg-white/[0.06] cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={visible}
                  onChange={() => onToggle(c.key)}
                  className="hx-focus h-3 w-3 accent-hx-accent-500"
                />
                <span className="truncate">{c.header ?? c.key}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---- grid ---------------------------------------------------------------- */

export function DataGrid({
  columns = [],
  rows = [],
  rowKey = (r, i) => r?.id ?? i,
  loading = false,
  onRowClick,
  selectedKey,
  sort: sortProp,              // { key, dir } — controlled sorting
  onSortChange,
  defaultSort,                 // { key, dir } — uncontrolled initial sorting
  emptyTitle = 'No rows',
  emptyHint,
  emptyAction,
  columnChooser = false,
  exportName,                  // truthy → renders a CSV export button
  toolbar,                     // extra nodes rendered left of the built-ins
  dense = true,
  stickyHeader = true,
  maxHeight,
  className = '',
  ariaLabel = 'Data grid',
}) {
  const [sortState, setSortState] = useState(defaultSort || null);
  const [hiddenKeys, setHiddenKeys] = useState(() => columns.filter((c) => c.hidden).map((c) => c.key));
  const bodyRef = useRef(null);

  const sort = sortProp !== undefined ? sortProp : sortState;
  const visible = useMemo(() => columns.filter((c) => !hiddenKeys.includes(c.key)), [columns, hiddenKeys]);

  const setSort = useCallback(
    (key) => {
      const col = columns.find((c) => c.key === key);
      if (!col || col.sortable === false) return;
      // Cycle asc → desc → unsorted, so a user can always get back to the
      // server's natural ordering (usually time or rank).
      let next;
      if (!sort || sort.key !== key) next = { key, dir: col.numeric ? 'desc' : 'asc' };
      else if (sort.dir === 'asc') next = { key, dir: 'desc' };
      else if (sort.dir === 'desc') next = null;
      else next = { key, dir: 'asc' };

      if (onSortChange) onSortChange(next);
      if (sortProp === undefined) setSortState(next);
    },
    [columns, sort, onSortChange, sortProp],
  );

  const sorted = useMemo(() => {
    if (!sort || !sort.key) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    // Copy — never sort the caller's array in place (useLivePoll reuses refs).
    return [...rows].sort((a, b) => compare(cellValue(col, a), cellValue(col, b), sort.dir));
  }, [rows, sort, columns]);

  /** ↑/↓ move between rows, Enter/Space activates. Keeps grids operable
      without a mouse, which is the point of a trading terminal. */
  const onRowKeyDown = (e, row, i) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick && onRowClick(row, i);
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const dir = e.key === 'ArrowDown' ? 1 : -1;
    const next = bodyRef.current?.querySelectorAll('tr[data-row]')[i + dir];
    if (next) next.focus();
  };

  const showToolbar = Boolean(toolbar || columnChooser || exportName);
  const rowH = dense ? 28 : 34;

  return (
    <div className={cx('flex flex-col min-h-0 min-w-0', className)}>
      {showToolbar && (
        <div className="flex items-center justify-between gap-2 px-2 py-1 shrink-0 border-b border-hx-border-subtle">
          {/* wrap: in a narrow column (e.g. the 380px watchlist) custom toolbar
              controls would otherwise collide with the built-in buttons */}
          <div className="flex flex-wrap items-center gap-1.5 min-w-0">{toolbar}</div>
          <div className="flex items-center gap-1 shrink-0">
            {columnChooser && (
              <ColumnChooser
                columns={columns}
                hiddenKeys={hiddenKeys}
                onToggle={(k) =>
                  setHiddenKeys((xs) => (xs.includes(k) ? xs.filter((x) => x !== k) : [...xs, k]))
                }
              />
            )}
            {exportName && (
              <Button
                size="xs"
                variant="subtle"
                icon="download"
                onClick={() => downloadCSV(exportName, visible, sorted)}
                disabled={!sorted.length}
              >
                CSV
              </Button>
            )}
          </div>
        </div>
      )}

      <div
        className="flex-1 min-h-0 overflow-auto hx-scroll"
        style={{ maxHeight }}
        aria-busy={loading || undefined}
      >
        <table
          className="w-full border-collapse"
          style={{ tableLayout: 'fixed' }}
          aria-label={ariaLabel}
          aria-rowcount={sorted.length}
        >
          <colgroup>
            {visible.map((c) => (
              <col key={c.key} style={{ width: c.width }} />
            ))}
          </colgroup>

          <thead className={stickyHeader ? 'hx-sticky-head' : undefined}>
            <tr>
              {visible.map((c) => {
                const sortable = c.sortable !== false;
                const active = sort && sort.key === c.key;
                const right = c.align === 'right' || (c.align === undefined && c.numeric);
                return (
                  <th
                    key={c.key}
                    scope="col"
                    title={c.headerTitle || undefined}
                    // aria-sort is what makes sort state audible, not just visual.
                    aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : sortable ? 'none' : undefined}
                    className={cx(
                      'h-[26px] px-2 font-medium text-hx-10 uppercase tracking-wider select-none',
                      'text-hx-text-lo whitespace-nowrap',
                      right ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                      c.headerClassName,
                    )}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => setSort(c.key)}
                        className={cx(
                          'hx-focus inline-flex items-center gap-1 max-w-full rounded transition-colors',
                          right && 'flex-row-reverse',
                          active ? 'text-hx-accent-300' : 'hover:text-hx-text-mid',
                        )}
                      >
                        <span className="truncate">{c.header ?? c.key}</span>
                        <Icon
                          name={active && sort.dir === 'asc' ? 'chevron-up' : 'chevron-down'}
                          size={11}
                          className={cx('shrink-0 transition-opacity', active ? 'opacity-100' : 'opacity-0')}
                        />
                      </button>
                    ) : (
                      <span className="truncate">{c.header ?? c.key}</span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>

          <tbody ref={bodyRef} className="divide-y divide-hx-border-subtle">
            {!loading &&
              sorted.map((row, i) => {
                const key = rowKey(row, i);
                const selected = selectedKey !== undefined && key === selectedKey;
                return (
                  <tr
                    key={key}
                    data-row=""
                    tabIndex={onRowClick ? 0 : -1}
                    aria-selected={selectedKey !== undefined ? selected : undefined}
                    onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                    onKeyDown={onRowClick ? (e) => onRowKeyDown(e, row, i) : undefined}
                    style={{ height: rowH }}
                    className={cx(
                      'transition-colors duration-75',
                      onRowClick && 'cursor-pointer hx-focus-inset',
                      selected ? 'bg-hx-accent-500/[0.10]' : 'hover:bg-white/[0.035]',
                    )}
                  >
                    {visible.map((c) => {
                      const right = c.align === 'right' || (c.align === undefined && c.numeric);
                      return (
                        <td
                          key={c.key}
                          className={cx(
                            'px-2 text-hx-12 truncate',
                            right ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                            // Numerics get mono + tabular figures so digits line
                            // up column-wise and don't shimmy as values tick.
                            (c.numeric || c.mono) ? 'hx-mono text-hx-text-hi' : 'text-hx-text-mid',
                            c.className,
                          )}
                        >
                          {c.render ? c.render(row, i) : cellValue(c, row) ?? '--'}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
          </tbody>
        </table>

        {loading && <SkeletonRows rows={8} cols={Math.min(visible.length || 4, 6)} />}

        {!loading && sorted.length === 0 && (
          <EmptyState title={emptyTitle} hint={emptyHint} action={emptyAction} />
        )}
      </div>
    </div>
  );
}

export default DataGrid;
