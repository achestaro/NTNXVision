// 40 px command bar, dark in every theme: brand · ⌘K search · run/connection readout ·
// theme menu (System / Light / Dark / High contrast) · help.

import React from 'react';
import { MagnifyingGlassIcon, QuestionIcon } from '@phosphor-icons/react';
import { Icon } from '../components/Icon';
import { NutanixIcon } from '../components/icons';
import { useRouter } from '../lib/router';
import { modKey, useSettings } from '../lib/settings';
import { runCounts, useRun } from '../lib/run';
import { fmtCount } from '../lib/format';
import { ThemeModeMenu } from './ThemeModeMenu';

export function CommandBar({
  onOpenPalette,
  helpOpen,
  onToggleHelp,
  readout,
}: {
  onOpenPalette(): void;
  helpOpen: boolean;
  onToggleHelp(): void;
  /** Latest snapshot's source · version, shown when nothing is running. */
  readout: string | null;
}): JSX.Element {
  const { navigate } = useRouter();
  const { appInfo } = useSettings();
  const { run, isRunning } = useRun();

  let live: string | null = null;
  if (run && isRunning) {
    const c = runCounts(run);
    const who = run.multi ? `${fmtCount(run.targets.length)} PCs` : run.label.split('.')[0];
    live = c.total ? `Collecting ${who} · ${fmtCount(c.done)} / ${fmtCount(c.total)}` : `Collecting ${who}…`;
  }

  return (
    <header className="cbar">
      <button type="button" className="brand" onClick={() => navigate({ screen: 'overview' })} aria-label="NTNXVision — go to Overview">
        <NutanixIcon name="nutanix-x" size={20} />
        NTNXVision
      </button>
      <button className="cmdk" type="button" aria-label="Search" aria-keyshortcuts="Meta+K Control+K" onClick={onOpenPalette}>
        <Icon icon={MagnifyingGlassIcon} size={16} />
        <span className="cmdk-label">Jump to cluster, VM, snapshot…</span>
        <span className="kbd">{modKey()}K</span>
      </button>
      <div style={{ flex: 1 }} />
      {appInfo?.demo && (
        <span className="bar-readout" title="Demo mode: collections run against the built-in synthetic Nutanix estate, not your network">
          Demo data
        </span>
      )}
      {live ? (
        <button
          type="button"
          className="bar-readout live"
          onClick={() => navigate({ screen: 'collecting' })}
          aria-label={`${live} — show progress`}
        >
          <span className="g" style={{ background: 'var(--bar-live)' }} aria-hidden="true" />
          {live}
        </button>
      ) : (
        readout && <span className="bar-readout">{readout}</span>
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
