import React, { useState } from 'react';
import { ConnectView } from './views/ConnectView';
import { SnapshotsView } from './views/SnapshotsView';

type Tab = 'connect' | 'snapshots' | 'about';

export function App(): JSX.Element {
  const [tab, setTab] = useState<Tab>('connect');

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          NTNX<span>Vision</span>
        </div>
        <div className="tagline">Nutanix inventory &amp; documentation</div>
        <nav className="nav">
          <button className={tab === 'connect' ? 'active' : ''} onClick={() => setTab('connect')}>
            Collect Inventory
          </button>
          <button className={tab === 'snapshots' ? 'active' : ''} onClick={() => setTab('snapshots')}>
            Snapshots &amp; Exports
          </button>
          <button className={tab === 'about' ? 'active' : ''} onClick={() => setTab('about')}>
            About
          </button>
        </nav>
      </aside>

      <main className="main">
        {tab === 'connect' && <ConnectView />}
        {tab === 'snapshots' && <SnapshotsView />}
        {tab === 'about' && <AboutView />}
      </main>
    </div>
  );
}

function AboutView(): JSX.Element {
  return (
    <>
      <h1>About NTNXVision</h1>
      <p className="subtitle">The "RVTools for Nutanix" — extended to the full Cloud Platform.</p>
      <div className="card">
        <p>
          NTNXVision connects to Nutanix Prism Central (read-only) and inventories your
          environment — clusters, hosts, VMs, storage, and networking today; data protection,
          Flow, Files/Objects, and NDB in upcoming releases.
        </p>
        <p className="muted">
          Credentials are stored in your OS keychain and never written to disk in plaintext.
          Exports and snapshots are saved under your user profile.
        </p>
        <p className="muted">Version 0.1.0 — Phase 1 (inventory MVP).</p>
      </div>
    </>
  );
}
