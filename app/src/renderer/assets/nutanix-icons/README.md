# Nutanix isometric icons

Copied from the Nutanix isometric icon pack (`Nutanix Icons Isometric 2026-09-24 05_11_13/`, kept outside this
repo in `nutanix-move-manager/docs/design/assets/ICONS/`). Only the files the app uses are here. Licence: Nutanix
brand assets, for use in Nutanix-related tooling under the Nutanix brand guidelines; not for redistribution outside the
product.

**Generated — do not edit by hand.** The list lives in `src/renderer/components/icons/nutanixManifest.ts`; to add or
change an icon, edit that file and run `node scripts/normalize-nutanix-icons.mjs "<pack folder>"` (needs
`npm i --no-save svgo@3`). The script minifies each file (svgo, 1-decimal precision), prefixes its ids, and replaces
every colour with a token class (`nutanix-icons.css`): greys become surface / rule / ink steps, purples the accent, reds
the error colour. That is why one drawing works in light, dark and high contrast, and follows the Iris / Prism accent.
The originals (`Name.svg`, the pack's dark-surface drawing) are preferred over the `Name (1).svg` light-surface copies.

## How the app uses them (Runway rule 13)

Three icon tiers, one job each:

1. **Phosphor glyphs** (`<Icon>`) — what a control does. Buttons, menus, inputs, the rail.
2. **Product / platform marks** (`<NutanixIcon>`, tier `mark` below) — what a thing is. 16–24 px, always next to the
   text label that names it, decorative (`aria-hidden`).
3. **Isometric illustrations** (`<Illustration>`, tier `illustration` below) — empty states and full-page errors only,
   160–240 px, at most one per view. Never inside tables, dialogs, toasts or menus.

Both components inline the SVG (bundled by Vite through `import.meta.glob(..., { query: '?raw', eager: true })`), so
the token classes apply and the packaged app needs no network.

## Tier 2: marks

| File | Original | Used for | Size |
|---|---|---|---|
| `nutanix-x.svg` | `Nutanix X - Side.svg` | Command bar brand slot (recoloured to the bar tokens) | 0.8 KB → 0.5 KB |
| `prism.svg` | `Prism.svg` | Overview "One Prism Central"; Snapshots source type PC | 0.5 KB → 0.4 KB |
| `prism-segmented.svg` | `Prism Segmented.svg` | Overview "Several Prism Centrals"; Snapshots source type Combined | 6.7 KB → 5.4 KB |
| `cluster.svg` | `Cluster.svg` | Inventory Clusters; Overview "One cluster (Prism Element)"; Snapshots source type PE | 5.5 KB → 3.9 KB |
| `2-node.svg` | `2 Node.svg` | Inventory Hosts (nodes) | 3.6 KB → 2.6 KB |
| `vm-blue.svg` | `VM Blue.svg` | Inventory VMs | 1.5 KB → 1.2 KB |
| `cvm.svg` | `CVM 1.svg` | Inventory System VMs (CVMs, PC VMs) | 6.7 KB → 4.9 KB |
| `enterprise-storage.svg` | `Enterprise Storage.svg` | Inventory Storage containers | 5.1 KB → 3.7 KB |
| `generic-storage.svg` | `Generic Storage.svg` | Inventory Volume groups | 6.8 KB → 5.6 KB |
| `network.svg` | `Network.svg` | Inventory Subnets | 3.8 KB → 2.1 KB |
| `sdn.svg` | `SDN 1.svg` | Inventory VPCs | 7.1 KB → 5.2 KB |
| `router.svg` | `Router.svg` | Inventory Floating IPs | 3.7 KB → 2.7 KB |
| `vm-template.svg` | `VM Template (2).svg` | Inventory Templates, OVAs, Images and templates | 5.4 KB → 3.8 KB |
| `ssd.svg` | `SSD.svg` | Inventory Images (disk images) | 3.0 KB → 2.2 KB |
| `protection-policies.svg` | `Protection Policies (2).svg` | Inventory Protection policies | 5.2 KB → 3.9 KB |
| `disaster-recovery.svg` | `Disaster Recovery.svg` | Inventory Recovery plans | 4.2 KB → 2.9 KB |
| `multi-cloud.svg` | `Multi Cloud.svg` | Inventory Availability zones, Remote sites | 3.0 KB → 1.9 KB |
| `alerts.svg` | `Alerts.svg` | Inventory Alerts | 7.5 KB → 5.2 KB |
| `update.svg` | `Update (3).svg` | Inventory LCM (Life Cycle Manager) | 1.8 KB → 1.4 KB |
| `flow.svg` | `Flow.svg` | Inventory Flow security policies | 7.2 KB → 5.1 KB |
| `categories.svg` | `Categories.svg` | Inventory Flow categories | 3.8 KB → 3.0 KB |
| `policies.svg` | `Policies.svg` | Inventory Service groups, Address groups | 4.2 KB → 3.3 KB |
| `acropolis-file-services.svg` | `Acropolis File Services.svg` | Inventory File servers, File shares (Nutanix Files) | 5.4 KB → 3.7 KB |
| `objects.svg` | `Objects.svg` | Inventory Object stores, Buckets (Nutanix Objects) | 20.9 KB → 12.7 KB |
| `era-database.svg` | `ERA (4).svg` | Inventory Databases (NDB / Era) | 4.4 KB → 3.3 KB |
| `vm-database-blue.svg` | `VM Database Blue.svg` | Inventory Database servers (NDB) | 2.9 KB → 2.2 KB |
| `stopwatch.svg` | `Stopwatch.svg` | Inventory Time machines (NDB) | 3.5 KB → 2.4 KB |
| `person.svg` | `Person.svg` | Inventory Users (IAM) | 2.2 KB → 1.6 KB |
| `roles.svg` | `Roles.svg` | Inventory Roles (IAM) | 4.7 KB → 2.9 KB |
| `projects.svg` | `Projects.svg` | Inventory Projects | 6.0 KB → 4.6 KB |
| `ai-powered-automation.svg` | `AI Powered Automation.svg` | Inventory Playbooks (X-Play) | 9.4 KB → 6.1 KB |
| `report.svg` | `Report.svg` | Inventory NCM reports | 4.7 KB → 3.6 KB |
| `licenses.svg` | `Licenses (2).svg` | Inventory Licensing | 7.1 KB → 5.0 KB |

## Tier 3: illustrations

| File | Original | Used for | Size |
|---|---|---|---|
| `no-recovery-point-created.svg` | `No Recovery Point Created.svg` | Snapshots and Overview empty state "No snapshots yet" | 6.3 KB → 3.9 KB |
| `no-files.svg` | `No Files (3).svg` | Inventory empty state "No snapshots to browse" | 7.3 KB → 4.7 KB |
| `no-comparison-created.svg` | `No Comparison Created.svg` | Compare empty state "Compare needs two snapshots" | 4.8 KB → 3.6 KB |
| `no-target.svg` | `No Target.svg` | Multi-PC with no targets; Overview "No saved targets yet" | 5.4 KB → 3.9 KB |
| `tasks.svg` | `Tasks.svg` | Collecting empty state "No collection running" | 7.9 KB → 5.5 KB |
| `no-workloads.svg` | `No Workloads.svg` | Results empty state "Nothing collected in this session" | 7.8 KB → 5.0 KB |
| `generic-error.svg` | `Generic Error.svg` | Overview full-page error "Couldn’t load the overview" | 7.9 KB → 4.4 KB |
| `storage-error.svg` | `Storage Error.svg` | Snapshots / Inventory / Compare full-page error "Couldn’t list snapshots" | 5.0 KB → 4.1 KB |
| `vm-error.svg` | `VM Error.svg` | Results full-page error "Couldn’t read the snapshot" | 3.7 KB → 2.9 KB |
