// Typed registry of the Nutanix isometric icons copied into assets/nutanix-icons/
// (Runway icon tiers, rule 13 — see docs in assets/nutanix-icons/README.md).
//
//   tier 'mark'          "what is this thing": a product/platform mark, 16–24 px, always
//                        beside its text label (<NutanixIcon>).
//   tier 'illustration'  "nothing here yet / something went wrong": page-level empty
//                        states and full-page errors only, 160–240 px, one per view
//                        (<Illustration>).
//
// This file is the single source of truth: scripts/normalize-nutanix-icons.mjs reads the
// `source` of every entry to (re)generate the SVGs, their token CSS and the README, so add
// an icon here first, then run the script. Keep each entry on one line.

export interface NutanixIconEntry {
  /** File name in the "Nutanix Icons Isometric 2026-09-24 05_11_13" pack. */
  source: string;
  tier: 'mark' | 'illustration';
  /** Where the app uses it. */
  use: string;
}

export const NUTANIX_ICONS = {
  // ── Tier 2: product / platform marks ─────────────────────────────────────
  'nutanix-x': { source: 'Nutanix X - Side.svg', tier: 'mark', use: 'Command bar brand slot (recoloured to the bar tokens)' },
  prism: { source: 'Prism.svg', tier: 'mark', use: 'Overview "One Prism Central"; Snapshots source type PC' },
  'prism-segmented': { source: 'Prism Segmented.svg', tier: 'mark', use: 'Overview "Several Prism Centrals"; Snapshots source type Combined' },
  cluster: { source: 'Cluster.svg', tier: 'mark', use: 'Inventory Clusters; Overview "One cluster (Prism Element)"; Snapshots source type PE' },
  '2-node': { source: '2 Node.svg', tier: 'mark', use: 'Inventory Hosts (nodes)' },
  'vm-blue': { source: 'VM Blue.svg', tier: 'mark', use: 'Inventory VMs' },
  cvm: { source: 'CVM 1.svg', tier: 'mark', use: 'Inventory System VMs (CVMs, PC VMs)' },
  'enterprise-storage': { source: 'Enterprise Storage.svg', tier: 'mark', use: 'Inventory Storage containers' },
  'generic-storage': { source: 'Generic Storage.svg', tier: 'mark', use: 'Inventory Volume groups' },
  network: { source: 'Network.svg', tier: 'mark', use: 'Inventory Subnets' },
  sdn: { source: 'SDN 1.svg', tier: 'mark', use: 'Inventory VPCs' },
  router: { source: 'Router.svg', tier: 'mark', use: 'Inventory Floating IPs' },
  'vm-template': { source: 'VM Template (2).svg', tier: 'mark', use: 'Inventory Templates, OVAs, Images and templates' },
  ssd: { source: 'SSD.svg', tier: 'mark', use: 'Inventory Images (disk images)' },
  'protection-policies': { source: 'Protection Policies (2).svg', tier: 'mark', use: 'Inventory Protection policies' },
  'disaster-recovery': { source: 'Disaster Recovery.svg', tier: 'mark', use: 'Inventory Recovery plans' },
  'multi-cloud': { source: 'Multi Cloud.svg', tier: 'mark', use: 'Inventory Availability zones, Remote sites' },
  alerts: { source: 'Alerts.svg', tier: 'mark', use: 'Inventory Alerts' },
  update: { source: 'Update (3).svg', tier: 'mark', use: 'Inventory LCM (Life Cycle Manager)' },
  flow: { source: 'Flow.svg', tier: 'mark', use: 'Inventory Flow security policies' },
  categories: { source: 'Categories.svg', tier: 'mark', use: 'Inventory Flow categories' },
  policies: { source: 'Policies.svg', tier: 'mark', use: 'Inventory Service groups, Address groups' },
  'acropolis-file-services': { source: 'Acropolis File Services.svg', tier: 'mark', use: 'Inventory File servers, File shares (Nutanix Files)' },
  objects: { source: 'Objects.svg', tier: 'mark', use: 'Inventory Object stores, Buckets (Nutanix Objects)' },
  'era-database': { source: 'ERA (4).svg', tier: 'mark', use: 'Inventory Databases (NDB / Era)' },
  'vm-database-blue': { source: 'VM Database Blue.svg', tier: 'mark', use: 'Inventory Database servers (NDB)' },
  stopwatch: { source: 'Stopwatch.svg', tier: 'mark', use: 'Inventory Time machines (NDB)' },
  person: { source: 'Person.svg', tier: 'mark', use: 'Inventory Users (IAM)' },
  roles: { source: 'Roles.svg', tier: 'mark', use: 'Inventory Roles (IAM)' },
  projects: { source: 'Projects.svg', tier: 'mark', use: 'Inventory Projects' },
  'ai-powered-automation': { source: 'AI Powered Automation.svg', tier: 'mark', use: 'Inventory Playbooks (X-Play)' },
  report: { source: 'Report.svg', tier: 'mark', use: 'Inventory NCM reports' },
  licenses: { source: 'Licenses (2).svg', tier: 'mark', use: 'Inventory Licensing' },

  // ── Tier 3: illustrations (empty states, full-page errors) ───────────────
  'no-recovery-point-created': { source: 'No Recovery Point Created.svg', tier: 'illustration', use: 'Snapshots and Overview empty state "No snapshots yet"' },
  'no-files': { source: 'No Files (3).svg', tier: 'illustration', use: 'Inventory empty state "No snapshots to browse"' },
  'no-comparison-created': { source: 'No Comparison Created.svg', tier: 'illustration', use: 'Compare empty state "Compare needs two snapshots"' },
  'no-target': { source: 'No Target.svg', tier: 'illustration', use: 'Multi-PC with no targets; Overview "No saved targets yet"' },
  tasks: { source: 'Tasks.svg', tier: 'illustration', use: 'Collecting empty state "No collection running"' },
  'no-workloads': { source: 'No Workloads.svg', tier: 'illustration', use: 'Results empty state "Nothing collected in this session"' },
  'generic-error': { source: 'Generic Error.svg', tier: 'illustration', use: 'Overview full-page error "Couldn’t load the overview"' },
  'storage-error': { source: 'Storage Error.svg', tier: 'illustration', use: 'Snapshots / Inventory / Compare full-page error "Couldn’t list snapshots"' },
  'vm-error': { source: 'VM Error.svg', tier: 'illustration', use: 'Results full-page error "Couldn’t read the snapshot"' },
} as const satisfies Record<string, NutanixIconEntry>;

