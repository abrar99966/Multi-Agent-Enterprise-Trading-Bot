/**
 * Badge — a static, non-interactive label chip.
 *
 * tone: neutral | accent | pos | neg | warn | info
 * size: xs (grid cells) | sm (headers, default)
 * Optional `icon` so the badge never relies on colour alone to carry meaning.
 * `dot` renders a leading marker instead of an icon for tighter rows.
 */
import React from 'react';
import { Icon } from './Icon';
import { TONE_TEXT, TONE_BG, TONE_BORDER, TONE_SOLID, cx } from './tokens';

const SIZES = {
  xs: { box: 'h-[16px] px-1.5 gap-1 text-hx-10 rounded', icon: 10 },
  sm: { box: 'h-[20px] px-2 gap-1 text-hx-11 rounded', icon: 12 },
};

export function Badge({ children, tone = 'neutral', size = 'sm', icon, dot = false, className = '', ...rest }) {
  const s = SIZES[size] || SIZES.sm;
  return (
    <span
      className={cx(
        'inline-flex items-center border font-medium uppercase tracking-wide whitespace-nowrap align-middle',
        s.box,
        TONE_TEXT[tone] || TONE_TEXT.neutral,
        TONE_BG[tone] || TONE_BG.neutral,
        TONE_BORDER[tone] || TONE_BORDER.neutral,
        className,
      )}
      {...rest}
    >
      {dot && <span className={cx('h-1.5 w-1.5 rounded-full shrink-0', TONE_SOLID[tone] || TONE_SOLID.neutral)} />}
      {!dot && icon && <Icon name={icon} size={s.icon} />}
      {children}
    </span>
  );
}

/**
 * CountBadge — a numeric pill for unread/pending counts. Caps at `max` and
 * appends "+" so the badge width stays bounded in a sidebar.
 */
export function CountBadge({ count = 0, max = 99, tone = 'accent', className = '' }) {
  if (!count) return null;
  return (
    <span
      className={cx(
        'inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full',
        'text-hx-10 font-semibold hx-tnum',
        TONE_TEXT[tone] || TONE_TEXT.accent,
        TONE_BG[tone] || TONE_BG.accent,
        className,
      )}
      aria-label={`${count} items`}
    >
      {count > max ? `${max}+` : count}
    </span>
  );
}

export default Badge;
