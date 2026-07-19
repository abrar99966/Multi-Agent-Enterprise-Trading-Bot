/**
 * Timeline — vertical event log: severity dot, timestamp, title, optional body.
 *
 * Used for order lifecycle, agent decisions, risk events, and audit trails. The
 * rail is drawn as a border on the dot column rather than a pseudo-element per
 * row, so a 500-event list stays cheap to paint.
 *
 * Events: [{ id, at, title, body, severity, icon, meta, onClick }]
 */
import React from 'react';
import { Icon } from './Icon';
import { EmptyState } from './EmptyState';
import { SEVERITY_META, TONE_TEXT, TONE_SOLID, severityTone, toSeverity, fmtTime, cx } from './tokens';

export function TimelineItem({ event, dense = false, timeMode = 'time', isLast = false }) {
  const sev = toSeverity(event.severity);
  const tone = severityTone(sev);
  const meta = SEVERITY_META[sev];
  const Tag = event.onClick ? 'button' : 'div';

  return (
    <Tag
      type={event.onClick ? 'button' : undefined}
      onClick={event.onClick}
      className={cx(
        'relative flex gap-2.5 w-full text-left group',
        dense ? 'py-1' : 'py-1.5',
        event.onClick && 'hx-focus-inset rounded hover:bg-white/[0.03] transition-colors cursor-pointer',
      )}
    >
      {/* rail + dot column */}
      <span className="relative flex flex-col items-center shrink-0 w-3">
        <span className={cx('mt-[5px] h-2 w-2 rounded-full ring-2 ring-hx-panel shrink-0', TONE_SOLID[tone])} />
        {!isLast && <span className="flex-1 w-px bg-hx-border-subtle mt-1" aria-hidden="true" />}
      </span>

      <span className="flex-1 min-w-0 pb-1">
        <span className="flex items-baseline justify-between gap-2 min-w-0">
          <span className="flex items-center gap-1.5 min-w-0">
            {event.icon && <Icon name={event.icon} size={12} className={cx('shrink-0', TONE_TEXT[tone])} />}
            <span className="text-hx-12 font-medium text-hx-text-hi truncate">{event.title}</span>
            {/* Severity word makes the dot colour redundant, not load-bearing. */}
            {sev !== 'ok' && (
              <span className={cx('text-hx-10 uppercase tracking-wide font-semibold shrink-0', TONE_TEXT[tone])}>
                {meta.label}
              </span>
            )}
          </span>
          <time
            className="text-hx-10 text-hx-text-dim hx-mono shrink-0"
            dateTime={event.at ? new Date(event.at).toISOString?.() : undefined}
          >
            {fmtTime(event.at, { mode: timeMode })}
          </time>
        </span>

        {event.body && (
          <span className="block text-hx-11 text-hx-text-lo leading-relaxed mt-0.5">{event.body}</span>
        )}

        {event.meta && (
          <span className="block text-hx-10 text-hx-text-dim hx-mono mt-0.5 truncate">{event.meta}</span>
        )}
      </span>
    </Tag>
  );
}

export function Timeline({
  events = [],
  dense = false,
  timeMode = 'time',
  loading = false,
  emptyTitle = 'No events',
  emptyHint,
  className = '',
}) {
  if (!loading && (!events || events.length === 0)) {
    return <EmptyState title={emptyTitle} hint={emptyHint} icon="logs" />;
  }

  return (
    <ol className={cx('flex flex-col', className)} aria-busy={loading || undefined}>
      {events.map((e, i) => (
        <li key={e.id ?? `${e.at}-${i}`}>
          <TimelineItem event={e} dense={dense} timeMode={timeMode} isLast={i === events.length - 1} />
        </li>
      ))}
    </ol>
  );
}

export default Timeline;
