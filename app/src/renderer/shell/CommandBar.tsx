// 40 px command bar, dark in every theme: brand · ⌘K search · live run progress ·
// theme menu (System / Light / Dark / High contrast) · help.

import React from 'react';
import { MagnifyingGlassIcon, QuestionIcon } from '@phosphor-icons/react';
import { Icon } from '../components/Icon';
import { BrandMark } from '../components/BrandMark';
import { useRouter } from '../lib/router';
import { modKey } from '../lib/settings';
import { runCounts, useRun } from '../lib/run';
import { fmtCount } from '../lib/format';
import { ThemeModeMenu } from './ThemeModeMenu';

export function CommandBar({
  onOpenPalette,
  helpOpen,
  onToggleHelp,
}: {
  onOpenPalette(): void;
  helpOpen: boolean;
  onToggleHelp(): void;
}): JSX.Element {
  const { navigate } = useRouter();
  const { run, isRunning } = useRun();

  let live: string | null = null;
  if (run && isRunning) {
    const c = runCounts(run);
    const who = run.multi ? `${fmtCount(run.targets.length)} PCs` : run.label.split('.')[0];
    live = c.total ? `Collecting ${who} · ${fmtCount(c.done)} / ${fmtCount(c.total)}` : `Collecting ${who}…`;
  }

  return (
    <header className="cbar">
      <button type="button" className="brand" onClick={() => navigate({ screen: 'overview' })} aria-label="NTNXVision: go to Overview">
        <BrandMark size={22} />
        NTNXVision
      </button>
      <button className="cmdk" type="button" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" onClick={onOpenPalette}>
        <Icon icon={MagnifyingGlassIcon} size={16} />
        <span className="cmdk-label">Jump to cluster, VM, snapshot…</span>
        <span className="kbd">{modKey()}K</span>
      </button>
      <div style={{ flex: 1 }} />
      {live && (
        <button
          type="button"
          className="bar-readout live"
          onClick={() => navigate({ screen: 'collecting' })}
          aria-label={`${live}. Show progress`}
        >
          <span className="g" style={{ background: 'var(--bar-live)' }} aria-hidden="true" />
          {live}
        </button>
      )}
      <ThemeModeMenu />
      <button
        className="bb"
        type="button"
        aria-label="Help and keyboard shortcuts"
        title="Help and keyboard shortcuts (?)"
        aria-expanded={helpOpen}
        aria-haspopup="dialog"
        data-help-button=""
        onClick={onToggleHelp}
      >
        <Icon icon={QuestionIcon} size={16} />
      </button>
    </header>
  );
}
