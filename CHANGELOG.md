# Changelog

NTNXVision follows [semantic versioning](https://semver.org): **patch** (0.4.1) for fixes,
**minor** (0.5.0) for new features, **major** (1.0.0) for breaking changes such as a snapshot
format older builds can't read. Windows shows the version as X.Y.Z.0 in Installed apps.
Every pull request that changes shipped code raises it with `node scripts/bump-version.mjs`
and adds a section here (CI checks both).

## 0.11.0 (2026-10-19)

### Added
- Network Segment Design (Networking): per AZ, each cluster's virtual switches with their VLAN
  subnets, prefixes, gateways and IPAM; external subnets counted apart.
- Flow Virtual Networking Design (Networking): per AZ, external subnets with NAT, each VPC's
  router, routes, policies, overlay subnets and floating IPs.
- Microsegmentation Design (Security Posture): per AZ, Flow policies by state with the groups
  they secure and their inbound and outbound rule peers (now collected, read-only).
- The Logical Cluster Design shows GPUs and nodes in maintenance; the Hosts table has a
  Maintenance column.
- All three new figures are editable on the Diagrams page and in the editable diagrams files.

### Changed
- Charts restyled: storage capacity per cluster and container, fullest first, with 80% and 90%
  marks; VMs per cluster as single-colour bars.
- The per-cluster compute figures are replaced by the Logical Cluster Design.

### Fixed
- VPCs with the same name on different Prism Centrals were drawn as one.

## 0.10.0 (2026-10-19)

### Added
- Architecture design figures in the Word and PDF reports, each with a short summary and the
  tables behind it: Logical Cluster Design (Logical Design), Management Plane Design (Prism
  Central), Host Network Configuration (Physical Design) and Disaster Recovery Design (Data
  Protection), plus an estate map when a figure is drawn in three or more parts.
- Editable diagrams: the Export dialog's "Editable diagrams" option (PDF or Word) also writes a
  zip with a multi-page Visio file, a draw.io file and one SVG per figure, Calibri throughout,
  with grouped shapes and glued connectors. It follows the chosen report sections.
- Every design figure can be edited on the Diagrams page (rename, reorder, remove, and add a
  cluster, component or note to the management, logical and DR figures). Edits reach the
  reports and the editable files; a figure's caption says "(edited)" only where an edit
  touches it, and its summary and tables stay as collected.

### Changed
- Reports are byte-stable: the same snapshot exports identical Word, PDF and diagram files.
- A figure starts its own page only when its measured summary and first part do not fit.
- One architecture figure that cannot be built never stops the report; a note says why.

## 0.9.0 (2026-10-19)

### Added
- LCM inventory before collection (opt-in, off by default, never from schedules): New
  collection and Multiple PCs can run a real LCM inventory on Prism Central and each in-scope
  cluster first, so firmware and software versions are current. Progress, per-target results
  and LCM's own error messages (for example a failed precheck) appear in the run and its notes;
  collection always continues.
- Prism Central pc.2024.3 and later use the v4 lifecycle API; pc.2024.1 and pc.2024.2 use
  LCM's legacy interface (validated against the demo estate only).

### Security
- The read-only guard still refuses every write except this one request, only while the
  user's opt-in is active. Legacy request bodies are decoded and checked exactly (method,
  arguments, the catalogue URL LCM itself reported), and their URLs are matched undecoded.

## 0.8.0 (2026-10-19)

### Added
- Report sections: choose which chapters the Word and PDF reports include and arrange their
  order, per export in the Export dialog ("Sections and order") or as a saved default in
  Settings > Reports. The cover, contents and Executive Summary always lead; the appendix
  always closes. Table, figure and chapter numbers follow the chosen order.
- Excel follows the same selection and order for its sheets.

### Changed
- Cross-references name only chapters the report includes: the Executive Summary mentions
  Licensing & Feature Usage and Assumptions & Constraints only when present, the v3 sunset
  finding cites the appendix by its real letter, and the Excel Overview points at the
  Capacity sheet only when the workbook has one.

## 0.7.1 (2026-10-19)

### Fixed
- Reports exported straight after a collection showed the full Prism Central build string
  (`el8.5-release-…-stable-<commit>`) on the Word / PDF cover, in the compatibility row and
  in the Results page header. Every report, export and app view now shows the version,
  whether the snapshot is fresh or loaded from disk.
- Compatibility checks read the version inside a build string instead of its `el8.5` prefix.

### Changed
- One Prism Central version format everywhere: `pc.` plus every part the API reports
  (`pc.7.5.1.1`, `pc.2024.3`), on the cover, in tables and prose, the diagrams, Excel, the
  snapshot list, the connection test and compatibility messages. AOS stays `AOS 7.5`.
- The supported range reads "pc.2024.1 to pc.7.5.1.6" (and "AOS 6.5 to 7.5") instead of
  using a dash.
- The full build string is kept in one place only: a "Build" row on the Excel Prism Central
  sheet, for support cases and LCM matching.
- Demo mode: `pc04.demo.local` reports a numeric version (7.5.1.1), and the scenario estates
  run pc.7.5.1.1.

## 0.7.0 (2026-10-19)

### Added
- Diagrams page (Explore): open a snapshot's generated diagrams, rename, reorder, add
  (user-added items get a dashed amber border and a USER-ADDED tag and never appear in tables)
  and delete elements, with undo / redo, and save the edits for that snapshot's exports.
