// SPDX-License-Identifier: MIT
// About & legal: version, copyright, the MIT license, the Nutanix trademark disclaimer and a
// viewer for the bundled NOTICE and THIRD_PARTY_NOTICES.md (read by main via getLegalDocuments).
// Reached from Settings and from the help popover.

import React, { useState } from 'react';
import type { LegalDocuments } from '@shared/ipc';
import { Button, ErrorState, Loading, PageHeader, Panel, PanelHeader, Tabs } from '../components';
import { useRouter } from '../lib/router';
import { useAsync } from '../lib/useAsync';

type DocTab = 'license' | 'notice' | 'third';

export const COPYRIGHT = 'Copyright (c) 2026 Alberto Chestaro';
export const TRADEMARK_DISCLAIMER =
  'Nutanix, Prism, AHV, AOS, Era/NDB, Flow, Files, Objects and the related names, logos and icons are ' +
  'trademarks of Nutanix, Inc. NTNXVision is an independent project and is not affiliated with, ' +
  'endorsed by, or sponsored by Nutanix, Inc.';

const preStyle: React.CSSProperties = {
  margin: 0,
  padding: 16,
  maxHeight: 520,
  overflow: 'auto',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  fontSize: 12,
  lineHeight: 1.5,
};

/** Split the notices into "#### name version" entries so the find box can narrow them. */
function filterNotices(text: string, query: string): string {
  const q = query.trim().toLowerCase();
  if (!q) return text;
  const parts = text.split(/\n(?=#### )/);
  const hits = parts.filter((p) => p.toLowerCase().includes(q));
  return hits.length ? hits.join('\n\n') : `No entries match "${query.trim()}".`;
}

export function LegalView(): JSX.Element {
  const { back, canGoBack, navigate } = useRouter();
  const docs = useAsync<LegalDocuments>(() => window.ntnx.getLegalDocuments(), []);
  const [tab, setTab] = useState<DocTab>('license');
  const [query, setQuery] = useState('');
  const d = docs.data;

  const missing = (name: string) => `${name} was not found in ${d?.source ?? 'the app resources'}.`;
  const body = !d
    ? ''
    : tab === 'license'
      ? (d.license ?? missing('LICENSE'))
      : tab === 'notice'
        ? (d.notice ?? missing('NOTICE'))
        : d.thirdPartyNotices
          ? filterNotices(d.thirdPartyNotices, query)
          : missing('THIRD_PARTY_NOTICES.md');

  const openFile = () => {
    if (!d) return;
    const sep = window.ntnx.platform === 'win32' ? '\\' : '/';
    const name = tab === 'license' ? 'LICENSE' : tab === 'notice' ? 'NOTICE' : 'THIRD_PARTY_NOTICES.md';
    void window.ntnx.openPath(`${d.source}${sep}${name}`);
  };

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="About & legal"
        description="License, trademarks and the third-party software included in NTNXVision."
        actions={
          <Button variant="ghost" onClick={() => (canGoBack ? back() : navigate({ screen: 'settings' }))}>
            Back
          </Button>
        }
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 880 }}>
        <Panel aria-labelledby="legal-about">
          <PanelHeader id="legal-about" title="About" />
          <div style={{ padding: '4px 16px 12px' }}>
            <dl>
              <div className="kv">
                <dt>Product</dt>
                <dd>NTNXVision</dd>
              </div>
              <div className="kv">
                <dt>Version</dt>
                <dd className="mono">{d?.version ?? 'N/A'}</dd>
              </div>
              <div className="kv">
                <dt>Copyright</dt>
                <dd>{COPYRIGHT}</dd>
              </div>
              <div className="kv">
                <dt>License</dt>
                <dd>MIT License, provided as is, without warranty of any kind</dd>
              </div>
              <div className="kv">
                <dt>Operation</dt>
                <dd>Read-only API calls; credentials are stored only in the OS keychain</dd>
              </div>
            </dl>
          </div>
        </Panel>

        <Panel aria-labelledby="legal-tm">
          <PanelHeader id="legal-tm" title="Trademarks" />
          <p style={{ margin: 0, padding: 16 }}>{TRADEMARK_DISCLAIMER}</p>
        </Panel>

        <Panel aria-labelledby="legal-docs">
          <PanelHeader id="legal-docs" title="Documents">
            {d && (
              <Button variant="ghost" size="sm" onClick={openFile}>
                Open file
              </Button>
            )}
          </PanelHeader>
          <div style={{ padding: '0 16px' }}>
            <Tabs<DocTab>
              label="Legal documents"
              panelId="legal-doc-panel"
              value={tab}
              onChange={setTab}
              tabs={[
                { id: 'license', label: 'MIT License' },
                { id: 'notice', label: 'Notice' },
                { id: 'third', label: 'Third-party notices' },
              ]}
            />
          </div>
          {tab === 'third' && d?.thirdPartyNotices && (
            <div style={{ padding: '12px 16px 0' }}>
              <label className="lbl" htmlFor="legal-find">
                Find a component
              </label>
              <input
                id="legal-find"
                className="input"
                style={{ maxWidth: 360 }}
                value={query}
                placeholder="e.g. react, fpdf2, OFL"
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          )}
          <div id="legal-doc-panel" role="tabpanel">
            {docs.loading && !d ? (
              <div style={{ padding: 16 }}>
                <Loading />
              </div>
            ) : docs.error ? (
              <div style={{ padding: 16 }}>
                <ErrorState title="Couldn’t read the legal documents" error={docs.error} onRetry={docs.reload} />
              </div>
            ) : (
              <pre className="mono" style={preStyle} tabIndex={0}>
                {body}
              </pre>
            )}
          </div>
        </Panel>
      </div>
    </>
  );
}
