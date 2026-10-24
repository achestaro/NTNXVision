// Synthetic `window.ntnx` for running the built renderer in a plain browser (screenshots,
// UI work without a cluster or the Python sidecar). Every method returns data shaped like
// docs/IPC_CONTRACT.md, seeded with the sample rows from docs/design/runway-mocks.
//
// Options (set window.__STUB__ before this script runs):
//   theme:    'light' | 'dark' | 'hc' | 'system'   (initial settings.theme)
//   accent:   'iris' | 'prism'
//   empty:    true → no snapshots, no saved targets (empty states)
//   noTargets: true → snapshots but no saved targets (New collection / Multiple PCs empty states)
//   run:      'hold'   → a started collection freezes mid-run (Collecting screen)
//             'finish' → a started collection completes after ~0.5 s (Results screen)
//             'fail'   → a started collection fails
//   test:     'ok' | 'auth_failed' | 'untrusted_cert' | 'trust_required' | 'cert_changed' | 'unreachable' | 'version_unsupported'
//             (trust_required answers ok once a certificate is trusted: req.pinnedCert, or trustCertificate on the saved target)
//   clusters: 'ok'      → listClusters answers 4 clusters (1 for a PE or pc-lab.example.com)
//             'loading' → listClusters never answers (the picker's loading state)
//             'error'   → listClusters fails (the picker's error state with retry)
//   scopeNotice: true → compareSnapshots reports different cluster scopes
//   platform: 'darwin' | 'win32' | 'linux'  ("Show in Finder" / "Show in Explorer" / "Show in folder")
//   smtp:     'configured' (default) | 'empty' → Settings > Email filled in or not set up
//   smtpTest: 'ok' (default) | an EmailStatus ('auth_failed', 'untrusted_cert', 'unreachable', …) → Send test email
//   email:    'ok' (default) | an EmailStatus ('too_large', 'recipient_rejected', …) → Email dialog send
//   schedules: 'samples' (default) | 'empty'
//   longNotes: true → a run whose notes, version string and API routing carry very long text
//             and unbroken tokens (URLs, build strings) to check that cards contain them
(function () {
  const opt = Object.assign({ theme: 'light', accent: 'iris', run: 'hold', test: 'ok', clusters: 'ok', platform: 'darwin' }, window.__STUB__ || {});
  const GB = 1024 ** 3;
  const MB = 1024 ** 2;
  const year = new Date().getFullYear();
  const at = (m, d, hh, mm, ss = 0) => new Date(year, m - 1, d, hh, mm, ss).toISOString();
  const delay = (v, ms = 40) => new Promise((r) => setTimeout(() => r(typeof v === 'function' ? v() : v), ms));
  const fail = (msg, ms = 40) => new Promise((_, rej) => setTimeout(() => rej(new Error(msg)), ms));
  const DIR = opt.platform === 'win32' ? 'C:\\Users\\alberto\\AppData\\Roaming\\NTNXVision\\snapshots' : '/Users/alberto/Library/Application Support/NTNXVision/snapshots';
  const EXPORT_DIR = opt.platform === 'win32' ? 'C:\\Users\\alberto\\Documents\\NTNXVision' : '/Users/alberto/Documents/NTNXVision';
  const sep = opt.platform === 'win32' ? '\\' : '/';

  const summary = (clusters, hosts, vms) => ({ clusters, hosts, vms, storage_containers: 18, subnets: 42, licenses: 8, protection_policies: 12, security_policies: 24 });
  const snap = (name, kind, source, version, when, c, h, v, sizeMb, scope = null) => ({
    name,
    path: `${DIR}${sep}${name}.ntnxsnap.json`,
    collected_at: when,
    summary: summary(c, h, v),
    kind,
    source,
    version,
    size_bytes: sizeMb * MB,
    scope,
  });

  // ── registered clusters (listClusters) and cluster scopes ──
  const REGISTERED = [
    { uuid: 'c-ams-edge-02', name: 'AMS-EDGE-02', aos_version: '6.10', num_nodes: 2, hypervisors: ['ESXi'] },
    { uuid: 'c-phx-dr-01', name: 'PHX-DR-01', aos_version: '7.0', num_nodes: 6, hypervisors: ['AHV'] },
    { uuid: 'c-phx-prod-01', name: 'PHX-PROD-01', aos_version: '6.10.1', num_nodes: 8, hypervisors: ['AHV'] },
    { uuid: 'c-phx-prod-02', name: 'PHX-PROD-02', aos_version: '7.0', num_nodes: 12, hypervisors: ['AHV'] },
  ];
  const scopeOf = (selected, registered = REGISTERED) => {
    const pick = registered.filter((c) => selected.includes(c.uuid));
    const rest = registered.filter((c) => !selected.includes(c.uuid));
    const ref = (c) => ({ uuid: c.uuid, name: c.name });
    return {
      selected: pick.map(ref),
      excluded: rest.map(ref),
      registered_count: registered.length,
      label: rest.length ? `${pick.length} of ${registered.length} clusters (${pick.map((c) => c.name).join(', ')})` : registered.length === 1 ? '1 cluster' : `All ${registered.length} clusters`,
    };
  };
  const SEA = ['SEA-PROD-01', 'SEA-PROD-02', 'SEA-VDI-01'].map((n) => ({ uuid: `c-${n.toLowerCase()}`, name: n }));
  let snapshots = opt.empty
    ? []
    : [
        snap('pc01 Q4 review', 'pc', 'pc01.example.com', 'pc.2024.3', at(10, 15, 9, 45), 4, 28, 1214, 18, scopeOf(REGISTERED.map((c) => c.uuid))),
        snap('Estate Q4 (combined)', 'combined', '3 Prism Centrals', null, at(10, 14, 17, 20), 9, 71, 3486, 52),
        snap('pc02 production', 'pc', 'pc02.example.com', 'pc.7.3', at(10, 14, 17, 18), 2, 20, 1430, 19, scopeOf(['c-sea-prod-01', 'c-sea-prod-02'], SEA)),
        snap('pc03 baseline', 'pc', 'pc03.example.com', 'pc.2024.1', at(10, 14, 17, 19), 2, 16, 682, 9),
        snap('phx-dr-01', 'pe', 'phx-dr-01.example.com', 'AOS 7.0', at(10, 9, 11, 2), 1, 6, 212, 3),
        snap('pc01 September', 'pc', 'pc01.example.com', 'pc.2024.3', at(9, 30, 8, 15), 4, 24, 1176, 17),
        snap('pc01 August', 'pc', 'pc01.example.com', 'pc.2024.2', at(8, 29, 16, 40), 4, 24, 1120, 16),
      ];

  // Saved targets (Targets page). Ids are stable UUID-like strings, as in the real app.
  const tested = (status, ok, message, version, when) => ({ status, ok, message, version, at: when });
  let connections = opt.empty || opt.noTargets
    ? []
    : [
        { id: 't-pc01', mode: 'prism_central', host: 'pc01.example.com', username: 'admin', port: 9440, insecure: false, name: 'PC01 Production', ndbHost: 'ndb01.example.com', ndbUser: 'admin', vcpuRatioOverrides: { 'c-phx-prod-01': 6 }, hasPassword: true, ndbHasPassword: true, lastTest: tested('ok', true, 'Connected to Prism Central pc.2024.3', 'pc.2024.3', at(10, 15, 9, 40)) },
        { id: 't-pc02', mode: 'prism_central', host: 'pc02.example.com', username: 'admin', port: 9440, insecure: false, name: 'PC02 DR site', hasPassword: true, lastTest: tested('ok', true, 'Connected to Prism Central pc.7.3', 'pc.7.3', at(10, 14, 17, 10)) },
        { id: 't-pc03', mode: 'prism_central', host: 'pc03.example.com', username: 'svc-inventory', port: 9440, insecure: false, name: 'PC03 Labs', hasPassword: false, lastTest: tested('auth_failed', false, 'HTTP 401 Unauthorized', null, at(10, 12, 11, 5)) },
        { id: 't-phx', mode: 'prism_element', host: 'phx-dr-01.example.com', username: 'admin', port: 9440, insecure: false, name: 'phx-dr-01', hasPassword: true, lastTest: null },
        { id: 't-lab', mode: 'prism_central', host: 'pc-lab.example.com', username: 'admin', port: 9440, insecure: true, name: 'Lab PC', hasPassword: true, lastTest: tested('version_unsupported', true, 'Connected to Prism Central pc.2023.4', 'pc.2023.4', at(10, 1, 15, 30)) },
      ];

  let settings = {
    theme: opt.theme,
    accent: opt.accent,
    density: 'comfortable',
    exportDir: '',
    author: 'Alberto Chestaro',
    multiTargetIds: ['t-pc01', 't-pc02', 't-pc03'],
    ...(opt.customOrder
      ? { customSectionOrder: true, sectionOrder: ['data_protection', 'networking', 'workloads', 'physical_design', 'logical_design'] }
      : {}),
  };

  // Report sections the stub snapshots have no data for (Export dialog > Sections and order).
  const NO_DATA = new Set(['nus', 'ndb', 'automation']);
  const REPORT_SECTIONS = ['executive_summary', 'licensing', 'assumptions', 'prism_central', 'logical_design', 'physical_design', 'workloads', 'catalog', 'nus', 'networking', 'data_protection', 'security', 'ndb', 'identity', 'automation', 'health', 'lcm', 'appendix'];


  // ── email (SMTP) and schedules ──
  let smtp = opt.smtp === 'empty' || opt.empty
    ? { host: '', port: 587, security: 'starttls', username: '', fromAddress: '', fromName: 'NTNXVision', allowUntrusted: false, maxAttachmentMb: 20, zipAttachments: false, hasPassword: false, configured: false }
    : { host: 'smtp.example.com', port: 587, security: 'starttls', username: 'ntnxvision@example.com', fromAddress: 'ntnxvision@example.com', fromName: 'NTNXVision', allowUntrusted: false, maxAttachmentMb: 20, zipAttachments: false, hasPassword: true, configured: true };
  const EMAIL_FAIL = {
    auth_failed: ['The server rejected the username or password.', '535 5.7.8 Authentication credentials invalid'],
    auth_required: ['The server requires sign-in. Enter a username and password.', '530 5.7.0 Authentication required'],
    tls_error: ['The secure connection failed. Check the Security setting for this port.', '[SSL: WRONG_VERSION_NUMBER] wrong version number'],
    untrusted_cert: ['The server’s certificate is not trusted. Turn on Allow untrusted certificate if you trust this server.', 'certificate verify failed: self-signed certificate'],
    unreachable: ['Can’t connect to smtp.example.com:587. The connection was refused.', '[Errno 61] Connection refused'],
    timeout: ['smtp.example.com:587 did not answer within 20 s.', 'timed out'],
    recipient_rejected: ['The server refused 1 recipient.', '550 5.1.1 User unknown'],
    sender_rejected: ['The server refused the From address.', '553 5.7.1 Sender address rejected'],
    too_large: ['Attachments total 34.2 MB, over the 20 MB limit. Turn on Zip attachments, choose fewer formats or raise the limit in Settings > Email.', null],
    not_configured: ['Set up the mail server in Settings > Email first.', null],
    invalid: ['Check the email settings.', null],
    error: ['The server closed the connection.', 'Connection unexpectedly closed'],
  };
  const emailFail = (status, extra = {}) => ({ ok: false, status, message: EMAIL_FAIL[status]?.[0] ?? 'Email failed.', detail: EMAIL_FAIL[status]?.[1] ?? null, ...extra });

  const nextAt = (match, hh, mm) => {
    const d = new Date();
    for (let i = 0; i < 40; i++) {
      const c = new Date(d.getFullYear(), d.getMonth(), d.getDate() + i, hh, mm);
      if (c > d && match(c)) return c.toISOString();
    }
    return null;
  };
  const runRec = (id, trigger, status, when, message, extra = {}) => ({ id, trigger, status, startedAt: when, finishedAt: when, message, ...extra });
  let schedules = opt.schedules === 'empty' || opt.empty
    ? []
    : [
        {
          id: 's-weekly', name: 'Weekly PC01 report', enabled: true, targetIds: ['t-pc01'], combine: false, formats: ['excel', 'pdf'],
          to: ['ops@example.com', 'infra-team@example.com'], cc: ['manager@example.com'], subject: 'NTNXVision report: {target} ({date})', body: 'Attached are the NTNXVision reports for {target}.\n\n{summary}\n',
          frequency: { kind: 'weekly', days: [1, 4], time: '07:00' }, createdAt: at(9, 1, 9, 0), updatedAt: at(9, 1, 9, 0), anchor: at(10, 16, 7, 0),
          history: [
            runRec('r3', 'scheduled', 'ok', at(10, 16, 7, 4), 'Emailed 2 reports to 3 recipients', { snapshots: ['Weekly PC01 report-6'] }),
            runRec('r2', 'catch_up', 'ok', at(10, 13, 8, 31), 'Emailed 2 reports to 3 recipients', { snapshots: ['Weekly PC01 report-5'] }),
            runRec('r1', 'manual', 'failed', at(10, 9, 15, 12), 'Email failed: the server rejected the username or password.', { stage: 'email' }),
          ],
          state: 'idle', problems: [], nextRun: nextAt((c) => [1, 4].includes(c.getDay()), 7, 0),
        },
        {
          id: 's-monthly', name: 'Monthly estate review', enabled: true, targetIds: ['t-pc01', 't-pc02', 't-pc03'], combine: true, formats: ['excel', 'word'],
          sections: ['executive_summary', 'clusters_hosts', 'workloads_storage', 'data_protection'],
          to: ['architecture@example.com'], cc: [], subject: 'Monthly estate review ({date})', body: 'Monthly NTNXVision estate review.\n\n{summary}\n',
          frequency: { kind: 'monthly', day: 1, time: '06:30' }, createdAt: at(8, 20, 10, 0), updatedAt: at(9, 2, 10, 0), anchor: at(10, 1, 6, 30),
          history: [
            runRec('m2', 'scheduled', 'failed', at(10, 1, 6, 41), 'Collection failed: pc03.example.com unreachable', { stage: 'collect' }),
            runRec('m1', 'scheduled', 'ok', at(9, 1, 6, 52), 'Emailed 2 reports to 1 recipient', { snapshots: ['Monthly estate review'] }),
          ],
          state: 'idle', problems: [], nextRun: nextAt((c) => c.getDate() === 1, 6, 30),
        },
        {
          id: 's-daily', name: 'Nightly DR cluster', enabled: false, targetIds: ['t-phx'], combine: false, formats: ['excel'],
          to: ['dr-oncall@example.com'], cc: [], subject: 'NTNXVision report: {target} ({date})', body: 'Attached are the NTNXVision reports for {target}.\n',
          frequency: { kind: 'daily', time: '22:00' }, createdAt: at(10, 2, 12, 0), updatedAt: at(10, 2, 12, 0), anchor: at(10, 2, 12, 0),
          history: [], state: 'idle', problems: [], nextRun: null,
        },
      ];
  for (const s of schedules) s.lastRun = s.history[0] ?? null;
  const schedListeners = new Set();
  const schedState = () => ({ schedules: schedules.map((s) => ({ ...s })), background: schedules.some((s) => s.enabled), loginItemSupported: true });
  const pushSched = () => {
    const st = schedState();
    schedListeners.forEach((cb) => cb(st));
    return st;
  };
  const stubNext = (s) => {
    if (!s.enabled) return null;
    const [hh, mm] = s.frequency.time.split(':').map(Number);
    const f = s.frequency;
    return nextAt((c) => (f.kind === 'daily' ? true : f.kind === 'weekly' ? f.days.includes(c.getDay()) : c.getDate() === Math.min(f.day, 28)), hh, mm);
  };

  // ── collection areas ──
  const AREAS = [
    ['clusters', 'Clusters', 'Infrastructure', true],
    ['hosts', 'Hosts / nodes', 'Infrastructure', true],
    ['storage', 'Storage containers', 'Infrastructure', true],
    ['subnets', 'Subnets / networks', 'Infrastructure', true],
    ['host_networking', 'Host networking (virtual switches, NICs)', 'Infrastructure', false],
    ['vms', 'Virtual machines', 'Infrastructure', true],
    ['images', 'Images', 'Infrastructure', false],
    ['templates', 'VM templates', 'Infrastructure', false],
    ['ovas', 'OVAs', 'Infrastructure', false],
    ['dr', 'Data protection / DR', 'Platform services', false],
    ['flow', 'Flow network security', 'Platform services', false],
    ['fvn', 'Flow virtual networking', 'Platform services', false],
    ['files', 'Nutanix Files', 'Platform services', false],
    ['objects', 'Nutanix Objects', 'Platform services', false],
    ['vg', 'Volume groups', 'Platform services', false],
    ['lic', 'Licensing', 'Operations and governance', false],
    ['alerts', 'Health alerts', 'Operations and governance', false],
    ['iam', 'IAM (users, roles, projects)', 'Operations and governance', false],
    ['lcm', 'LCM firmware and software', 'Operations and governance', false],
    ['xplay', 'X-Play playbooks', 'Operations and governance', false],
    ['ncm', 'NCM reports', 'Operations and governance', false],
    ['pcconf', 'Prism Central configuration', 'Operations and governance', false],
  ].map(([id, label, group, pe]) => ({ id, label, group, pe_supported: pe, default_on: true }));

  // Run order and mid-run state from Collecting.dc.html.
  const RUN = [
    ['clusters', 'collected', 'v4', 4, 3, ''],
    ['hosts', 'collected', 'v4', 28, 5, ''],
    ['storage', 'collected', 'v4', 18, 2, ''],
    ['subnets', 'collected', 'v4', 42, 2, ''],
    ['host_networking', 'collected', 'v4', 6, 2, ''],
    ['vms', 'notes', 'v4', 1214, 72, '3 VMs skipped: the v4 endpoint returned an error for them'],
    ['lic', 'collected', 'v4', 8, 4, ''],
    ['dr', 'collected', 'v3', 18, 9, 'Recovery plans use v3, which retires Q4 2026'],
    ['alerts', 'collected', 'v3', 126, 6, 'v4 alerts endpoint not served, collected over v3'],
    ['flow', 'collected', 'v4', 24, 3, ''],
    ['fvn', 'skipped', null, null, 1, 'Not enabled on this Prism Central'],
    ['files', 'skipped', null, null, 1, 'Not deployed'],
    ['objects', 'running', 'v4', 1, 4, 'Reading object store buckets…'],
    ['vg', 'collected', 'v4', 27, 3, ''],
    ['iam', 'collected', 'v4', 46, 5, ''],
    ['xplay', 'collected', 'v3', 6, 2, ''],
    ['ncm', 'collected', 'v4', 19, 3, ''],
    ['lcm', 'collected', 'v4', 34, 6, ''],
    ['images', 'collected', 'v4', 22, 2, ''],
    ['templates', 'collected', 'v4', 18, 3, ''],
    ['ovas', 'collected', 'v4', 11, 2, ''],
    ['pcconf', 'collected', 'v4', 1, 1, ''],
  ];
  const label = (id) => AREAS.find((a) => a.id === id).label;
  const areaObj = (i, st) => {
    const [id, status, api, objects, secs, detail] = RUN[i];
    const s = st || status;
    return {
      id,
      label: label(id),
      index: i + 1,
      status: s,
      api: s === 'pending' ? null : api,
      objects: s === 'pending' || s === 'running' ? (s === 'running' ? objects : null) : objects,
      duration_ms: s === 'pending' ? null : secs * 1000,
      detail: s === 'pending' ? null : s === 'running' ? detail : detailOf(id, detail) || null,
    };
  };

  // ── long-text scenario (longNotes) ──
  const LONG_VER = 'el8.5-release-ganges-7.5.1.1-stable-0123456789abcdef0123456789abcdef01234567';
  const vmNames = Array.from({ length: 10 }, (_, i) => `app-tier-${String(i + 1).padStart(2, '0')}`).join(', ');
  const LONG = {
    vms: `46 VM(s) read from the groups API because the v4 API failed for them: ${vmNames} + 36 more; NICs and per-disk details unavailable; 136 VM(s) skipped: db-tier-01, db-tier-02, db-tier-03, web-frontend-blue-01, web-frontend-green-01 + 131 more`,
    flow: 'https://192.0.2.10:9440/api/microseg/v4.0/config/service-groups?%24page=5&%24limit=100: {"data":{"error":[{"message":"Failed to perform the operation as the backend service could not be reached","severity":"ERROR","code":"MICROSEG-30001"}]},"$objectType":"microseg.v4.error.ErrorResponse"}',
    dr: `Recovery plans use v3, which retires Q4 2026; availability zone pc-dr.example.lab reported version ${LONG_VER}`,
  };
  const detailOf = (id, d) => (opt.longNotes && LONG[id]) || d;

  const listeners = { progress: new Set(), finished: new Set() };
  const emit = (kind, e) => listeners[kind].forEach((cb) => cb(e));

  const finalAreas = () => RUN.map((r, i) => areaObj(i, r[1] === 'running' ? 'collected' : r[1]));

  // ── the optional LCM inventory (when the request switches it on) ──
  const UPGRADE = {
    operation: 'UPGRADE',
    description: 'Upgrade root task',
    percent: 35,
    started_at: new Date(Date.now() - 22 * 60000).toISOString(),
    task_id: 'ZXJnb24=:5b1c0e7a-2f4d-4c1b-9a1e-7d2c3b4a5f60',
    components: [
      { name: 'AOS', from_version: '7.0', to_version: '7.3' },
      { name: 'AHV hypervisor', from_version: '20230302.101026', to_version: '20230302.102001' },
    ],
  };
  const lcmTarget = (name, kind, status, extra = {}) => ({
    target_id: kind === 'cluster' ? `c-${name.toLowerCase()}` : null,
    name,
    kind,
    status,
    joined: false,
    task_id: status === 'skipped_busy' || status === 'forbidden' ? null : `ZXJnb24=:${name.length}a7c4e2-0b1d-4f6e-8a9c-${String(name.length).padStart(12, '0')}`,
    percent: status === 'succeeded' ? 100 : null,
    started_at: new Date(Date.now() - 150000).toISOString(),
    finished_at: null,
    duration_ms: status === 'succeeded' ? 262000 : null,
    detail: null,
    busy: null,
    ...extra,
  });
  const lcmInventory = (live) => ({
    requested: true,
    api: 'v4',
    timeout_s: 1800,
    started_at: new Date(Date.now() - 150000).toISOString(),
    finished_at: live ? null : new Date().toISOString(),
    duration_ms: live ? null : 271000,
    targets: [
      lcmTarget('Prism Central', 'prism_central', live ? 'running' : 'succeeded', { percent: live ? 62 : 100 }),
      lcmTarget('AMS-EDGE-02', 'cluster', 'succeeded'),
      lcmTarget('PHX-DR-01', 'cluster', 'skipped_busy', {
        busy: UPGRADE,
        detail: 'upgrade in progress (AOS 7.0 to 7.3, AHV hypervisor 20230302.101026 to 20230302.102001; 35%)',
      }),
      lcmTarget('PHX-PROD-01', 'cluster', live ? 'running' : 'succeeded', { percent: live ? 41 : 100 }),
      lcmTarget('PHX-PROD-02', 'cluster', live ? 'running' : 'succeeded', {
        joined: true,
        percent: live ? 88 : 100,
      }),
    ],
  });

  const result = (req) => ({
    summary: summary(4, 28, 1214),
    source_type: req.mode || 'prism_central',
    prism_central: { host: req.host, version: opt.longNotes ? LONG_VER : 'pc.2024.3', version_supported: true },
    warnings: opt.longNotes ? [`Flow network security: ${LONG.flow}`] : [],
    collection_notes: { vms: '3 VMs skipped', fvn: 'Not enabled', files: 'Not deployed' },
    snapshot_path: `${DIR}${sep}${req.name}.ntnxsnap.json`,
    snapshot_name: req.name,
    exports: {},
    export_errors: {},
    started_at: new Date(Date.now() - 252000).toISOString(),
    finished_at: new Date().toISOString(),
    duration_ms: 252000,
    areas: finalAreas().map((a) =>
      ['vms', 'fvn', 'files'].includes(a.id)
        ? a
        : opt.longNotes && LONG[a.id]
          ? { ...a, status: 'notes' }
          : { ...a, status: 'collected', detail: null },
    ),
    reports: (req.formats || []).filter((f) => f !== 'pdf').map((f) =>
      f === 'excel'
        ? { format: 'excel', path: `${EXPORT_DIR}${sep}${req.name}.xlsx`, name: `${req.name}.xlsx`, size_bytes: 2.4 * MB, detail: '34 sheets' }
        : { format: 'word', path: `${EXPORT_DIR}${sep}${req.name}-ADD.docx`, name: `${req.name}-ADD.docx`, size_bytes: 6.1 * MB, detail: '48 pages' },
    ),
    figures: [
      { label: 'Clusters', value: 4 },
      { label: 'Hosts', value: 28 },
      { label: 'VMs', value: 1214, qualifier: '3 skipped', qualifier_tone: 'warn' },
      { label: 'Storage containers', value: 18 },
      { label: 'Protection policies', value: 12 },
      { label: 'Flow policies', value: 24 },
    ],
    api_routing: [
      { areas: 'Clusters, hosts, storage, VMs, networks, Flow, LCM, licensing + 6 more', api: 'v4', reason: 'v4 SDK served by this Prism Central', after_sunset: { tone: 'ok', label: 'Unaffected' } },
      { areas: 'Health alerts', api: 'v3', reason: `v4 alerts endpoint not served on ${opt.longNotes ? LONG_VER : 'pc.2024.3'}`, after_sunset: { tone: 'ok', label: 'Moves to v4 after a PC upgrade' } },
      { areas: 'Recovery plans · availability zones', api: 'v3', reason: 'No v4 equivalent yet', after_sunset: { tone: 'warn', label: 'Stops collecting' } },
      { areas: 'X-Play playbooks · IAM projects', api: 'v3', reason: 'No v4 equivalent yet', after_sunset: { tone: 'warn', label: 'Stops collecting' } },
    ],
    compat: { version: opt.longNotes ? LONG_VER : 'pc.2024.3', supported: true, range: 'pc.2024.1 to pc.7.5.1.6' },
    scope: req.mode === 'prism_element' ? null : scopeOf(req.clusters || REGISTERED.map((c) => c.uuid)),
    lcm_inventory: req.lcmInventory ? lcmInventory(false) : { requested: false },
  });

  function runCollection(req, runId) {
    const started = new Date(Date.now() - 168000).toISOString();
    setTimeout(() => {
      emit('progress', {
        type: 'started',
        runId,
        started_at: started,
        mode: req.mode || 'prism_central',
        host: req.host,
        name: req.name,
        version: opt.longNotes ? LONG_VER : 'pc.2024.3',
        formats: req.formats || [],
        typical_seconds: req.lcmInventory ? 840 : 240,
        areas: RUN.map((_, i) => areaObj(i, 'pending')),
      });
      if (req.lcmInventory) emit('progress', { type: 'lcm_inventory', runId, inventory: lcmInventory(opt.run === 'hold') });
      const upto = opt.run === 'hold' ? RUN.findIndex((r) => r[1] === 'running') + 1 : RUN.length;
      for (let i = 0; i < upto; i += 1) {
        const st = opt.run === 'hold' ? RUN[i][1] : RUN[i][1] === 'running' ? 'collected' : RUN[i][1];
        emit('progress', { type: 'area', runId, area: areaObj(i, st), objects_total: 1483, clusters: 4 });
      }
      if (opt.run === 'finish') {
        setTimeout(() => emit('finished', { runId, outcome: 'completed', multi: false, result: result(req) }), 400);
      } else if (opt.run === 'fail') {
        setTimeout(() => emit('finished', { runId, outcome: 'failed', multi: false, error: 'Prism Central closed the connection (HTTP 503)' }), 400);
      }
    }, 60);
  }

  // ── inventory ──
  const VMS = [
    ['k8s-worker-07', 'PHX-PROD-02', 1, 16, 64, 500, 'Rocky Linux 9', 'None', '10.20.4.17', 'phx-prod-02-n3', '2 disks', 'Prod', 'Kubernetes', '6f1c…a93e'],
    ['vdi-gold-image', 'PHX-PROD-02', 0, 2, 8, 80, 'Windows 11 Enterprise', 'None', null, 'phx-prod-02-n1', '1 disk', 'Dev', 'VDI', '1b77…02cd'],
    ['backup-proxy-01', 'PHX-DR-01', 1, 4, 8, 60, 'Ubuntu 22.04', 'None', '10.30.0.12', 'phx-dr-01-n2', '1 disk', 'Prod', 'Backup', '93ad…7710'],
    ['jump-01', 'AMS-EDGE-02', 0, 2, 4, 40, 'Windows Server 2019', 'None', null, 'ams-edge-02-n1', '1 disk', 'Ops', 'Access', 'c402…e18b'],
    ['test-runner-12', 'PHX-PROD-02', 1, 8, 16, 200, 'Ubuntu 24.04', 'None', '10.20.4.62', 'phx-prod-02-n4', '2 disks', 'Dev', 'CI', '58e0…bb41'],
    ['grafana-02', 'PHX-PROD-01', 1, 2, 8, 100, 'Debian 12', 'None', '10.20.1.77', 'phx-prod-01-n2', '1 disk', 'Ops', 'Monitoring', '0c9d…4f12'],
    ['lab-dc-01', 'AMS-EDGE-02', 1, 2, 8, 80, 'Windows Server 2022', 'None', '10.40.0.10', 'ams-edge-02-n2', '1 disk', 'Lab', 'Directory', 'e7a5…9c03'],
    ['build-cache', 'PHX-PROD-02', 1, 4, 16, 1024, 'Rocky Linux 9', 'None', '10.20.4.90', 'phx-prod-02-n2', '3 disks', 'Dev', 'CI', '2df8…6a7e'],
    ['legacy-ftp', 'PHX-PROD-01', 0, 1, 2, 250, 'CentOS 7', 'None', null, 'phx-prod-01-n4', '2 disks', 'Prod', 'Legacy', 'aa31…d205'],
    ['sandbox-ml-03', 'PHX-PROD-02', 1, 12, 96, 800, 'Ubuntu 22.04', 'None', '10.20.4.33', 'phx-prod-02-n3', '2 disks', 'Dev', 'ML', '7be2…1c58'],
  ];
  const TYPE_COUNTS = [
    ['clusters', 'Clusters', 'Infrastructure', 4],
    ['hosts', 'Hosts', 'Infrastructure', 28],
    ['vms', 'Virtual machines', 'Infrastructure', 1214],
    ['storage_containers', 'Storage containers', 'Infrastructure', 18],
    ['subnets', 'Subnets', 'Infrastructure', 42],
    ['images', 'Images and templates', 'Infrastructure', 51],
    ['protection_policies', 'Protection policies', 'Protection and security', 12],
    ['recovery_plans', 'Recovery plans', 'Protection and security', 6],
    ['security_policies', 'Flow policies', 'Protection and security', 24],
    ['vpcs', 'VPCs', 'Protection and security', 3],
    ['object_stores', 'Object stores', 'Services', 1],
    ['volume_groups', 'Volume groups', 'Services', 27],
    ['file_servers', 'File servers', 'Services', null],
    ['users', 'Users and roles', 'Governance', 46],
    ['licenses', 'Licenses', 'Governance', 8],
    ['lcm_entities', 'LCM entities', 'Governance', 34],
    ['alerts', 'Alerts', 'Governance', 126],
  ].map(([type, l, group, count]) => ({ type, label: l, group, count }));

  const CLUSTERS = [
    ['PHX-PROD-01', 'AOS 7.0', 8, 342, 'NX-8155-G9'],
    ['PHX-PROD-02', 'AOS 7.0', 12, 612, 'NX-8155-G9'],
    ['PHX-DR-01', 'AOS 7.0', 6, 212, 'NX-3170-G8'],
    ['AMS-EDGE-02', 'AOS 6.10', 2, 48, 'NX-1175S-G8'],
  ];

  function queryInventory(path, type, filter, sort, page) {
    const text = (filter && filter.text ? filter.text : '').toLowerCase();
    const facets = (filter && filter.facets) || {};
    if (type === 'clusters') {
      let rows = CLUSTERS.map((c) => ({
        id: c[0],
        cells: { name: c[0], version: c[1], hosts: c[2], vms: c[3], model: c[4], health: { tone: 'ok', label: 'Healthy' } },
      }));
      if (text) rows = rows.filter((r) => r.cells.name.toLowerCase().includes(text));
      return {
        object_type: 'clusters',
        label: 'Clusters',
        unit_plural: 'clusters',
        facts: '4 clusters · 28 hosts',
        columns: [
          { key: 'name', label: 'Name', kind: 'name' },
          { key: 'version', label: 'Version', kind: 'mono' },
          { key: 'health', label: 'Health', kind: 'status' },
          { key: 'hosts', label: 'Hosts', kind: 'number' },
          { key: 'vms', label: 'VMs', kind: 'number' },
          { key: 'model', label: 'Model', kind: 'text' },
        ],
        rows,
        total: 4,
        matching: rows.length,
        offset: 0,
        limit: page.limit,
        type_counts: TYPE_COUNTS,
        facets: [],
      };
    }
    if (type !== 'vms') {
      const tc = TYPE_COUNTS.find((t) => t.type === type);
      return {
        object_type: type,
        label: tc ? tc.label : type,
        unit_plural: tc ? tc.label.toLowerCase() : type,
        facts: null,
        columns: [
          { key: 'name', label: 'Name', kind: 'name' },
          { key: 'cluster', label: 'Cluster', kind: 'text' },
        ],
        rows: tc && tc.count ? Array.from({ length: Math.min(tc.count, page.limit) }, (_, i) => ({ id: `${type}-${i}`, cells: { name: `${type.replace(/_/g, '-')}-${String(i + 1).padStart(2, '0')}`, cluster: CLUSTERS[i % 4][0] } })) : [],
        total: tc && tc.count ? tc.count : 0,
        matching: tc && tc.count ? tc.count : 0,
        offset: page.offset,
        limit: page.limit,
        type_counts: TYPE_COUNTS,
        facets: [],
      };
    }
    let rows = VMS.map((v) => ({
      id: v[13],
      cells: {
        name: v[0],
        cluster: v[1],
        power: v[2] ? { tone: 'ok', label: 'On' } : { tone: 'idle', label: 'Off' },
        vcpu: v[3],
        memory: v[4] * GB,
        storage: v[5] * GB,
        os: v[6],
        protection: v[7],
        ip: v[8],
      },
    }));
    if (text) rows = rows.filter((r) => [r.cells.name, r.cells.os, r.cells.ip || ''].join(' ').toLowerCase().includes(text));
    if (facets.cluster) rows = rows.filter((r) => r.cells.cluster === facets.cluster);
    if (facets.power) rows = rows.filter((r) => r.cells.power.label.toLowerCase() === facets.power);
    if (sort) {
      rows = rows.slice().sort((a, b) => {
        const x = a.cells[sort.key];
        const y = b.cells[sort.key];
        const xv = x && typeof x === 'object' ? x.label : x;
        const yv = y && typeof y === 'object' ? y.label : y;
        return (xv > yv ? 1 : xv < yv ? -1 : 0) * (sort.dir === 'asc' ? 1 : -1);
      });
    }
    const filtered = text || Object.keys(facets).length;
    const matching = filtered ? (facets.protection === 'none' && !text && Object.keys(facets).length === 1 ? 158 : rows.length) : 1214;
    return {
      object_type: 'vms',
      label: 'Virtual machines',
      unit_plural: 'VMs',
      facts: '1,214 VMs · 4 clusters',
      columns: [
        { key: 'name', label: 'Name', kind: 'name' },
        { key: 'cluster', label: 'Cluster', kind: 'text' },
        { key: 'power', label: 'Power', kind: 'status' },
        { key: 'vcpu', label: 'vCPU', kind: 'number' },
        { key: 'memory', label: 'Memory', kind: 'bytes' },
        { key: 'storage', label: 'Storage', kind: 'bytes' },
        { key: 'os', label: 'OS', kind: 'text' },
        { key: 'protection', label: 'Protection', kind: 'text' },
        { key: 'ip', label: 'IP', kind: 'mono' },
      ],
      rows: rows.slice(0, page.limit),
      total: 1214,
      matching,
      offset: page.offset,
      limit: page.limit,
      type_counts: TYPE_COUNTS,
      facets: [
        { key: 'cluster', label: 'Cluster', options: CLUSTERS.map((c) => ({ value: c[0], label: c[0], count: c[3] })) },
        { key: 'power', label: 'Power', options: [{ value: 'on', label: 'On', count: 1102 }, { value: 'off', label: 'Off', count: 112 }] },
        { key: 'protection', label: 'Protection', options: [{ value: 'gold', label: 'Gold-15min', count: 702 }, { value: 'silver', label: 'Silver-1h', count: 354 }, { value: 'none', label: 'None', count: 158 }] },
      ],
    };
  }

  function objectDetail(path, type, id) {
    const v = VMS.find((x) => x[13] === id);
    if (!v) {
      return { id, object_type: type, type_label: type === 'clusters' ? 'Cluster' : 'Object', name: id, status: null, context: null, fields: [{ label: 'Id', value: id, mono: true }], categories: [], notes: [], prism_url: null };
    }
    return {
      id,
      object_type: 'vms',
      type_label: 'VM',
      name: v[0],
      status: v[2] ? { tone: 'ok', label: 'Powered on' } : { tone: 'idle', label: 'Powered off' },
      context: v[1],
      fields: [
        { label: 'Host', value: v[9] },
        { label: 'Size', value: `${v[3]} vCPU · ${v[4]} GB`, mono: true },
        { label: 'Storage', value: `${v[5] >= 1024 ? (v[5] / 1024).toFixed(1) + ' TB' : v[5] + ' GB'} · ${v[10]}`, mono: true },
        { label: 'OS', value: v[6] },
        { label: 'IP', value: v[8], mono: true },
        { label: 'Protection', value: v[7] },
        { label: 'UUID', value: v[13], mono: true },
      ],
      categories: [`Env: ${v[11]}`, `App: ${v[12]}`],
      notes: v[7] === 'None' ? [{ tone: 'warn', text: 'Not in any protection policy or recovery plan.' }] : [],
      prism_url: 'https://pc01.example.com:9440/console/',
    };
  }

  const CHANGES = [
    ['changed', 'Cluster', 'PHX-PROD-01', 'pc01', 'AOS', '6.10.1', '7.0', null, 1],
    ['added', 'Host', 'phx-prod-02-n5 … n8', 'PHX-PROD-02', null, null, null, '4 nodes · NX-8155-G9 · 96 cores each', 4],
    ['changed', 'Protection policy', 'Gold-15min', 'pc01', 'RPO', '1 h', '15 min', null, 1],
    ['removed', 'Protection policy', 'Bronze-daily', 'pc01', null, null, null, '23 VMs it covered are now unprotected', 1],
    ['added', 'VM', 'k8s-worker-09 … 40', 'PHX-PROD-02', null, null, null, '32 VMs · 16 vCPU · 64 GB', 32],
    ['changed', 'VM', 'app-sql-01', 'PHX-PROD-01', 'memory', '32 GB', '64 GB', null, 1],
    ['removed', 'VM', 'legacy-app-01 … 06', 'PHX-PROD-01', null, null, null, '6 VMs · 1.4 TB freed', 6],
    ['added', 'Storage container', 'ctr-k8s-02', 'PHX-PROD-02', null, null, null, '12 TB · compression on', 1],
    ['changed', 'LCM entity', 'BIOS · phx-prod-01-n1 … n4', 'PHX-PROD-01', 'version', 'PB60.002', 'PB60.004', null, 4],
    ['added', 'Flow policy', 'isolate-pci', 'pc01', null, null, null, 'Isolation · enforced', 1],
  ].map(([kind, object_type, object, where, field, before, after, detail, count]) => ({ kind, object_type, object, where, field, before, after, detail, count }));

  const find = (p) => snapshots.find((s) => s.path === p);

  window.ntnx = {
    platform: opt.platform,
    ping: () => delay({ pong: true, version: '0.4.0' }),
    getAppInfo: () => delay({ version: '0.4.0', platform: opt.platform, defaultExportDir: EXPORT_DIR, demo: !!opt.demo }),
    getLegalDocuments: () =>
      delay({
        version: '0.4.0',
        license: 'MIT License\n\nCopyright (c) 2026 Alberto Chestaro\n\n(Stub: the real app shows the bundled LICENSE file.)',
        notice: 'NTNXVision\n(Stub: the real app shows the bundled NOTICE file.)',
        thirdPartyNotices: '# Third-party notices\n\n#### react 18.3.1\n\nLicense: **MIT**\n\n#### fpdf2 2.8.9\n\nLicense: **LGPL-3.0-only**',
        source: opt.platform === 'win32' ? 'C:\\Program Files\\NTNXVision\\resources' : '/Applications/NTNXVision.app/Contents/Resources',
      }),

    testConnection: (req) =>
      window.ntnx.testRaw(req).then((r) => {
        const t = req.targetId && connections.find((c) => c.id === req.targetId);
        if (t) t.lastTest = { status: r.status, ok: r.ok, message: r.message, version: r.version || null, at: new Date().toISOString() };
        return r;
      }),
    testRaw: (req) => {
      const host = req.host;
      const scenario = host.startsWith('pc03') && !req.password ? 'auth_failed' : opt.test;
      const base = { product: req.mode, name: host, supported_range: 'pc.2024.1 to pc.7.5.1.6', typical_seconds: req.mode === 'prism_element' ? 60 : 240 };
      if (scenario === 'auth_failed') return delay({ ...base, status: 'auth_failed', ok: false, message: 'HTTP 401 Unauthorized' }, 300);
      if (scenario === 'untrusted_cert')
        return delay(
          {
            ...base,
            status: 'untrusted_cert',
            ok: false,
            message: 'certificate verify failed: self-signed certificate',
            certificate: { subject: `CN=${host}`, issuer: `CN=${host}`, self_signed: true, fingerprint_sha256: '3A:9F:12:C4:7B:E0:55:91:0D:AA:6C:2E:F1:83:4B:77:19:C0:DE:52:8A:31:6F:0B:E9:44:27:D3:A8:1E:90:6C', not_after: '2027-03-02T00:00:00Z' },
          },
          300,
        );
      if (scenario === 'trust_required' || scenario === 'cert_changed') {
        const saved = req.targetId && connections.find((c) => c.id === req.targetId);
        const trusted = req.pinnedCert !== undefined ? !!req.pinnedCert : !!(saved && saved.pinnedCert);
        if (scenario === 'trust_required' && trusted) return delay({ ...base, status: 'ok', ok: true, version: 'pc.2024.3', version_supported: true, message: 'Connected to Prism Central pc.2024.3', detail: '1 cluster · v4 for 14 areas · within supported range', clusters: 1, hosts: 4, vms: 40, api: { v4: 14, v3: 5, v2: 0, not_deployed: 2 } }, 300);
        return delay(
          {
            ...base,
            status: scenario,
            ok: false,
            message: scenario === 'trust_required' ? 'This server certificate has not been trusted yet' : 'The server certificate changed since you trusted it',
            certificate: { subject: `CN=${host}`, issuer: `CN=${host}`, self_signed: true, fingerprint_sha256: '3A:9F:12:C4:7B:E0:55:91:0D:AA:6C:2E:F1:83:4B:77:19:C0:DE:52:8A:31:6F:0B:E9:44:27:D3:A8:1E:90:6C', not_after: '2027-03-02T00:00:00Z' },
          },
          300,
        );
      }
      if (scenario === 'unreachable') return delay({ ...base, status: 'unreachable', ok: false, message: 'Connection timed out after 10 s (no route to host)' }, 300);
      if (scenario === 'version_unsupported')
        return delay({ ...base, status: 'version_unsupported', ok: true, version: 'pc.2023.4', version_supported: false, message: 'Connected to Prism Central pc.2023.4', detail: '3 clusters · v3 for 19 areas · outside supported range' }, 300);
      const pe = req.mode === 'prism_element';
      const ver = host.startsWith('pc02') ? 'pc.7.3' : host.startsWith('pc03') ? 'pc.2024.1' : 'pc.2024.3';
      return delay(
        {
          ...base,
          status: 'ok',
          ok: true,
          version: pe ? 'AOS 7.0' : ver,
          version_supported: true,
          message: pe ? 'Connected to phx-dr-01 · AOS 7.0' : `Connected to Prism Central ${ver}`,
          detail: pe ? '6 hosts · 212 VMs · v2.0 API · within supported range' : '4 clusters · v4 for 14 areas, v3 for 5, 2 not deployed · within supported range',
          clusters: 4,
          hosts: 28,
          vms: 1214,
          api: { v4: 14, v3: 5, v2: 0, not_deployed: 2 },
        },
        300,
      );
    },
    listClusters: (req) => {
      if (opt.clusters === 'loading') return new Promise(() => {});
      if (opt.clusters === 'error') return fail('Could not reach Prism Central at pc01.example.com:9440 (connection timed out)', 300);
      const pe = req.mode === 'prism_element';
      const single = pe || req.host.startsWith('pc-lab');
      const clusters = pe
        ? [{ uuid: 'c-phx-dr-01', name: 'phx-dr-01', aos_version: '7.0', num_nodes: 6, hypervisors: ['AHV'] }]
        : single
          ? [{ uuid: 'c-lab-01', name: 'LAB-01', aos_version: '7.0', num_nodes: 3, hypervisors: ['AHV'] }]
          : REGISTERED;
      return delay({ mode: req.mode, host: req.host, api: pe ? 'v2' : 'v4', version: pe ? '7.0' : 'pc.2024.3', clusters }, 300);
    },
    getCollectionAreas: () => delay({ areas: AREAS, typical_seconds: { prism_central: 240, prism_element: 60 }, lcm_inventory_typical_seconds: 600 }),
    startCollection: (req) => {
      const runId = 'run-' + Math.random().toString(36).slice(2, 8);
      runCollection(req, runId);
      return delay({ runId }, 20);
    },
    startMultiCollection: (req) => {
      const runId = 'run-' + Math.random().toString(36).slice(2, 8);
      runCollection({ ...req, host: req.targets[0].host, name: req.combinedName }, runId);
      return delay({ runId }, 20);
    },
    cancelRun: (runId) => {
      setTimeout(() => emit('finished', { runId, outcome: 'cancelled', multi: false, error: 'Run cancelled' }), 200);
      return delay({ cancelled: true });
    },
    onProgress: (cb) => (listeners.progress.add(cb), () => listeners.progress.delete(cb)),
    onRunFinished: (cb) => (listeners.finished.add(cb), () => listeners.finished.delete(cb)),

    getOverview: () =>
      delay(() =>
        snapshots.length
          ? {
              latest: snapshots[0],
              figures: [
                { label: 'Clusters', value: 4, qualifier: 'AOS 6.10 – 7.0' },
                { label: 'Hosts', value: 28, qualifier: '+4 since Sep 30' },
                { label: 'VMs', value: 1214, qualifier: '1,102 powered on' },
                { label: 'Storage', value: '186', unit: 'TB', qualifier: '41% used' },
                { label: 'Protected VMs', value: '87%', qualifier: '158 unprotected', qualifier_tone: 'warn' },
                { label: 'Run notes', value: 3, qualifier: '2 skipped · 1 with notes · 0 errors' },
              ],
              recent: [snapshots[0], snapshots[1], snapshots[4], snapshots[5]].filter(Boolean),
              coverage: { collected: 18, with_notes: 1, skipped: 2, failed: 0, total: 21 },
              api_routing: { v4: 14, v3: 5, v2: 0 },
              sunset_areas: ['X-Play', 'recovery plans', 'availability zones', 'projects'],
              compare_baseline: snapshots[5] ? snapshots[5].path : null,
            }
          : { latest: null, figures: [], recent: [], coverage: null, api_routing: null, sunset_areas: [], compare_baseline: null },
      ),
    listSnapshots: () => delay(() => ({ snapshots: snapshots.slice(), dir: DIR, total_bytes: snapshots.reduce((s, x) => s + x.size_bytes, 0) })),
    getSnapshot: (p) => {
      const s = find(p);
      if (!s) return fail('Snapshot not found');
      return delay({
        ...s,
        source_type: s.kind === 'pe' ? 'prism_element' : 'prism_central',
        prism_central: { host: s.source, version: s.version },
        is_combined: s.kind === 'combined',
        sources: s.kind === 'combined' ? ['pc01.example.com', 'pc02.example.com', 'pc03.example.com'] : [],
        collection_notes: {},
        export_estimates: { excel: { bytes: 2.4 * MB, detail: '34 sheets' } },
        run:
          s === snapshots[0]
            ? (({ started_at, finished_at, duration_ms, areas, figures, api_routing, compat, reports, warnings, scope }) => ({ started_at, finished_at, duration_ms, areas, figures, api_routing, compat, reports, warnings, scope }))(
                result({ name: s.name, host: s.source, formats: ['excel', 'word'] }),
              )
            : null,
      });
    },
    queryInventory: (p, t, f, s, pg) => (find(p) ? delay(() => queryInventory(p, t, f, s, pg)) : fail('Snapshot not found')),
    getObjectDetail: (p, t, id) => delay(() => objectDetail(p, t, id)),
    compareSnapshots: (b, c) => {
      const base = find(b);
      const cur = find(c);
      if (!base || !cur) return fail('Snapshot not found');
      return delay({
        baseline: base,
        current: cur,
        days: Math.max(0, (Date.parse(cur.collected_at) - Date.parse(base.collected_at)) / 86400000),
        summary: { total: 67, added: 50, removed: 7, changed: 10 },
        figures: [
          { label: 'Changes', value: 67, qualifier: '50 added · 7 removed · 10 changed' },
          { label: 'VMs', value: 1214, qualifier: '+44 / −6 · was 1,176' },
          { label: 'Hosts', value: 28, qualifier: '+4 · was 24' },
          { label: 'Storage', value: '186', unit: 'TB', qualifier: '+12 TB · was 174 TB' },
          { label: 'Protection policies', value: 12, qualifier: '−1 · was 13' },
        ],
        changes: CHANGES,
        scope_notice: opt.scopeNotice
          ? {
              title: 'Different cluster scopes',
              baseline: 'All 4 clusters',
              current: '2 of 4 clusters (PHX-PROD-01, PHX-PROD-02)',
              clusters: ['AMS-EDGE-02', 'PHX-DR-01'],
              out_of_scope: 301,
              detail: 'Objects on AMS-EDGE-02, PHX-DR-01, which only one snapshot collected, are left out of the comparison and its totals instead of being reported as added or removed (301 objects).',
            }
          : null,
      });
    },
    exportComparison: () => delay({ path: `${EXPORT_DIR}${sep}changes.xlsx` }, 300),
    // Diagrams: fixtures from scripts/stub-diagrams.json (window.__STUB_DIAGRAMS__, the
    // synthetic "medium" estate laid out by the real engine). opt.diagrams picks the state:
    // 'generated' (default) | 'edited' | 'stale' | 'carry'. Any edit lays out as 'edited'.
    listDiagrams: (p) => {
      const D = window.__STUB_DIAGRAMS__;
      if (!find(p) || !D) return fail('Snapshot not found');
      const mode = opt.diagrams || 'generated';
      const edited = mode === 'edited' || mode === 'stale';
      const prev = snapshots.find((s) => s.path !== p && s.source === find(p).source) || snapshots[1];
      return delay({
        snapshot: p,
        diagrams: [{ ...D.summary, edited, edits: edited ? D.edited.ops.length : 0, saved_at: edited ? D.edited.saved_at : null, stale: mode === 'stale' }],
        carry: mode === 'carry' && prev ? { snapshot: prev, diagrams: D.carry } : null,
      });
    },
    getDiagram: () => {
      const D = window.__STUB_DIAGRAMS__;
      const mode = opt.diagrams || 'generated';
      return delay(mode === 'stale' ? D.stale : mode === 'edited' ? D.edited : D.generated);
    },
    layoutDiagram: (_p, _t, ops) => {
      const D = window.__STUB_DIAGRAMS__;
      const src = ops.length ? D.edited : D.generated;
      return delay({ type: src.type, model: src.model, scenes: src.scenes, outline: src.outline, connectors: src.connectors || [], problems: [], notes: src.notes || [] }, 20);
    },
    saveDiagram: (_p, _t, ops) => {
      const D = window.__STUB_DIAGRAMS__;
      return delay(ops.length ? { ...D.edited, ops, saved_at: new Date().toISOString() } : D.generated, 120);
    },
    resetDiagram: () => delay(window.__STUB_DIAGRAMS__.generated, 120),
    carryDiagrams: () => delay({ diagrams: window.__STUB_DIAGRAMS__.carry }, 200),
    exportDiagram: (req) => {
      const s = find(req.snapshotPath);
      const name = `${s ? s.name : 'snapshot'}-2026-10-20_solution-conceptual-overview.${req.format}`;
      return delay({ path: `${EXPORT_DIR}${sep}${name}`, paths: [`${EXPORT_DIR}${sep}${name}`], name, detail: '1 file' }, 200);
    },
    // What lands in the export folder (Export dialog): synthetic names and sizes.
    previewExport: (req) => {
      const s = find(req.snapshotPath);
      const stem = `${req.filePrefix ? `${req.filePrefix}-` : ''}${s ? s.name : 'snapshot'}-2026-10-20`;
      const KIND = { excel: ['Excel', 'xlsx', 1.1], pdf: ['PDF', 'pdf', 3.1], word: ['Word ADD', 'docx', 2.4] };
      // A report's size is known after its first export (pc01 has exported a PDF before).
      const files = req.formats.map((f) => ({ format: f, name: `${stem}${f === 'excel' ? '-2' : ''}.${KIND[f][1]}`, kind: KIND[f][0], size_bytes: f === 'pdf' ? KIND[f][2] * MB : null, approx: f === 'pdf', detail: null }));
      const docs = req.formats.includes('pdf') || req.formats.includes('word');
      const figs = ['solution-conceptual-overview', 'logical-design-pc01', 'logical-design-pc02', 'management-plane', 'host-network-profile-a', 'data-protection'];
      const mode = opt.diagrams || 'generated';
      const tagged = (i) => (i === 0 && (mode === 'edited' || mode === 'stale') ? (mode === 'stale' ? ['edited', 'stale'] : ['edited']) : []);
      const svg = figs.map((f, i) => ({ name: `0${i + 1}-${f}.svg`, kind: 'SVG', size_bytes: (9 + i * 2) * 1024, approx: false, tags: tagged(i) }));
      const png = figs.map((f, i) => ({ name: `0${i + 1}-${f}.png`, kind: 'PNG', size_bytes: (180 + i * 30) * 1024, approx: false, tags: tagged(i) }));
      const folder = req.diagramsPackage === 'folder';
      const diagrams =
        req.diagrams && docs
          ? {
              name: `${stem}_diagrams${folder ? '' : '.zip'}`,
              package: folder ? 'folder' : 'zip',
              count: figs.length,
              size_bytes: 1.6 * MB,
              approx: true,
              edited: mode === 'generated' ? [] : ['conceptual'],
              stale: mode === 'stale' ? [{ type: 'conceptual', title: 'Solution conceptual overview' }] : [],
              entries: [
                { name: `${stem}_diagrams.vsdx`, kind: 'Visio', detail: `${figs.length} pages`, size_bytes: 212 * 1024, approx: false },
                { name: `${stem}_diagrams.drawio`, kind: 'draw.io', detail: `${figs.length} tabs`, size_bytes: 96 * 1024, approx: false },
                { name: 'svg/', kind: 'SVG', detail: 'Live text', folder: true, size_bytes: 80 * 1024, approx: false, children: svg },
                { name: 'png/', kind: 'PNG', detail: '200 dpi', folder: true, size_bytes: 1.4 * MB, approx: false, children: png },
              ],
            }
          : null;
      return delay({ files, diagrams, stem }, 120);
    },
    setUnsavedChanges: () => delay(undefined, 0),
    getReportSections: (p) =>
      find(p)
        ? delay({ sections: REPORT_SECTIONS.map((id) => ({ id, available: !NO_DATA.has(id), hint: NO_DATA.has(id) ? 'No data in this snapshot' : null })) })
        : fail('Snapshot not found'),
    exportSnapshot: (req) => {
      const s = find(req.snapshotPath);
      const name = s ? s.name : 'snapshot';
      const paths = {};
      req.formats.forEach((f) => (paths[f] = `${EXPORT_DIR}${sep}${name}.${f === 'excel' ? 'xlsx' : f === 'word' ? 'docx' : 'pdf'}`));
      const diagrams = req.diagrams
        ? { path: `${EXPORT_DIR}${sep}${name}_diagrams.zip`, name: `${name}_diagrams.zip`, size_bytes: 0.2 * MB, detail: '6 diagrams' }
        : null;
      return delay({ paths, errors: {}, path: Object.values(paths)[0] || null, files: [], diagrams }, 400);
    },
    renameSnapshot: (p, n) => {
      const s = find(p);
      if (!s) return fail('Snapshot not found');
      s.name = n;
      s.path = `${DIR}${sep}${n}.ntnxsnap.json`;
      return delay({ path: s.path, name: n });
    },
    deleteSnapshot: (p) => {
      snapshots = snapshots.filter((s) => s.path !== p);
      return delay({ deleted: true });
    },
    clearSnapshots: () => {
      const n = snapshots.length;
      snapshots = [];
      return delay({ deleted: n });
    },

    exportSnapshotFile: (req) => {
      const s = find(Array.isArray(req.snapshotPath) ? req.snapshotPath[0] : req.snapshotPath);
      return delay({ path: req.destPath, name: req.destPath.split(sep).pop(), size_bytes: 0.4 * MB, encrypted: !!req.password, snapshots: [{ snapshot_id: 'stub', name: s ? s.name : null }] }, 300);
    },
    exportSnapshotFiles: (req) =>
      delay({ dir: req.destDir, files: req.snapshotPaths.map((p) => ({ path: `${req.destDir}${sep}${(find(p) || {}).name}.ntnxsnap`, name: `${(find(p) || {}).name}.ntnxsnap`, size_bytes: 0.4 * MB, encrypted: !!req.password, snapshots: [] })), errors: [] }, 300),
    inspectSnapshotFile: (p, password) =>
      delay({
        path: p,
        file_name: p.split(sep).pop(),
        size_bytes: 0.4 * MB,
        format_version: 1,
        schema_version: 1,
        app_version: '0.4.0',
        exported_at: snapshots[0] ? snapshots[0].collected_at : null,
        exporter: { app_version: '0.4.0', platform: 'windows', note: 'Alberto Chestaro' },
        encrypted: false,
        locked: false,
        password_error: password ? null : null,
        snapshots: snapshots.slice(0, 1).map((s) => ({ snapshot_id: 'stub', name: s.name, collected_at: s.collected_at, kind: s.kind, source: s.source, version: s.version, sources: [], summary: s.summary, size_bytes: s.size_bytes, conflict: { name: s.name, path: s.path }, import_name: `${s.name}-2` })),
        conflicts: snapshots.length ? 1 : 0,
      }),
    importSnapshotFile: () => delay({ imported: [], count: 0, skipped: 0 }, 300),
    revealPath: () => delay(undefined),
    openPath: () => delay({ error: null }),
    openExternal: () => delay(undefined),
    chooseDirectory: () => delay(EXPORT_DIR),
    chooseFile: () => delay(null),
    chooseSnapshotSavePath: (name) => delay(`${EXPORT_DIR}${sep}${name}.ntnxsnap`),
    chooseSnapshotFiles: () => delay([]),
    pathForFile: () => '',
    takePendingSnapshotFiles: () => delay([]),
    onOpenSnapshotFiles: () => () => {},

    listConnections: () => delay(() => connections.slice()),
    saveTarget: (req) => {
      const errors = {};
      if (!req.name.trim()) errors.name = 'Enter a name.';
      else if (connections.some((c) => c.id !== req.id && c.name.toLowerCase() === req.name.trim().toLowerCase())) errors.name = 'Another target already uses this name.';
      if (!req.host.trim()) errors.host = 'Enter an address.';
      if (Object.keys(errors).length) return delay({ ok: false, errors, targets: connections.slice() });
      const id = req.id || 't-' + Math.random().toString(36).slice(2, 10);
      const prev = connections.find((c) => c.id === id);
      const { password, ndbPassword, ...rest } = req;
      const target = {
        ...rest,
        id,
        ndbHost: req.ndbHost || undefined,
        hasPassword: !!password || (prev ? prev.hasPassword : false),
        ndbHasPassword: !!req.ndbHost && (!!ndbPassword || (prev ? !!prev.ndbHasPassword : false)),
        lastTest: prev ? prev.lastTest : null,
      };
      connections = prev ? connections.map((c) => (c.id === id ? target : c)) : [target, ...connections];
      return delay(() => ({ ok: true, target, targets: connections.slice() }));
    },
    trustCertificate: (req) => {
      connections = connections.map((c) => (c.id === req.targetId ? { ...c, [req.endpoint === 'ndb' ? 'ndbPinnedCert' : req.endpoint === 'vcenter' ? 'vcenterPinnedCert' : 'pinnedCert']: req.fingerprint } : c));
      return delay(() => connections.slice());
    },
    deleteTarget: (id) => {
      connections = connections.filter((c) => c.id !== id);
      return delay(() => connections.slice());
    },
    getSmtpSettings: () => delay(() => ({ ...smtp })),
    saveSmtpSettings: (req) => {
      const errors = {};
      if (req.host && !req.fromAddress) errors.fromAddress = 'Enter the address mail is sent from.';
      if (Object.keys(errors).length) return delay({ ok: false, errors, settings: { ...smtp } });
      const { password, clearPassword, ...rest } = req;
      smtp = { ...rest, hasPassword: clearPassword ? false : !!password || smtp.hasPassword, configured: !!(rest.host && rest.fromAddress) };
      return delay(() => ({ ok: true, settings: { ...smtp } }));
    },
    trustSmtpCertificate: (req) => {
      const same = req.host === smtp.host && req.port === smtp.port && req.security === smtp.security && smtp.allowUntrusted;
      if (same) smtp = { ...smtp, pinnedSha256: req.fingerprint.replace(/:/g, '').toLowerCase() };
      return delay(() => ({ ok: same, settings: { ...smtp } }));
    },
    testSmtp: (req) =>
      delay(() => (!opt.smtpTest || opt.smtpTest === 'ok'
        ? { ok: true, status: 'ok', message: `Sent to ${req.to}`, detail: `${req.host}:${req.port} · ${req.security === 'ssl' ? 'SSL/TLS' : req.security === 'starttls' ? 'STARTTLS' : 'no encryption'} · 250 2.0.0 OK queued`, recipients: [req.to] }
        : emailFail(opt.smtpTest)), 400),
    emailSnapshot: (req) =>
      delay(() => {
        const name = req.snapshotPaths[0].split(/[\\/]/).pop().replace(/\.ntnxsnap.*$/, '');
        const ext = { excel: 'xlsx', pdf: 'pdf', word: 'docx' };
        const files = req.formats.map((f) => ({ format: f, name: `${name}-2026-10-17.${ext[f]}`, path: `${EXPORT_DIR}${sep}${name}-2026-10-17.${ext[f]}`, size_bytes: 2.4 * MB }));
        if (opt.email && opt.email !== 'ok') return emailFail(opt.email, { files });
        return { ok: true, status: 'ok', message: `Sent to ${req.to.length + req.cc.length} recipients`, recipients: [...req.to, ...req.cc], attachments: files.map((f) => ({ name: f.name, size_bytes: f.size_bytes })), files };
      }, 600),
    clearSmtpSettings: () => {
      smtp = { ...smtp, host: '', fromAddress: '', username: '', pinnedSha256: '', hasPassword: false, configured: false };
      const off = schedules.filter((x) => x.enabled).map((x) => x.name);
      schedules = schedules.map((x) => ({ ...x, enabled: false, nextRun: null }));
      pushSched();
      return delay(() => ({ settings: { ...smtp }, disabledSchedules: off }));
    },
    listSchedules: () => delay(schedState),
    saveSchedule: (req) => {
      const errors = {};
      if (schedules.some((x) => x.id !== req.id && x.name.toLowerCase() === req.name.trim().toLowerCase())) errors.name = 'Another schedule already uses this name.';
      if (Object.keys(errors).length) return delay({ ok: false, errors, schedules: schedState().schedules });
      const now = new Date().toISOString();
      const prev = schedules.find((x) => x.id === req.id);
      const id = req.id || 's-' + Math.random().toString(36).slice(2, 10);
      const s = { ...(prev ?? { createdAt: now, history: [], lastRun: null, state: 'idle', problems: [] }), ...req, id, updatedAt: now, anchor: now };
      s.nextRun = stubNext(s);
      schedules = prev ? schedules.map((x) => (x.id === id ? s : x)) : [...schedules, s];
      const st = pushSched();
      return delay(() => ({ ok: true, schedule: { ...s }, schedules: st.schedules }));
    },
    deleteSchedule: (id) => {
      schedules = schedules.filter((x) => x.id !== id);
      return delay(pushSched);
    },
    setScheduleEnabled: (id, enabled) => {
      schedules = schedules.map((x) => (x.id === id ? { ...x, enabled, nextRun: stubNext({ ...x, enabled }) } : x));
      return delay(pushSched);
    },
    runScheduleNow: (id) => {
      const s = schedules.find((x) => x.id === id);
      if (!s) return fail('That schedule no longer exists.');
      if (s.state !== 'idle') return delay(() => ({ ...schedState(), queued: false }));
      s.state = 'running';
      s.lastRun = { id: 'r-now', trigger: 'manual', status: 'running', stage: 'collect', startedAt: new Date().toISOString(), message: 'Collecting…' };
      return delay(() => ({ ...pushSched(), queued: true }));
    },
    onSchedulesChanged: (cb) => (schedListeners.add(cb), () => schedListeners.delete(cb)),
    getSettings: () => delay(() => ({ ...settings })),
    setSettings: (s) => {
      settings = { ...settings, ...s };
      return delay(() => ({ ...settings }), 10);
    },
  };

  // ── Insights > Optimization (getOptimization) ──
  // A sample of what the sidecar's `optimization` returns for the demo pc01 estate.
  // opt.optimization: 'findings' (default) | 'clean' (every VM fits its hosts) | 'cards' (a
  // placeholder second review, so the page shows its card grid instead of opening the review).
  (function () {
    const host = (name, sockets, perNode, memGb, basis = 'current') => ({
      uuid: `h-${name}`, name, sockets, cores: sockets * perNode, cores_per_node: perNode, threads_per_core: 2,
      memory_bytes: memGb * GB, memory_per_node_bytes: (memGb / sockets) * GB, numa_nodes: sockets, basis,
    });
    const lay = (sockets, cores, memGb, vnuma, threads = 1) => ({
      sockets, cores_per_socket: cores, threads_per_core: threads, vcpus: sockets * cores * threads, memory_bytes: memGb * GB, vnuma_nodes: vnuma,
    });
    const R = {
      exceed: (v, c) => ['vcpus_exceed_cores', 'high', 'More vCPUs than physical cores', `${v} vCPUs on a host with ${c} physical cores. The vCPUs wait for each other to be scheduled; keep it at ${c} or fewer.`],
      span: (v, c, n) => ['numa_span_cpu', 'high', 'Spans NUMA nodes, guest sees one', `${v} vCPUs do not fit in one NUMA node (${c} cores), so AHV spreads them over ${n} nodes while the guest sees a single node. Turn on vNUMA with ${n} nodes so the guest places its work and memory on the right node.`],
      mem: (m, p) => ['numa_span_memory', 'medium', 'Memory spans NUMA nodes', `${m} GiB of memory is more than one NUMA node holds (${p} GiB of memory), so part of it is remote to the vCPUs. Turn on vNUMA with 2 nodes so the guest places its work and memory on the right node.`],
      uneven: (v, t) => ['uneven_split', 'medium', 'vCPUs don’t split evenly', `${v} vCPUs over 2 NUMA nodes leaves the nodes unequal. ${t} vCPUs split evenly (${t / 2} per node).`],
      straddle: (s) => ['socket_straddles_vnuma', 'medium', 'A socket straddles vNUMA nodes', `${s} sockets cannot be shared evenly over 2 vNUMA nodes, so the guest sees a socket split across nodes. Use 2 sockets.`],
      mismatch: () => ['vnuma_mismatch', 'medium', 'vNUMA nodes don’t match the host', 'vNUMA is set to 3 nodes but the VM needs 2 of this host’s 2 NUMA nodes. Set it to 2.'],
      notNeeded: () => ['vnuma_not_needed', 'low', 'vNUMA not needed', 'The VM fits in one NUMA node of this host, yet vNUMA shows the guest 2 nodes and splits its memory between them. Turn vNUMA off.'],
      guest: () => ['guest_socket_limit', 'high', 'Guest can’t use all its sockets', 'Windows 11 Enterprise uses at most 2 CPU sockets, so the vCPUs on the other 2 sit unused. Give it fewer sockets with more cores each.'],
      smaller: (h, s) => ['smaller_host', 'medium', 'Doesn’t fit the cluster’s smallest host', `It takes one NUMA node on ${h}, but the cluster also has smaller hosts such as ${s} (32 cores, 256 GB per NUMA node). Moved there by HA or ADS, it would span 2 NUMA nodes.`],
    };
    const f = (vm, cluster, power, hv, cur, rec, h, reasons, extra = {}) => {
      const rs = reasons.map(([rule, severity, title, text]) => ({ rule, severity, title, text }));
      const rank = { high: 3, medium: 2, low: 1 };
      const top = rs.reduce((a, b) => (rank[b.severity] > rank[a.severity] ? b : a));
      return {
        id: `vm-${vm}`, vm, cluster, cluster_uuid: `c-${cluster.toLowerCase()}`, power_state: power, hypervisor: hv, guest_os: extra.guest_os ?? null,
        source_pc: null, severity: top.severity, finding: top.title, reasons: rs, current: cur, recommended: rec,
        sized_for: extra.sized_for ?? h.name, host: h, mixed_cluster: !!extra.mixed,
      };
    };
    const P1 = host('phx-prod-01-n6', 2, 32, 1024);
    const P2 = host('phx-prod-02-n10', 2, 48, 1024);
    const DR = host('phx-dr-01-n5', 2, 24, 512);
    const DRS = host('phx-dr-01-n1', 2, 24, 512, 'smallest');
    const AMS = { ...host('ams-edge-02-n2', 1, 32, 512), threads_per_core: 2 };
    const FINDINGS = [
      f('web-235', 'AMS-EDGE-02', 'ON', 'ESXi', { ...lay(40, 1, 8, null) }, { ...lay(1, 32, 8, null) }, AMS, [R.exceed(40, 32)]),
      f('db-01', 'PHX-PROD-01', 'ON', 'AHV', lay(40, 1, 128, 0), lay(2, 20, 128, 2), P1, [R.span(40, 32, 2)]),
      f('ml-01', 'PHX-PROD-01', 'ON', 'AHV', lay(80, 1, 96, 0), lay(2, 32, 96, 2), P1, [R.exceed(80, 64), R.span(64, 32, 2)]),
      f('vdi-01', 'PHX-PROD-01', 'ON', 'AHV', lay(4, 1, 8, 0), lay(1, 4, 8, 0), P1, [R.guest()], { guest_os: 'Windows 11 Enterprise' }),
      f('db-59', 'PHX-DR-01', 'ON', 'AHV', lay(32, 1, 128, 0), lay(2, 16, 128, 2), DR, [R.span(32, 24, 2)]),
      f('app-sql-41', 'PHX-DR-01', 'OFF', 'AHV', lay(8, 1, 320, 0), lay(2, 4, 320, 2), DRS, [R.mem(320, 256)]),
      f('k8s-worker-03', 'PHX-PROD-01', 'ON', 'AHV', lay(33, 1, 64, 2), lay(2, 17, 64, 2), P1, [R.straddle(33), R.uneven(33, 34)]),
      f('test-runner-02', 'PHX-PROD-01', 'ON', 'AHV', lay(3, 16, 16, 3), lay(2, 24, 16, 2), P1, [R.mismatch()]),
      f('app-57', 'PHX-PROD-02', 'ON', 'AHV', lay(1, 36, 16, 0), lay(2, 18, 16, 2), P2, [R.smaller('phx-prod-02-n10', 'phx-prod-02-n1')], { mixed: true, sized_for: 'phx-prod-02-n1' }),
      f('test-runner-01', 'PHX-PROD-01', 'ON', 'AHV', lay(8, 1, 16, 2), lay(8, 1, 16, 0), P1, [R.notNeeded()]),
      f('test-runner-79', 'PHX-DR-01', 'ON', 'AHV', lay(8, 1, 16, 2), lay(8, 1, 16, 0), DR, [R.notNeeded()]),
    ];
    const reviewOf = (findings) => {
      const count = (s) => findings.filter((x) => x.severity === s).length;
      const summary = { reviewed: 1214, to_optimize: findings.length, clean: 1214 - findings.length, high: count('high'), medium: count('medium'), low: count('low'), not_checked: 0, powered_off: 99 };
      const n = findings.length;
      return {
        reviews: [{
          id: 'vm_compute', title: 'VM compute', description: 'vCPU and memory layouts that don’t fit their hosts’ NUMA layout.',
          headline: n ? `${n} VM${n === 1 ? '' : 's'} to review` : 'Nothing to review', count: n,
          severity: summary.high ? 'high' : summary.medium ? 'medium' : summary.low ? 'low' : null, reviewed: summary.reviewed,
        }],
        vm_compute: {
          summary,
          findings,
          not_checked: [],
          assumptions: ['One NUMA node per CPU socket (Nutanix APIs report no NUMA node count).', 'Powered-off VMs are checked against the smallest host in their cluster.'],
        },
      };
    };
    const withSecond = (r) => ({
      ...r,
      reviews: [...r.reviews, {
        id: 'sample', title: 'Sample review', description: 'A placeholder second review for the card grid.',
        headline: 'Nothing to review', count: 0, severity: null, reviewed: r.vm_compute.summary.reviewed,
      }],
    });
    window.ntnx.getOptimization = (p) => {
      if (!snapshots.some((s) => s.path === p)) return fail('Snapshot not found');
      const r = reviewOf(opt.optimization === 'clean' ? [] : FINDINGS);
      return delay(() => (opt.optimization === 'cards' ? withSecond(r) : r));
    };
  })();

  // ── Insights > Metrics (getMetrics / cancelMetrics) ──────────────────
  // Options: metrics: 'ok' (default) | 'loading' (never answers) | 'error' | 'trendsLoading'
  // (cards answer, trends never) | 'trendsError' | 'partial' (a source failed: notes).
  // Three clusters: PHX-PROD-02 grows in storage (80% in about six weeks), PHX-PROD-01 in
  // memory, AMS-EDGE-02 is flat and healthy. Shapes as in docs/IPC_CONTRACT.md "metrics".
  {
    const DAY = 86_400_000;
    const TB = 1024 ** 4;
    const mopt = opt.metrics || 'ok';
    const unit = (s) => {
      let h = 2166136261;
      for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
      return ((h >>> 0) % 10000) / 10000;
    };
    const MC = [
      { uuid: 'c-phx-prod-01', name: 'PHX-PROD-01', hv: 'AHV', nodes: 8, aos: '6.10.1', cpu: [34, 0], mem: [63, 0.11], sto: [48, 0.02], cap: 184 * TB, cores: 384, memB: 6 * TB, crit: 2, warn: 9 },
      { uuid: 'c-phx-prod-02', name: 'PHX-PROD-02', hv: 'AHV', nodes: 12, aos: '7.0', cpu: [41, 0.03], mem: [49, 0], sto: [69.5, 0.26], cap: 276 * TB, cores: 576, memB: 9 * TB, crit: 1, warn: 6 },
      { uuid: 'c-ams-edge-02', name: 'AMS-EDGE-02', hv: 'ESXi', nodes: 2, aos: '6.10', cpu: [26, 0], mem: [52, 0], sto: [37, 0], cap: 46 * TB, cores: 96, memB: 1.5 * TB, crit: 0, warn: 0 },
    ];
    const today = Date.now() - (Date.now() % DAY);
    const val = (c, key, daysAgo) => {
      const [level, growth] = c[key];
      const noise = (unit(`${c.uuid}${key}${daysAgo}`) - 0.5) * (key === 'cpu' ? 3 : 1);
      const weekly = key === 'cpu' ? 4 * Math.sin((2 * Math.PI * daysAgo) / 7) : 0;
      return Math.round(Math.max(1, Math.min(99, level - growth * daysAgo + noise + weekly)) * 100) / 100;
    };
    const fit = (pts) => {
      const n = pts.length;
      const mx = pts.reduce((a, p) => a + p[0], 0) / n;
      const my = pts.reduce((a, p) => a + p[1], 0) / n;
      const sxx = pts.reduce((a, p) => a + (p[0] - mx) ** 2, 0);
      const sxy = pts.reduce((a, p) => a + (p[0] - mx) * (p[1] - my), 0);
      const slope = sxx ? sxy / sxx : 0;
      const sst = pts.reduce((a, p) => a + (p[1] - my) ** 2, 0);
      const ssr = pts.reduce((a, p) => a + (p[1] - (my + slope * (p[0] - mx))) ** 2, 0);
      return { slope, r2: sst ? Math.max(0, 1 - ssr / sst) : 1 };
    };
    const iso = (ms) => new Date(ms).toISOString().slice(0, 10);
    const runway = (t, values, days) => {
      const end = t[t.length - 1];
      const pts = t.map((x, i) => [(x - end) / DAY, values[i]]).filter((p) => p[0] >= -days && p[1] !== null);
      const start = pts.slice(-3).reduce((a, p) => a + p[1], 0) / Math.min(3, pts.length);
      const { slope, r2 } = fit(pts);
      const status = slope >= 0.01 ? 'growing' : 'no_growth';
      const thresholds = [80, 100].map((pct) => {
        if (start >= pct) return { pct, state: 'reached', days: 0, date: iso(end) };
        if (status !== 'growing') return { pct, state: 'never', days: null, date: null };
        const d = Math.ceil((pct - start) / slope);
        return d > 1825 ? { pct, state: 'beyond', days: null, date: null } : { pct, state: 'projected', days: d, date: iso(end + d * DAY) };
      });
      return { status, window_days: days, points: pts.length, current: Math.round(start * 100) / 100, slope_per_day: Math.round(slope * 10000) / 10000, r2: Math.round(r2 * 1000) / 1000, thresholds };
    };
    const runways = (tr) => Object.fromEntries([30, 60, 90].map((d) => [String(d), { cpu: runway(tr.t, tr.cpu_pct, d), memory: runway(tr.t, tr.memory_pct, d), storage: runway(tr.t, tr.storage_pct, d) }]));
    const trendsOf = (c) => {
      const t = [];
      for (let d = 89; d >= 0; d -= 1) t.push(today - d * DAY);
      const ago = (x) => Math.round((today - x) / DAY);
      return {
        t,
        cpu_pct: t.map((x) => val(c, 'cpu', ago(x))),
        memory_pct: t.map((x) => val(c, 'mem', ago(x))),
        storage_pct: t.map((x) => val(c, 'sto', ago(x))),
        storage_used_bytes: t.map((x) => Math.round((c.cap * val(c, 'sto', ago(x))) / 100)),
        iops: t.map((x) => Math.round(380 * c.nodes * (0.7 + 0.6 * unit(`${c.uuid}io${ago(x)}`)))),
        latency_usecs: t.map((x) => Math.round((c.hv === 'ESXi' ? 1300 : 650) * (0.8 + 0.5 * unit(`${c.uuid}lat${ago(x)}`)))),
      };
    };
    const VMS = ['sql-prod-01', 'k8s-worker-07', 'exch-mbx-02', 'vdi-gold-image', 'erp-app-03', 'k8s-worker-12', 'elk-data-01', 'jenkins-agent-4', 'citrix-ddc-01', 'web-front-09'];
    const vm = (c, i) => ({
      uuid: `${c.uuid}-vm${i}`,
      name: `${c.name.toLowerCase().slice(0, 3)}-${VMS[i % VMS.length]}`,
      cluster_uuid: c.uuid,
      cpu_pct: Math.round((88 - i * 13 - unit(c.uuid + i) * 6) * 100) / 100,
      memory_pct: Math.round((91 - i * 9 - unit(c.uuid + 'm' + i) * 5) * 100) / 100,
      iops: Math.round(2400 / (i + 1) - unit(c.uuid + 'i' + i) * 50),
      latency_usecs: Math.round(5200 / (i + 1) + 300),
      vcpus: [16, 8, 8, 4, 4][i],
      memory_bytes: [64, 32, 48, 16, 16][i] * GB,
    });
    const top = (c) => {
      const rows = [0, 1, 2, 3, 4].map((i) => vm(c, i));
      const by = (f) => [...rows].sort((a, b) => b[f] - a[f]);
      return { cpu: by('cpu_pct'), memory: by('memory_pct'), iops: by('iops'), latency: by('latency_usecs') };
    };
    const nodes = (c) =>
      Array.from({ length: c.nodes }, (_, i) => ({
        uuid: `${c.uuid}-n${i + 1}`,
        name: `${c.name.toLowerCase()}-n${i + 1}`,
        status: c.crit && i === 2 ? 'warn' : 'ok',
        node_state: 'NORMAL',
        maintenance: !!(c.crit && i === 2),
        degraded: false,
        cvm_ip: `10.20.${MC.indexOf(c)}.${31 + i}`,
        cvm_status: 'ok',
        metadata_store: 'Normal mode',
        cpu_pct: Math.round((c.cpu[0] + (unit(c.uuid + 'n' + i) - 0.5) * 10) * 100) / 100,
        memory_pct: Math.round((c.mem[0] + (unit(c.uuid + 'nm' + i) - 0.5) * 6) * 100) / 100,
        num_vms: 18 + Math.round(unit(c.uuid + 'v' + i) * 20),
      }));
    const nics = (c) =>
      nodes(c).flatMap((n, i) =>
        [0, 1, 2, 3].map((k) => ({
          host: n.name,
          name: c.hv === 'ESXi' ? `vmnic${k}` : `eth${k}`,
          link_speed_kbps: k < 2 ? 25_000_000 : 10_000_000,
          rx_kBps: k % 2 === 0 ? Math.round(18000 * unit(n.uuid + 'rx' + k)) : 0,
          tx_kBps: k % 2 === 0 ? Math.round(12000 * unit(n.uuid + 'tx' + k)) : 0,
          rx_errors: c.crit && i === 0 && k === 0 ? 3 : 0,
          tx_errors: 0,
          rx_dropped: c.crit && i === 0 && k === 0 ? 12 : 0,
          tx_dropped: 0,
        })),
      );
    const ALERTS = ['Disk failure detected on {h}', 'Host {h} is down', 'CVM {h} rebooted', 'Boot RAID volumes degraded on CVM {h}'];
    const alerts = [];
    MC.forEach((c, ci) => {
      for (let i = 0; i < c.crit; i += 1) {
        alerts.push({ id: `a-${c.uuid}-${i}`, title: ALERTS[(ci + i) % ALERTS.length].replace('{h}', `${c.name.toLowerCase()}-n${i + 3}`), severity: 'critical', cluster_uuid: c.uuid, cluster_name: c.name, source: `${c.name.toLowerCase()}-n${i + 3}`, source_type: 'node', created_at: new Date(Date.now() - (ci * 7 + i * 3 + 2) * 3600_000).toISOString(), acknowledged: i % 2 === 1, impact: 'Availability' });
      }
    });
    alerts.sort((a, b) => b.created_at.localeCompare(a.created_at));
    const cluster = (c) => {
      const tr = trendsOf(c);
      const sto = tr.storage_pct[tr.storage_pct.length - 1];
      const used = Math.round((c.cap * sto) / 100);
      const ns = nodes(c);
      const nw = nics(c);
      const sum = (k) => nw.reduce((a, n) => a + (n[k] ?? 0), 0);
      const reasons = [];
      if (c.crit) reasons.push(`${c.crit} critical alert${c.crit === 1 ? '' : 's'}`);
      const status = c.crit ? 'critical' : c.warn ? 'warning' : 'ok';
      if (!c.crit && c.warn) reasons.push(`${c.warn} warnings`);
      return {
        uuid: c.uuid, name: c.name, aos_version: c.aos, hypervisors: [c.hv], num_nodes: c.nodes,
        health: { status, reasons, critical_alerts: c.crit, warning_alerts: c.warn, nodes_total: ns.length, nodes_ok: ns.length, resiliency: { current: c.nodes >= 8 ? 2 : 1, desired: c.nodes >= 8 ? 2 : 1, ok: true, kind: 'fault_tolerance' } },
        utilization: { cpu_pct: tr.cpu_pct[89], memory_pct: tr.memory_pct[89], storage_pct: sto },
        capacity: {
          cpu_count: c.cores, memory_bytes: c.memB, storage_used_bytes: used, storage_capacity_bytes: c.cap, storage_free_bytes: c.cap - used,
          storage_logical_bytes: Math.round(used * 1.45), redundancy_factor: c.nodes >= 8 ? 3 : 2,
          usable_bytes: Math.round((c.cap * 0.95) / (c.nodes >= 8 ? 3 : 2)), usable_free_bytes: Math.round((c.cap * 0.95 - used) / (c.nodes >= 8 ? 3 : 2)),
        },
        performance: { iops: tr.iops[89], read_iops: Math.round(tr.iops[89] * 0.38), write_iops: Math.round(tr.iops[89] * 0.62), latency_usecs: tr.latency_usecs[89], bandwidth_kbps: tr.iops[89] * 24 },
        efficiency: { data_reduction_ratio: 1.45, overall_ratio: 4.8, saved_bytes: Math.round(used * 0.45), compression_ratio: 1.36, dedup_ratio: 1.07, erasure_coding_ratio: 1.0 },
        alerts: { critical: c.crit, warning: c.warn },
        prism_runway_days: { cpu: 365, memory: c.mem[1] ? 160 : 365, storage: c.sto[1] > 0.1 ? 41 : 365, overall: c.sto[1] > 0.1 ? 41 : c.mem[1] ? 160 : 365 },
        nodes: ns,
        network: { rx_kBps: sum('rx_kBps'), tx_kBps: sum('tx_kBps'), errors: sum('rx_errors') + sum('tx_errors'), dropped: sum('rx_dropped') + sum('tx_dropped'), nics: nw, links_up: nw.length },
        top_vms: top(c),
      };
    };
    const head = (part) => ({ part, host: 'pc01.example.com', version: 'pc.2024.3', collected_at: new Date(Date.now() - 8000).toISOString(), ranges: [30, 60, 90], history_days: 90, cached: false, age_seconds: 0, ttl_seconds: 120, notes: mopt === 'partial' && part === 'now' ? ['PHX-PROD-01: network of phx-prod-01-n4: HTTP 500 from https://pc01.example.com:9440/PrismGateway/services/rest/v2.0/hosts/c-phx-prod-01-n4/host_nics'] : [] });
    const weighted = (cl, key, w) => {
      const tw = cl.reduce((a, c) => a + w(c), 0);
      return Math.round((cl.reduce((a, c) => a + c.utilization[key] * w(c), 0) / tw) * 100) / 100;
    };
    const nowResult = () => {
      const cl = MC.map(cluster);
      const total = (k) => cl.reduce((a, c) => a + (c.capacity[k] ?? 0), 0);
      return {
        ...head('now'),
        clusters: cl,
        total: {
          name: 'All clusters', clusters: cl.length,
          utilization: { cpu_pct: weighted(cl, 'cpu_pct', (c) => c.capacity.cpu_count), memory_pct: weighted(cl, 'memory_pct', (c) => c.capacity.memory_bytes), storage_pct: Math.round((total('storage_used_bytes') / total('storage_capacity_bytes')) * 10000) / 100 },
          capacity: { storage_used_bytes: total('storage_used_bytes'), storage_capacity_bytes: total('storage_capacity_bytes'), storage_free_bytes: total('storage_free_bytes'), usable_bytes: total('usable_bytes'), usable_free_bytes: total('usable_free_bytes'), storage_logical_bytes: total('storage_logical_bytes') },
          performance: { iops: cl.reduce((a, c) => a + c.performance.iops, 0), latency_usecs: 880 },
          efficiency: { saved_bytes: cl.reduce((a, c) => a + c.efficiency.saved_bytes, 0) },
          health: { critical: cl.filter((c) => c.health.status === 'critical').length, warning: 0, ok: cl.filter((c) => c.health.status === 'ok').length, nodes_total: cl.reduce((a, c) => a + c.nodes.length, 0), nodes_ok: cl.reduce((a, c) => a + c.nodes.length, 0) },
          alerts: { critical: alerts.length, warning: cl.reduce((a, c) => a + c.alerts.warning, 0) },
        },
        alerts: { critical: alerts, critical_count: alerts.length + 1, warning_count: 15, listed: alerts.length },
        sources: [
          { card: 'Clusters', api: 'v4 clusters' },
          { card: 'Health, alerts', api: 'v3 groups (alert) · PE v2.0 hosts, cluster' },
          { card: 'Utilization, efficiency, performance', api: 'v3 groups (cluster)' },
          { card: 'Trends and runway', api: 'v3 groups time series, daily, 90 days' },
          { card: 'Top VMs', api: 'v3 groups (mh_vm)' },
          { card: 'Nodes and CVMs', api: 'v3 groups (host) · PE v2.0 hosts' },
          { card: 'Network', api: 'PE v2.0 host_nics' },
        ],
      };
    };
    const trendsResult = () => {
      const rows = MC.map((c) => {
        const tr = trendsOf(c);
        return { uuid: c.uuid, name: c.name, trends: tr, runway: runways(tr) };
      });
      const w = MC.map((c) => [c.cores, c.memB, c.cap]);
      const t = rows[0].trends.t;
      const agg = (col, wi) => t.map((_, i) => Math.round((rows.reduce((a, r, ri) => a + r.trends[col][i] * w[ri][wi], 0) / w.reduce((a, x) => a + x[wi], 0)) * 100) / 100);
      const iops = t.map((_, i) => rows.reduce((a, r) => a + r.trends.iops[i], 0));
      const lat = t.map((_, i) => Math.round(rows.reduce((a, r) => a + r.trends.latency_usecs[i] * r.trends.iops[i], 0) / iops[i]));
      const tot = { t, cpu_pct: agg('cpu_pct', 0), memory_pct: agg('memory_pct', 1), storage_pct: agg('storage_pct', 2), iops, latency_usecs: lat };
      return { ...head('trends'), clusters: rows, total: { name: 'All clusters', trends: tot, runway: runways(tot) } };
    };
    const never = () => new Promise(() => {});
    Object.assign(window.ntnx, {
      getMetrics: (req) => {
        if (mopt === 'loading') return never();
        if (mopt === 'error' && req.part === 'now') return fail('Could not reach Prism Central at pc01.example.com:9440 (connection timed out)');
        if (req.part === 'trends') {
          if (mopt === 'trendsLoading') return never();
          if (mopt === 'trendsError') return fail('Prism Central did not answer within 150 seconds. Try Refresh.');
          return delay(trendsResult, 250);
        }
        return delay(nowResult, 80);
      },
      cancelMetrics: () => delay({ cancelled: true }, 5),
    });
  }
  // ── end Insights > Metrics ───────────────────────────────────────────
})();
