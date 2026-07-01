import React, { useState } from 'react';
import { ConnectView } from './views/ConnectView';
import { SnapshotsView } from './views/SnapshotsView';
import logoUrl from './assets/logo.svg';

type Tab = 'connect' | 'snapshots' | 'about';

export function App(): JSX.Element {
  const [tab, setTab] = useState<Tab>('connect');

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src={logoUrl} alt="" />
          <span className="brand-name">
            NTNX<span>Vision</span>
          </span>
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
          NTNXVision connects to Nutanix Prism Central or directly to a cluster&apos;s Prism
          Element (read-only) and inventories the full Nutanix Cloud Platform — clusters,
          hosts, VMs, storage, networking, data protection &amp; DR, Flow, Files/Objects,
          IAM, X-Play, NCM, licensing, and NDB — then exports to Excel, PDF, or a Word
          Architecture Design Document.
        </p>
        <p className="muted">
          Credentials are stored in your OS keychain and never written to disk in plaintext.
          Exports and snapshots are saved under your user profile.
        </p>
        <p className="muted">Version 0.1.0</p>
      </div>
    </>
  );
}
