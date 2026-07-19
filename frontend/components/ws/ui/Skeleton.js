/**
 * Skeleton — shimmer placeholders that reserve the exact space real content
 * will occupy, so first paint doesn't shift the layout when a poll resolves.
 *
 * Skeleton      — one block (w/h/rounded controllable)
 * SkeletonText  — n stacked lines, last line short like real prose
 * SkeletonRows  — grid-row placeholders matching the 28px dense row rhythm
 */
import React from 'react';
import { cx } from './tokens';

export function Skeleton({ className = '', w, h = 12, rounded = 'rounded', style, ...rest }) {
  return (
    <div
      // aria-hidden: the loading state is announced once by the container's
      // aria-busy; per-block announcements would spam the screen reader.
      aria-hidden="true"
      className={cx('hx-shimmer-bg animate-hx-shimmer', rounded, className)}
      style={{ width: w, height: h, ...style }}
      {...rest}
    />
  );
}

export function SkeletonText({ lines = 3, className = '' }) {
  return (
    <div className={cx('space-y-1.5', className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} h={10} className={i === lines - 1 ? 'w-1/2' : 'w-full'} />
      ))}
    </div>
  );
}

export function SkeletonRows({ rows = 6, cols = 4, className = '' }) {
  return (
    <div className={cx('divide-y divide-hx-border-subtle', className)} aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex items-center gap-3 px-3" style={{ height: 'var(--hx-row-h, 28px)' }}>
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton
              key={c}
              h={8}
              // First column is the label (wide); the rest are numerics (narrow).
              className={c === 0 ? 'flex-1' : 'w-16'}
              // Stagger so the sweep reads as one wave down the table.
              style={{ animationDelay: `${(r * cols + c) * 40}ms` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export default Skeleton;