- Saved edits are checked against the data they were made on: changed elements are flagged,
  and edits carry automatically to the next collection of the same estate when every edit still
  applies (otherwise the run and its email say why and the Diagrams page offers to carry them).
  Resetting a diagram is final for later collections.
- Edited figures keep the as-collected counts in their summary, caption and tables and add a
  bullet saying what was changed (and which snapshot the edits came from).
- Unsaved edits are guarded when leaving the page or closing the app.

### Changed
- Word table columns are sized from real font metrics: numbers, dates, versions, host and
  cluster names stay on one line, header words wrap at natural break points, and long lists
  break after whole items.
- Word tables never leave their header alone at the foot of a page.
- Figures go on a page of their own only when the measured summary and figure do not fit.

### Fixed
- Word summaries could drop a space after a hyphenated name and captions showed gaps around
  hyphens; reports now use plain hyphens so names copy and search correctly.

## 0.6.0 (2026-10-19)

### Added
- Solution Conceptual Overview in the Word and PDF reports (Logical Design chapter): each
  availability zone with its Prism Central (version, deployment, size, services in use), its
  clusters (hypervisor, nodes, RF, hardware, DSF storage, external arrays by product name,
  Files / Objects / vCenter), AZ pairing (muted when not connected) and replication per
  direction typed from the RPO, remote AZs, a design summary and an availability zones table.
- Large estates split into parts of up to 3 availability zones (paired zones kept together)
  and every zone is drawn at the same level of detail: the cluster hosting Prism Central, the
  two largest clusters and a "+N more clusters" card listed in a clusters-per-AZ table.
- The figure is vector in the PDF; in Word its page turns landscape when it needs the width.
  The same estate always gives the same figure.
- Demo estates for small, medium, enterprise and very large environments (sm-pc01,
  md-pc01/02, en-pc01..04, xl-pc01..08).

### Changed
- The management and replication topology pictures are replaced by the conceptual overview.
- Word keeps short hyphenated names ("Table 5-2", "X-Play", PC names) on one line.

### Fixed
- PDF contents could reserve one page too many and fail the export on reports with many
  sections.
- Word left a blank page before figures taller than the page body (the Flow VPC figure).

## 0.5.0 (2026-10-18)

### Added
- Collection of the data the architecture design diagrams need: blocks and slots, Controller
  VMs, AHV virtual switches (bond mode, MTU, bridges, uplinks, LACP), host NICs (model, driver,
  firmware, speed, LLDP neighbour when reported), external storage arrays (Pure FlashArray,
  Dell PowerFlex, Dell PowerStore) and the containers on them, cluster type and fault
  tolerance, Prism Central deployment (size, scale-out, hosting cluster, PC VMs), enabled PC
  products, AZ registrations, protection policy sites and replication directions, v4 recovery
  plans, NearSync capability, Files and Objects deployment status.
- New collection area "Host networking (virtual switches, NICs)"; selecting it also collects
  Hosts.
- New Excel sheets and Inventory types: Virtual Switches, Host NICs, External Storage, PC VMs.

### Fixed
- A protection policy with an RPO of 0 is reported as Sync, not NearSync, including in
  snapshots collected before this fix.
- A cluster-scoped collection no longer keeps the AOS registrations of excluded clusters.
- Collection area object counts count the area's main objects (Hosts counted blocks and CVMs too).
- Nutanix Objects reads "not enabled" only when Prism Central says so (HTTP 503); other
  errors read "status unknown".

## 0.4.2 (2026-10-18)

### Changed
- One app icon everywhere (Cube Iris: an eye with an isometric cube in the iris): the
  top-left of the app, window, taskbar and dock, desktop shortcut, Installed apps, tray and
  menu bar. Art in `app/build/art/`, all sizes generated by `npm run icons`.

## 0.4.1 (2026-10-18)

### Fixed
- Prism Central and AOS versions show as the plain version (7.5.1.1) instead of the full
  build string (el8.5-release-ganges-7.5.1.1-stable-<commit>) on every page, report and
  comparison, including snapshots collected before this fix.

## 0.4.0 (2026-10-18)

### Fixed
- Windows: saved passwords were stored empty, so targets always showed "Password needed"
  and Email and schedules could not sign in. `@napi-rs/keyring` 1.3.0 to 2.1.1 write empty
  Credential Manager entries; pinned to 1.2.0, and the packaged `--smoke` check now saves and
  reads back a real value. Re-enter each saved password once after upgrading.
- Long collection notes stay inside their cards on Results (#3).

### Added
- Email reports over SMTP, sent now or on a schedule to several addresses; scheduled sends
  collect fresh first (#7).
- Complete ESXi clusters: Prism Element and optional vCenter enrichment, port groups and
  retries when Prism throttles (#6).
- Per-cluster vCPU:pCPU target in New collection, recorded on the snapshot (#5).

### Security
- Five review rounds (#8 to #14, #26 to #34): Electron lockdown (sandbox, CSP, app://,
  fuses), endpoint-bound keychain entries, tamper checks on saved files, certificate pinning
  when TLS verification is off, spreadsheet formula neutralisation, least-privilege CI with
  pinned actions and dependency audits.

## 0.3.0 (2026-08-25)

First tracked version: Runway UI, targets, cluster scope, capacity and oversubscription,
snapshot transfer, Excel, PDF and Word exports (#1, #2).
