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
//   test:     'ok' | 'auth_failed' | 'untrusted_cert' | 'unreachable' | 'version_unsupported'
//   clusters: 'ok'      → listClusters answers 4 clusters (1 for a PE or pc-lab.example.com)
//             'loading' → listClusters never answers (the picker's loading state)
//             'error'   → listClusters fails (the picker's error state with retry)
//   scopeNotice: true → compareSnapshots reports different cluster scopes
//   platform: 'darwin' | 'win32' | 'linux'  ("Show in Finder" / "Show in Explorer" / "Show in folder")
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
  };

  // ── collection areas ──
  const AREAS = [
    ['clusters', 'Clusters', 'Infrastructure', true],
    ['hosts', 'Hosts / nodes', 'Infrastructure', true],
    ['storage', 'Storage containers', 'Infrastructure', true],
    ['subnets', 'Subnets / networks', 'Infrastructure', true],
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
      detail: s === 'pending' ? null : s === 'running' ? detail : detail || null,
    };
  };

  const listeners = { progress: new Set(), finished: new Set() };
  const emit = (kind, e) => listeners[kind].forEach((cb) => cb(e));

  const finalAreas = () => RUN.map((r, i) => areaObj(i, r[1] === 'running' ? 'collected' : r[1]));

  const result = (req) => ({
    summary: summary(4, 28, 1214),
    source_type: req.mode || 'prism_central',
    prism_central: { host: req.host, version: 'pc.2024.3', version_supported: true },
    warnings: [],
    collection_notes: { vms: '3 VMs skipped', fvn: 'Not enabled', files: 'Not deployed' },
    snapshot_path: `${DIR}${sep}${req.name}.ntnxsnap.json`,
    snapshot_name: req.name,
    exports: {},
    export_errors: {},
    started_at: new Date(Date.now() - 252000).toISOString(),
    finished_at: new Date().toISOString(),
    duration_ms: 252000,
    areas: finalAreas().map((a) => (['vms', 'fvn', 'files'].includes(a.id) ? a : { ...a, status: 'collected', detail: null })),
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
      { areas: 'Health alerts', api: 'v3', reason: 'v4 alerts endpoint not served on pc.2024.3', after_sunset: { tone: 'ok', label: 'Moves to v4 after a PC upgrade' } },
      { areas: 'Recovery plans · availability zones', api: 'v3', reason: 'No v4 equivalent yet', after_sunset: { tone: 'warn', label: 'Stops collecting' } },
      { areas: 'X-Play playbooks · IAM projects', api: 'v3', reason: 'No v4 equivalent yet', after_sunset: { tone: 'warn', label: 'Stops collecting' } },
    ],
    compat: { version: 'pc.2024.3', supported: true, range: 'pc.2024.1 – 7.5.1.6' },
    scope: req.mode === 'prism_element' ? null : scopeOf(req.clusters || REGISTERED.map((c) => c.uuid)),
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
        version: 'pc.2024.3',
        formats: req.formats || [],
        typical_seconds: 240,
        areas: RUN.map((_, i) => areaObj(i, 'pending')),
      });
      const upto = opt.run === 'hold' ? 12 : RUN.length;
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
    getAppInfo: () => delay({ version: '0.4.0', platform: opt.platform, defaultExportDir: EXPORT_DIR }),
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
      const base = { product: req.mode, name: host, supported_range: 'pc.2024.1 – 7.5.1.6', typical_seconds: req.mode === 'prism_element' ? 60 : 240 };
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
    getCollectionAreas: () => delay({ areas: AREAS, typical_seconds: { prism_central: 240, prism_element: 60 } }),
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
    exportSnapshot: (req) => {
      const s = find(req.snapshotPath);
      const name = s ? s.name : 'snapshot';
      const paths = {};
      req.formats.forEach((f) => (paths[f] = `${EXPORT_DIR}${sep}${name}.${f === 'excel' ? 'xlsx' : f === 'word' ? 'docx' : 'pdf'}`));
      return delay({ paths, errors: {}, path: Object.values(paths)[0] || null, files: [] }, 400);
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
    deleteTarget: (id) => {
      connections = connections.filter((c) => c.id !== id);
      return delay(() => connections.slice());
    },
    getSettings: () => delay(() => ({ ...settings })),
    setSettings: (s) => {
      settings = { ...settings, ...s };
      return delay(() => ({ ...settings }), 10);
    },
  };
})();
