// 200 px rail with labelled groups (Overview · Collect · Explore) and Settings at the foot.
// Active item: white text, bar-hover fill, 2 px bar-accent inset, duotone glyph.

import React from 'react';
import { Icon } from '../components/Icon';
import { useRouter } from '../lib/router';
import { NAV_GROUPS, NavItem, SETTINGS_ITEM } from './nav';

export function Rail({ version, coreState }: { version: string | null; coreState: 'starting' | 'ready' | 'error' }): JSX.Element {
  const { route, navigate } = useRouter();
  const item = (it: NavItem) => {
    const on = route.screen === it.screen || !!it.also?.includes(route.screen);
    return (
      <button
        key={it.screen}
        type="button"
        className={on ? 'ri on' : 'ri'}
        aria-current={on ? 'page' : undefined}
        onClick={() => navigate({ screen: it.screen } as never)}
      >
        <Icon icon={it.icon} size={20} active={on} />
        {it.label}
      </button>
    );
  };
  const coreLabel = coreState === 'ready' ? 'core ready' : coreState === 'error' ? 'core unavailable' : 'core starting';
  return (
    <nav className="rail" aria-label="Primary">
      {NAV_GROUPS.map((g, i) => (
        <div className="rail-group" key={g.label || i}>
          {g.label && <div className="rail-label">{g.label}</div>}
          {g.items.map(item)}
        </div>
      ))}
      <div style={{ flex: 1 }} />
      {item(SETTINGS_ITEM)}
      <div className="mono rail-foot">
        {version ? `v${version} · ` : ''}
        {coreLabel}
      </div>
    </nav>
  );
}
