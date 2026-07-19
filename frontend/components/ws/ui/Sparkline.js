/**
 * Sparkline — dependency-free inline SVG trend line.
 *
 * props: values (number[]), tone, height, width, filled (area under line),
 *        baseline (draw a zero/flat reference), autoTone (green up / red down
 *        based on first vs last value).
 *
 * WHY viewBox + preserveAspectRatio="none": the SVG stretches to whatever grid
 * cell it lands in without us measuring the DOM — no ResizeObserver, no layout
 * thrash during a 20s poll cycle. Stroke width is compensated so it doesn't
 * smear when the x-axis stretches.
 */
import React, { useId } from 'react';
import { TONE_HEX, cx } from './tokens';

export function Sparkline({
  values = [],
  tone = 'accent',
  autoTone = false,
  height = 24,
  width = 80,
  filled = false,
  baseline = false,
  strokeWidth = 1.25,
  className = '',
  'aria-label': ariaLabel,
}) {
  const gradId = useId();
  const clean = (values || []).map(Number).filter((n) => Number.isFinite(n));

  // Under 2 points there's no trend to draw — render a flat rule so the cell
  // keeps its height and the row doesn't jump when data arrives.
  if (clean.length < 2) {
    return (
      <svg width={width} height={height} className={cx('block', className)} aria-hidden="true">
        <line
          x1="0" y1={height / 2} x2={width} y2={height / 2}
          stroke="currentColor" strokeOpacity="0.15" strokeWidth="1" strokeDasharray="2 3"
          className="text-hx-text-dim"
        />
      </svg>
    );
  }

  const resolvedTone = autoTone
    ? clean[clean.length - 1] >= clean[0] ? 'pos' : 'neg'
    : tone;
  const color = TONE_HEX[resolvedTone] || TONE_HEX.accent;

  const min = Math.min(...clean);
  const max = Math.max(...clean);
  // Flat series would divide by zero; centre it instead.
  const span = max - min || 1;
  const pad = strokeWidth; // keep the stroke inside the box at the extremes
  const h = height - pad * 2;

  const pts = clean.map((v, i) => {
    const x = (i / (clean.length - 1)) * width;
    const y = pad + h - ((v - min) / span) * h;
    return [x, y];
  });

  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
  const area = `${line} L${width} ${height} L0 ${height} Z`;
  const zeroY = pad + h - ((0 - min) / span) * h;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={cx('block overflow-visible', className)}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : 'true'}
    >
      {filled && (
        <>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.22" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradId})`} />
        </>
      )}
      {baseline && min <= 0 && max >= 0 && (
        <line x1="0" y1={zeroY} x2={width} y2={zeroY} stroke={color} strokeOpacity="0.25" strokeWidth="0.75" strokeDasharray="2 2" />
      )}
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export default Sparkline;
