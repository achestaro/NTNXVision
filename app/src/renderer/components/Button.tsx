// Runway button: one primary per view; secondary (outlined), ghost, destructive outline,
// and a red-filled variant reserved for the confirm of a destructive dialog.

import React, { forwardRef } from 'react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import { Icon } from './Icon';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'destructive' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

type Base = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: PhosphorIcon;
  /** Shortcut hint rendered as a key cap after the label (e.g. "⌘↵"). */
  kbd?: string;
  block?: boolean;
};

// Icon-only buttons must carry an aria-label.
export type ButtonProps =
  | (Base & { children: React.ReactNode; iconOnly?: false })
  | (Base & { iconOnly: true; icon: PhosphorIcon; 'aria-label': string; children?: never });

export function buttonClass(variant: ButtonVariant = 'secondary', size: ButtonSize = 'md', extra = ''): string {
  return [
    'btn',
    variant === 'primary' && 'primary',
    variant === 'ghost' && 'ghost',
    variant === 'destructive' && 'destructive',
    variant === 'danger' && 'danger-fill',
    size === 'sm' && 'sm',
    size === 'lg' && 'lg',
    extra,
  ]
    .filter(Boolean)
    .join(' ');
}

/** Key caps are written with ⌘; Windows and Linux show Ctrl+ instead ("⌘↵" → "Ctrl+↵"). */
function platformKbd(kbd: string): string {
  return window.ntnx?.platform === 'darwin' ? kbd : kbd.replace(/⌘/g, 'Ctrl+');
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(props, ref) {
  const { variant = 'secondary', size = 'md', icon, kbd, block, className, iconOnly, children, type, ...rest } =
    props as Base & { iconOnly?: boolean; children?: React.ReactNode };
  const cls = buttonClass(variant, size, [iconOnly && 'icon', block && 'block', className].filter(Boolean).join(' '));
  return (
    <button ref={ref} type={type ?? 'button'} className={cls} {...rest}>
      {icon && <Icon icon={icon} size={size === 'sm' && iconOnly ? 16 : size === 'sm' ? 14 : 16} />}
      {children}
      {kbd && (
        <span className="kbd" style={{ marginLeft: 4 }}>
          {platformKbd(kbd)}
        </span>
      )}
    </button>
  );
});