export type NutanixIconName = keyof typeof NUTANIX_ICONS;

type Tiered<T extends NutanixIconEntry['tier']> = {
  [K in NutanixIconName]: (typeof NUTANIX_ICONS)[K]['tier'] extends T ? K : never;
}[NutanixIconName];

/** Names usable with <NutanixIcon> (tier 2). */
export type NutanixMarkName = Tiered<'mark'>;
/** Names usable with <Illustration> (tier 3). */
export type IllustrationName = Tiered<'illustration'>;

/** Inventory object type → its product mark. Unknown types fall back to no mark. */
export const OBJECT_TYPE_MARK: Record<string, NutanixMarkName> = {
  clusters: 'cluster',
  hosts: '2-node',
  vms: 'vm-blue',
  system_vms: 'cvm',
  storage_containers: 'enterprise-storage',
  volume_groups: 'generic-storage',
  subnets: 'network',
  vpcs: 'sdn',
  floating_ips: 'router',
  images: 'vm-template',
  templates: 'vm-template',
  ovas: 'vm-template',
  images_only: 'ssd',
  protection_policies: 'protection-policies',
  recovery_plans: 'disaster-recovery',
  availability_zones: 'multi-cloud',
  remote_sites: 'multi-cloud',
  alerts: 'alerts',
  lcm_entities: 'update',
  security_policies: 'flow',
  categories: 'categories',
  service_groups: 'policies',
  address_groups: 'policies',
  file_servers: 'acropolis-file-services',
  file_shares: 'acropolis-file-services',
  object_stores: 'objects',
  buckets: 'objects',
  databases: 'era-database',
  database_servers: 'vm-database-blue',
  time_machines: 'stopwatch',
  users: 'person',
  roles: 'roles',
  projects: 'projects',
  playbooks: 'ai-powered-automation',
  ncm_reports: 'report',
  licenses: 'licenses',
};

/** Snapshot source kind → its mark. */
export const SOURCE_KIND_MARK: Record<'pc' | 'pe' | 'combined', NutanixMarkName> = {
  pc: 'prism',
  pe: 'cluster',
  combined: 'prism-segmented',
};
