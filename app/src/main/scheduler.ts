// The scheduler and email relays of the main process.
//
// A scheduled send collects fresh, then emails: at a slot the scheduler runs a new
// collection of the schedule's target(s) through the sidecar (respecting each target's
// remembered cluster scope, saving snapshots like any run), then asks the sidecar to build
// the chosen reports and send them (send_email). Timing, the queue and catch-up live in
// schedules.ts (plain Node, tested); this file wires them to the sidecar, a 30 s timer,
// power events (wake from sleep), the tray / menu bar, OS notifications and login items.
//
// While any schedule is enabled the app keeps running in the tray (Windows) / menu bar
// (macOS) when its window is closed; main.ts asks `keepInBackground()` on close.

import { BrowserWindow, IpcMain, Menu, Notification, Tray, app, nativeImage, powerMonitor } from 'electron';
import { randomUUID } from 'node:crypto';
import type {
  AppSettings,
  ClusterListResult,
  CollectionAreasResult,
  ConnectionMode,
  EmailResult,
  EmailSnapshotRequest,
  InventoryResult,
  MultiInventoryResult,
  Schedule,
  ScheduleRunNowResult,
  ScheduleRunRecord,
  ScheduleSaveRequest,
  SchedulesState,
  SmtpClearResult,
  SmtpSaveRequest,
  SmtpTestRequest,
} from '../shared/ipc';
import { EMAIL_TAMPERED_MESSAGE, IPC, SCHEDULE_REVIEW_MESSAGE } from '../shared/ipc';
import { TargetRef, collectionFailureReason, describeFrequency, failureNotice, scheduleSnapshotNames } from '../shared/schedule';
import { SmtpStore, smtpParams } from './mail';
import { Job, RetryLater, RunOutcome, ScheduleRunner, ScheduleStore, nextRun, scheduleInfo } from './schedules';
import { SidecarClient, SidecarError } from './sidecar';
import { cleanOverrides, endpointTls, TARGETS_TAMPERED_MESSAGE } from './targets';
import type { StoredTarget } from './targets';
import * as store from './store';
import { defaultExportDir, wordTemplateFor } from './security';
import { defaultSectionOrder } from '../shared/reportSections';
import { bool, obj, optStr, optStrArray, str, strArray } from './validate';
import { APP_ICON_PNG, MAC_TEMPLATE_PNG } from './trayIcons';

/** JSON-RPC code the sidecar answers when a collection is already running. */
const BUSY_CODE = -32002;
const TICK_MS = 30_000;

export interface SchedulerHost {
  sidecar: SidecarClient;
  mail: SmtpStore;
  schedules: ScheduleStore;
  /** A collection the user started is running. */
  manualBusy(): boolean;
  showWindow(): void;
  /** Show the window again if it is hidden (the tray is going away). */
  revealIfHidden(): void;
  send(channel: string, payload: unknown): void;
  /** Quit for real (tray menu). */
  quit(): void;
  demo: boolean;
  e2e: boolean;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
const label = (t: { name?: string; host: string }) => t.name?.trim() || t.host;
const errMsg = (err: unknown) => (err instanceof Error ? err.message : String(err));

const FAILED = (stage: 'collect' | 'email', message: string, snapshots: string[] = [], extra: Partial<RunOutcome> = {}): RunOutcome => ({
  status: 'failed',
  stage,
  message,
  snapshots,
  ...extra,
});

/** A collection failure with a user-facing reason; the raw error goes to the run's detail. */
const COLLECT_FAILED = (raw: string): RunOutcome => {
  const reason = collectionFailureReason(raw);
  return FAILED('collect', `Collection failed: ${reason.message}`, [], { detail: reason.detail });
};

/** Where reports are saved when no folder is set: the folder the dialogs show (see AppInfo). */
export function reportFolder(settings: AppSettings = store.getSettings()): string {
  return settings.exportDir || defaultExportDir();
}

/** host (lower case) -> saved target name, for {target} in emails. */
export function targetNames(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of store.targets.list()) if (t.name?.trim()) out[t.host.trim().toLowerCase()] = t.name.trim();
  return out;
}

