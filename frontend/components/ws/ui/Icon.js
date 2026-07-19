/**
 * Inline SVG icon set. 1.5px stroke, 24px viewBox, rendered at 16/18/20px.
 *
 * WHY hand-rolled: no icon package may be installed, and inlining keeps icons
 * in the same paint as their button (no flash-of-missing-icon on first render).
 * Every path uses currentColor, so tone is inherited from the parent's text
 * colour — icons never carry their own colour logic.
 *
 * Usage: <Icon name="risk" size={16} />   <Icon name="check" className="text-hx-pos-400" />
 */

// Path geometry only. Keyed by name; `d` may be a string or an array of strings.
const PATHS = {
  // --- navigation ---------------------------------------------------------
  dashboard: ['M3 3h7v9H3z', 'M14 3h7v5h-7z', 'M14 12h7v9h-7z', 'M3 16h7v5H3z'],
  markets: ['M3 17l5-6 4 3 5-7 4 4', 'M3 21h18'],
  portfolio: ['M3 7h18v13H3z', 'M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 'M3 12h18'],
  strategies: ['M6 3v6a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v6', 'M3 6h6', 'M15 18h6', 'M3 3h3v3H3z', 'M18 18h3v3h-3z'],
  orders: ['M8 6h13', 'M8 12h13', 'M8 18h13', 'M3 6h.01', 'M3 12h.01', 'M3 18h.01'],
  risk: ['M12 3l9 16H3z', 'M12 10v4', 'M12 17h.01'],
  learning: ['M3 8l9-4 9 4-9 4z', 'M7 10v5c0 1.5 2.2 3 5 3s5-1.5 5-3v-5', 'M21 8v6'],
  replay: ['M3 12a9 9 0 1 0 3-6.7', 'M3 4v5h5'],
  copilot: ['M12 3a4 4 0 0 1 4 4v1h1a3 3 0 0 1 3 3v4a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5v-4a3 3 0 0 1 3-3h1V7a4 4 0 0 1 4-4z', 'M9.5 13.5h.01', 'M14.5 13.5h.01'],
  analytics: ['M3 21V10', 'M9 21V4', 'M15 21v-8', 'M21 21V7'],
  logs: ['M5 3h9l5 5v13H5z', 'M14 3v5h5', 'M9 13h6', 'M9 17h6'],
  settings: ['M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z'],

  // --- chrome / controls --------------------------------------------------
  search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.3-4.3'],
  bell: ['M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9', 'M13.7 21a2 2 0 0 1-3.4 0'],
  'chevron-up': 'M18 15l-6-6-6 6',
  'chevron-down': 'M6 9l6 6 6-6',
  'chevron-left': 'M15 18l-6-6 6-6',
  'chevron-right': 'M9 18l6-6-6-6',
  'chevrons-left': ['M11 17l-5-5 5-5', 'M18 17l-5-5 5-5'],
  'chevrons-right': ['M13 17l5-5-5-5', 'M6 17l5-5-5-5'],
  close: ['M18 6L6 18', 'M6 6l12 12'],
  filter: 'M3 4h18l-7 8v7l-4 2v-9z',
  download: ['M12 3v12', 'M7 11l5 5 5-5', 'M4 21h16'],
  pin: ['M12 17v5', 'M9 3h6l-1 6 3 3v2H7v-2l3-3z'],
  play: 'M7 4l13 8-13 8z',
  pause: ['M8 4v16', 'M16 4v16'],
  check: 'M4 12.5l5 5L20 6.5',
  alert: ['M12 3l9 16H3z', 'M12 9v5', 'M12 17.5h.01'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v5', 'M12 7.5h.01'],
  plus: ['M12 5v14', 'M5 12h14'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  spark: ['M12 2l2.2 6.1L20 10l-5.8 1.9L12 18l-2.2-6.1L4 10l5.8-1.9z', 'M18.5 16.5l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9z'],
  kill: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v6', 'M12 16.5h.01'],
  refresh: ['M20 11a8 8 0 1 0-2.3 6.3', 'M20 4v7h-7'],
  columns: ['M4 4h16v16H4z', 'M10 4v16', 'M16 4v16'],
  drag: ['M9 5h.01', 'M9 12h.01', 'M9 19h.01', 'M15 5h.01', 'M15 12h.01', 'M15 19h.01'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5l3.2 1.9'],
};

export const ICON_NAMES = Object.keys(PATHS);

/**
 * @param {string} name  key from ICON_NAMES
 * @param {number} size  16 | 18 | 20 (any px works)
 * @param {boolean} title  pass a string to expose the icon to screen readers;
 *   omitted → aria-hidden, which is correct when adjacent text already labels it.
 */
export function Icon({ name, size = 16, className = '', title, strokeWidth = 1.5, ...rest }) {
  const d = PATHS[name];
  if (!d) return null;
  const paths = Array.isArray(d) ? d : [d];
  const labelled = Boolean(title);

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
      role={labelled ? 'img' : undefined}
      aria-label={labelled ? title : undefined}
      aria-hidden={labelled ? undefined : 'true'}
      focusable="false"
      {...rest}
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  );
}

export default Icon;