const DATE = new Intl.DateTimeFormat('en-US', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

export class Scheduler {
  readonly runner: ScheduleRunner;
  private tray: Tray | null = null;
  private timer: NodeJS.Timeout | null = null;
  /** Collection run ids started by schedules (their progress is not relayed to the renderer). */
  private readonly runIds = new Set<string>();
  private hiddenNoticeShown = false;

  constructor(private readonly host: SchedulerHost) {
    this.runner = new ScheduleRunner({
      store: host.schedules,
      now: () => new Date(),
      newId: () => randomUUID(),
      busy: () => host.manualBusy(),
      execute: (s, job) => this.execute(s, job),
      changed: () => this.changed(),
      finished: (s, r) => this.finished(s, r),
      notifyFailure: (s, r) => this.sendFailureNotice(s, r),
    });
  }

  // ─── lifecycle ───────────────────────────────────────────────────────

  /** After launch: mark interrupted runs, catch up missed slots once, start the timer. */
  start(): void {
    this.host.schedules.recoverInterrupted();
    this.refreshTray();
    this.runner.tick();
    this.timer = setInterval(() => this.runner.tick(), TICK_MS);
    // Timers drift or stall while the computer sleeps: check as soon as it wakes.
    powerMonitor.on('resume', () => this.runner.tick());
    powerMonitor.on('unlock-screen', () => this.runner.tick());
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.tray?.destroy();
    this.tray = null;
  }

  /** A user-started collection finished: a waiting scheduled run may start. */
  kick(): void {
    void this.runner.pump();
  }

  /** At least one schedule is enabled: closing the window keeps the app running. */
  background(): boolean {
    return this.host.schedules.list().some((s) => s.enabled);
  }

  isScheduledRun(runId: string | undefined): boolean {
    return !!runId && this.runIds.has(runId);
  }

  /** The schedule whose collection is running, for the "already running" message. */
  runningName(): string | null {
    return this.runner.running()?.name ?? null;
  }

  /** Started from a login item: open in the tray without showing the window. */
  startHidden(): boolean {
    if (!this.background()) return false;
    if (process.argv.includes('--background')) return true;
    if (process.platform === 'darwin') {
      try {
        return app.getLoginItemSettings().wasOpenedAtLogin === true;
      } catch {
        return false;
      }
    }
    return false;
  }

  /** The window is being closed: hide it instead (true) while schedules need the app. */
  hideOnClose(win: BrowserWindow): boolean {
    if (!this.background()) return false;
    win.hide();
    if (!this.hiddenNoticeShown && !this.host.e2e) {
      this.hiddenNoticeShown = true;
      this.notify(
        'NTNXVision is still running',
        `Schedules keep running from the ${process.platform === 'darwin' ? 'menu bar' : 'system tray'}. Quit from there to stop them.`,
      );
    }
    return true;
  }

  // ─── login item ──────────────────────────────────────────────────────

  loginItemSupported(): boolean {
    return app.isPackaged && !this.host.demo && !this.host.e2e && (process.platform === 'darwin' || process.platform === 'win32');
  }

  /** Apply the Start at login setting (installed app only: a dev build would register Electron itself). */
  applyLoginItem(settings: AppSettings = store.getSettings()): void {
    if (!this.loginItemSupported()) return;
    try {
      app.setLoginItemSettings({ openAtLogin: !!settings.startAtLogin, args: ['--background'] });
    } catch (err) {
      console.error('Could not update the login item:', err);
    }
  }

  // ─── state for the renderer ──────────────────────────────────────────

  private targetRefs(): TargetRef[] {
    return store.targets.list().map((t) => ({ id: t.id, mode: t.mode, name: label(t), host: t.host }));
  }

  state(): SchedulesState {
    const refs = this.targetRefs();
    return {
      schedules: this.host.schedules.list().map((s) => scheduleInfo(s, this.runner.state(s.id), refs)),
      background: this.background(),
      tampered: this.host.schedules.tampered,
      loginItemSupported: this.loginItemSupported(),
    };
  }

  private changed(): void {
    this.refreshTray();
    this.host.send(IPC.schedulesChanged, this.state());
  }

  // ─── tray / menu bar ─────────────────────────────────────────────────

  private nextUp(): { schedule: Schedule; at: Date } | null {
    let best: { schedule: Schedule; at: Date } | null = null;
    for (const s of this.host.schedules.list()) {
      const at = nextRun(s);
      if (at && (!best || at < best.at)) best = { schedule: s, at };
    }
    return best;
  }

  refreshTray(): void {
    if (!this.background()) {
      if (this.tray) {
        this.tray.destroy();
        this.tray = null;
        // No tray to reopen it from: a hidden window would leave the app unreachable.
        this.host.revealIfHidden();
      }
      return;
    }
    if (!this.tray) {
      const icon =
        process.platform === 'darwin'
          ? nativeImage.createFromBuffer(Buffer.from(MAC_TEMPLATE_PNG, 'base64'), { scaleFactor: 2 })
          : nativeImage.createFromBuffer(Buffer.from(APP_ICON_PNG, 'base64'));
      if (process.platform === 'darwin') icon.setTemplateImage(true);
      this.tray = new Tray(icon);
      if (process.platform !== 'darwin') this.tray.on('click', () => this.host.showWindow());
    }
    const next = this.nextUp();
    const running = this.runner.running();
    const nextLine = next ? `Next: ${next.schedule.name}, ${DATE.format(next.at)}` : 'No upcoming runs';
    const enabled = this.host.schedules.list().filter((s) => s.enabled);
    this.tray.setToolTip(running ? `NTNXVision: running ${running.name}` : `NTNXVision: ${nextLine}`);
    this.tray.setContextMenu(
      Menu.buildFromTemplate([
        { label: 'Open NTNXVision', click: () => this.host.showWindow() },
        { type: 'separator' },
        { label: running ? `Running: ${running.name}…` : nextLine, enabled: false },
        {
          label: 'Run due schedules now',
          click: () => {
            const n = this.runner.tick();
            if (!n && !this.runner.isActive()) this.notify('Nothing is due', next ? nextLine : 'No schedule is enabled.');
          },
        },
        {
          label: 'Run now',
          enabled: enabled.length > 0,
          submenu: enabled.map((s) => ({
            label: `${s.name} (${describeFrequency(s.frequency)})`,
            click: () => this.runNow(s.id),
          })),
        },
        { type: 'separator' },
        { label: 'Quit NTNXVision', click: () => this.host.quit() },
      ]),
    );
  }

  private runNow(id: string): void {
    try {
      this.runner.runNow(id);
    } catch (err) {
      console.error('Run now failed:', err);
    }
  }

  // ─── notifications ───────────────────────────────────────────────────

  /** Shown notifications, kept referenced until closed: a collected one can vanish on Windows. */
  private readonly shown = new Set<Notification>();

  private notify(title: string, body: string): void {
    if (this.host.e2e || !Notification.isSupported()) return;
    try {
      const n = new Notification({ title, body });
      this.shown.add(n);
      n.on('click', () => this.host.showWindow());
      n.on('close', () => this.shown.delete(n));
      n.on('failed', (_e, error) => {
        this.shown.delete(n);
        console.error('Notification failed:', error);
      });
      n.show();
      // Bound the set if a platform never reports close.
      if (this.shown.size > 20) this.shown.delete(this.shown.values().next().value as Notification);
    } catch (err) {
      console.error('Notification failed:', err);
    }
  }

  private finished(s: Schedule, r: ScheduleRunRecord): void {
    if (r.status === 'failed') this.notify(`Schedule failed: ${s.name}`, r.message);
    else if (r.status === 'warning') this.notify(`Schedule finished with warnings: ${s.name}`, r.message);
  }

  /** "Email when a run fails": one short notice to the schedule's recipients, never retried. */
  private async sendFailureNotice(s: Schedule, r: ScheduleRunRecord): Promise<{ sent: boolean; message: string } | null> {
    const smtp = await this.host.mail.info();
    if (smtp.tampered || !smtp.configured) return null;
    const labels = s.targetIds.map((id) => {
      const t = store.targets.get(id);
      return t ? label(t) : 'Deleted target';
    });
    const notice = failureNotice(s, labels, r);
    try {
      const res = await this.host.sidecar.call<EmailResult>('send_notice', {
        smtp: smtpParams(smtp, await this.host.mail.password()),
        to: s.to,
        cc: s.cc,
        subject: notice.subject,
        body: notice.body,
      });
      if (res.ok) {
        const n = (res.recipients ?? []).length || s.to.length + s.cc.length;
        return { sent: true, message: `Failure notice emailed to ${plural(n, 'recipient')}.` };
      }
      return { sent: false, message: `Failure notice not sent: ${res.message}` };
    } catch (err) {
      return { sent: false, message: `Failure notice not sent: ${errMsg(err)}` };
    }
  }

  // ─── running a schedule ──────────────────────────────────────────────

  /** A target's connection with its keychain passwords (NDB when the target has one saved). */
  private async connection(t: StoredTarget) {
    // Saved targets edited outside the app release no passwords: fail with the reason.
    if (store.targets.tampered) throw new Error(TARGETS_TAMPERED_MESSAGE);
    const password = (await store.targets.password(t.id)) ?? '';
    // Each endpoint has its own TLS choice; with verification off its trusted certificate is
    // pinned. A target with none trusted yet fails the run with a message to trust it.
    const tls = endpointTls(t);
    let ndb: { host: string; username: string; password: string; insecure: boolean; pinned_sha256?: string } | undefined;
    if (t.mode === 'prism_central' && t.ndbHost) {
      const ndbPassword = (await store.targets.ndbPassword(t.id)) ?? '';
      if (ndbPassword) ndb = { host: t.ndbHost, username: t.ndbUser || 'admin', password: ndbPassword, insecure: tls.ndb.insecure, pinned_sha256: tls.ndb.pinnedCert };
    }
    // The target's vCenter (ESXi enrichment), as New collection turns it on when one is saved.
    let vcenter: { host: string; username: string; password: string; insecure: boolean; pinned_sha256?: string } | undefined;
    if (t.mode === 'prism_central' && t.vcenterHost) {
      const vcPassword = (await store.targets.vcenterPassword(t.id)) ?? '';
      if (vcPassword) {
        vcenter = { host: t.vcenterHost, username: t.vcenterUser || 'administrator@vsphere.local', password: vcPassword, insecure: tls.vcenter.insecure, pinned_sha256: tls.vcenter.pinnedCert };
      }
    }
    // Per-cluster vCPU:pCPU targets remembered from the last manual run.
    const overrides = cleanOverrides(t.vcpuRatioOverrides);
    return { host: t.host, username: t.username, port: t.port, insecure: t.insecure, pinnedCert: tls.primary.pinnedCert, password, ndb, vcenter, overrides };
  }

  /**
   * The clusters to collect from a Prism Central target: its remembered selection where
   * it still applies, plus clusters registered since (new clusters are collected), as the
   * New collection picker preselects them. Undefined (every cluster) without a memory.
   */
  private async clusterScope(t: StoredTarget, password: string): Promise<string[] | undefined> {
    const memory = t.clusterSelection;
    if (t.mode !== 'prism_central' || !memory?.known?.length) return undefined;
    try {
      const listed = await this.host.sidecar.call<ClusterListResult>('list_clusters', {
        mode: t.mode,
        host: t.host,
        port: t.port,
        username: t.username,
        password,
        insecure: t.insecure,
        pinned_sha256: t.insecure ? t.pinnedCert : undefined,
      });
      const picked = new Set(memory.selected);
      const known = new Set(memory.known);
      const all = listed.clusters.map((c) => c.uuid);
      const next = all.filter((u) => picked.has(u) || !known.has(u));
      return next.length ? next : undefined;
    } catch {
      // Listing failed: the collection reports the connection problem itself.
      return memory.selected.length ? memory.selected : undefined;
    }
  }

  private async areas(): Promise<(mode: ConnectionMode) => string[]> {
    const catalogue = await this.host.sidecar.call<CollectionAreasResult>('list_areas');
    return (mode) => catalogue.areas.filter((a) => a.default_on && (mode === 'prism_central' || a.pe_supported)).map((a) => a.id);
  }

  private async execute(s: Schedule, _job: Job): Promise<RunOutcome> {
    if (this.host.manualBusy()) throw new RetryLater();
    if (s.needsReview) return FAILED('collect', SCHEDULE_REVIEW_MESSAGE);
    const smtp = await this.host.mail.info();
    if (smtp.tampered) return FAILED('email', EMAIL_TAMPERED_MESSAGE);
    if (!smtp.configured) return FAILED('email', 'Email is not set up. Set up the mail server in Settings > Email.');
    const targets = s.targetIds.map((id) => store.targets.get(id));
    if (!targets.length || targets.some((t) => !t)) {
      return FAILED('collect', 'A target of this schedule was deleted. Edit the schedule to choose another.');
    }
    const picked = targets as StoredTarget[];
    if (picked.length > 1 && picked.some((t) => t.mode !== 'prism_central')) {
      return FAILED('collect', 'Several targets must all be Prism Centrals. Edit the schedule.');
    }
    const settings = store.getSettings();
    const labels = picked.map(label);
    const names = scheduleSnapshotNames(s.name, labels, s.combine);
    const runId = randomUUID();
    this.runIds.add(runId);

    let snapshotPaths: string[] = [];
    let snapshotNames: string[] = [];
    let partial: string | null = null;
    let partialDetail: string | null = null;
    try {
      const areasFor = await this.areas();
      if (picked.length === 1) {
        const t = picked[0];
        const conn = await this.connection(t);
        const clusters = await this.clusterScope(t, conn.password);
        const result = await this.host.sidecar.call<InventoryResult>(t.mode === 'prism_element' ? 'inventory_cluster' : 'inventory', {
          run_id: runId,
          host: conn.host,
          username: conn.username,
          password: conn.password,
          port: conn.port,
          insecure: conn.insecure,
          pinned_sha256: conn.pinnedCert,
          name: names.targets[0],
          unique_name: true,
          areas: areasFor(t.mode),
          clusters,
          formats: [],
          export_dir: settings.exportDir,
          ndb: conn.ndb,
          vcenter: conn.vcenter,
          vcpu_ratio: settings.vcpuRatio,
          vcpu_ratio_overrides: conn.overrides,
        });
        if (!result.snapshot_path) {
          return FAILED('collect', `The snapshot could not be saved. ${result.warnings.join(' ')}`.trim());
        }
        snapshotPaths = [result.snapshot_path];
        snapshotNames = [result.snapshot_name ?? names.targets[0]];
      } else {
        const conns = await Promise.all(picked.map((t) => this.connection(t)));
        const scopes = await Promise.all(picked.map((t, i) => this.clusterScope(t, conns[i].password)));
        const res = await this.host.sidecar.call<MultiInventoryResult>('inventory_multi', {
          run_id: runId,
          targets: conns.map((c, i) => ({
            host: c.host,
            username: c.username,
            password: c.password,
            port: c.port,
            insecure: c.insecure,
            pinned_sha256: c.pinnedCert,
            name: names.targets[i],
            ndb: c.ndb,
            vcenter: c.vcenter,
            vcpu_ratio_overrides: c.overrides,
            clusters: scopes[i],
          })),
          areas: areasFor('prism_central'),
          formats: [],
          export_dir: settings.exportDir,
          combine: s.combine,
          combined_name: names.combined ?? undefined,
          unique_name: true,
          vcpu_ratio: settings.vcpuRatio,
        });
        const ok = res.targets.filter((t) => t.success) as (InventoryResult & { host: string })[];
        const bad = res.targets.filter((t) => !t.success) as { host: string; error: string }[];
        const why = (b: { host: string; error: string }) => `${b.host}: ${collectionFailureReason(b.error).message}`;
        const raw = bad.map((b) => `${b.host}: ${b.error}`).join('; ');
        if (!ok.length) {
          return FAILED('collect', `No target was collected. ${bad.map(why).join(' ')}`, [], { detail: raw });
        }
        if (bad.length) {
          partial = `Not collected: ${bad.map(why).join(' ')}`;
          partialDetail = raw;
        }
        const chosen = s.combine && res.combined?.snapshot_path ? [res.combined] : ok;
        snapshotPaths = chosen.map((r) => r.snapshot_path).filter((p): p is string => !!p);
        snapshotNames = chosen.map((r) => r.snapshot_name ?? '').filter(Boolean);
        if (!snapshotPaths.length) return FAILED('collect', 'The snapshots could not be saved.');
      }
    } catch (err) {
      if (err instanceof SidecarError && err.code === BUSY_CODE) throw new RetryLater();
      return COLLECT_FAILED(errMsg(err));
    } finally {
      this.runIds.delete(runId);
    }

    let sent: EmailResult;
    try {
      sent = await this.host.sidecar.call<EmailResult>('send_email', {
        smtp: smtpParams(smtp, await this.host.mail.password()),
        to: s.to,
        cc: s.cc,
        subject: s.subject,
        body: s.body,
        context: { target: labels.join(', '), target_names: targetNames() },
        snapshots: snapshotPaths,
        formats: s.formats,
        sections: s.sections,
        word: { template: 'standard', prepared_for: s.word?.preparedFor ?? '', author: s.word?.author || settings.author || '' },
        export_dir: reportFolder(settings),
        file_prefix: settings.filePrefix ?? '',
        vcpu_ratio: settings.vcpuRatio,
        section_order: defaultSectionOrder(settings),
      });
    } catch (err) {
      return FAILED('email', `Email failed: ${errMsg(err)}`, snapshotNames, { emailStatus: 'error' });
    }
    if (!sent.ok) return FAILED('email', `Email failed: ${sent.message}`, snapshotNames, { detail: sent.detail ?? null, emailStatus: sent.status });
    const files = sent.files?.length ?? sent.attachments?.length ?? 0;
    const recipients = sent.recipients?.length ?? s.to.length + s.cc.length;
    const message = `Emailed ${plural(files, 'report')} to ${plural(recipients, 'recipient')}`;
    // Mail went out: anything missing is a warning on a sent run, not a failure.
    const warnings: string[] = [];
    if (partial) warnings.push(partial);
    if (sent.status === 'sent_with_warnings' && sent.rejected?.length) warnings.push(`The server refused ${sent.rejected.join(', ')}.`);
    if (warnings.length) {
      const detail = [partialDetail, sent.status === 'sent_with_warnings' ? sent.detail : null].filter(Boolean).join('; ') || null;
      return { status: 'warning', stage: partial ? 'collect' : 'email', message: `${message}. ${warnings.join(' ')}`, detail, snapshots: snapshotNames };
    }
    return { status: 'ok', message, snapshots: snapshotNames };
  }

  // ─── IPC ─────────────────────────────────────────────────────────────

  registerHandlers(ipc: Pick<IpcMain, 'handle'>): void {
    const { mail, sidecar } = this.host;
    ipc.handle(IPC.getSmtpSettings, () => mail.info());
    ipc.handle(IPC.clearSmtpSettings, async (): Promise<SmtpClearResult> => {
      const settings = await mail.clear();
      // Every schedule emails its reports: none can run without a mail server.
      const disabledSchedules = this.host.schedules.disableAll();
      for (const s of this.host.schedules.list()) this.runner.drop(s.id);
      this.changed();
      return { settings, disabledSchedules };
    });
    ipc.handle(IPC.saveSmtpSettings, (_e, req: SmtpSaveRequest) => mail.save(obj(req, 'request') as unknown as SmtpSaveRequest));
    ipc.handle(IPC.testSmtp, async (_e, req: SmtpTestRequest) => {
      obj(req, 'request');
      const password = await mail.testPassword(req);
      return sidecar.call<EmailResult>('test_smtp', { smtp: smtpParams(req, password), to: req.to });
    });
    ipc.handle(IPC.emailSnapshot, async (_e, req: EmailSnapshotRequest) => {
      obj(req, 'request');
      strArray(req.snapshotPaths, 'snapshotPaths', 1000, 4096);
      strArray(req.to, 'to', 500, 320);
      optStrArray(req.cc, 'cc', 500, 320);
      optStr(req.subject, 'subject', 1000);
      optStr(req.body, 'body', 100000);
      optStr(req.filePrefix, 'filePrefix', 200);
      if (req.word) wordTemplateFor(req.word.template);
      const smtp = await mail.info();
      if (!smtp.configured) {
        const message = smtp.tampered ? EMAIL_TAMPERED_MESSAGE : 'Email is not set up. Set up the mail server in Settings > Email.';
        return { ok: false, status: 'not_configured', message } satisfies EmailResult;
      }
      const settings = store.getSettings();
      return sidecar.call<EmailResult>('send_email', {
        smtp: smtpParams(smtp, await mail.password()),
        to: req.to,
        cc: req.cc,
        subject: req.subject,
        body: req.body,
        context: { target_names: targetNames() },
        snapshots: req.snapshotPaths,
        formats: req.formats,
        sections: req.sections,
        word: req.word
          ? { template: req.word.template, prepared_for: req.word.preparedFor ?? '', author: req.word.author ?? '' }
          : { template: 'standard', prepared_for: '', author: settings.author ?? '' },
        export_dir: reportFolder(settings),
        file_prefix: req.filePrefix !== undefined ? req.filePrefix : settings.filePrefix ?? '',
        vcpu_ratio: settings.vcpuRatio,
        section_order: defaultSectionOrder(settings),
      });
    });

    ipc.handle(IPC.listSchedules, () => this.state());
    ipc.handle(IPC.saveSchedule, (_e, req: ScheduleSaveRequest) => {
      obj(req, 'request');
      const res = this.host.schedules.saveSchedule(req, this.targetRefs());
      const state = this.state();
      if (res.ok) this.changed();
      return {
        ok: res.ok,
        errors: res.errors,
        schedule: res.schedule ? state.schedules.find((x) => x.id === res.schedule!.id) : undefined,
        schedules: state.schedules,
      };
    });
    ipc.handle(IPC.deleteSchedule, (_e, id: string) => {
      str(id, 'id', 128);
      this.runner.drop(id);
      this.host.schedules.delete(id);
      this.changed();
      return this.state();
    });
    ipc.handle(IPC.setScheduleEnabled, (_e, id: string, enabled: boolean) => {
      str(id, 'id', 128);
      bool(enabled, 'enabled');
      if (!this.host.schedules.setEnabled(id, enabled)) throw new Error('That schedule no longer exists.');
      if (!enabled) this.runner.drop(id);
      this.changed();
      return this.state();
    });
    ipc.handle(IPC.runScheduleNow, (_e, id: string): ScheduleRunNowResult => {
      str(id, 'id', 128);
      const queued = this.runner.runNow(id);
      return { ...this.state(), queued };
    });
  }
}
