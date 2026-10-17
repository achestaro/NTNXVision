// Schedules: collect saved targets fresh at set times and email the reports. The main
// process owns the timer (it keeps running in the tray / menu bar while a schedule is
// enabled); this page lists, edits and starts them, and follows their state live.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { PlusIcon, XIcon } from '@phosphor-icons/react';
import type {
  ExportFormat,
  ExportSection,
  SavedConnection,
  ScheduleDefinition,
  ScheduleField,
  ScheduleFrequency,
  ScheduleInfo,
  ScheduleRunRecord,
  ScheduleTrigger,
  SchedulesState,
} from '@shared/ipc';
import { DEFAULT_EMAIL_BODY, DEFAULT_EMAIL_SUBJECT } from '@shared/ipc';
import { WEEK_ORDER, addressIssue, describeFrequency, nextRunAfter, parseAddressList, validateSchedule } from '@shared/schedule';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  ChecklistHeader,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  FormatCard,
  LoadingPage,
  PageHeader,
  Panel,
  SelectAllLink,
  Segmented,
  Status,
  StatusGlyph,
  Switch,
  useToast,
} from '../components';
import type { MenuItem } from '../components';
import { useRouter } from '../lib/router';
import { errorMessage, fmtAbs, fmtCount, fmtRel, targetLabel } from '../lib/format';
import { REPORT_FORMATS, REPORT_SECTIONS } from './reportOptions';

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

const TRIGGER_LABEL: Record<ScheduleTrigger, string> = { scheduled: 'Scheduled', catch_up: 'Caught up', manual: 'Run now' };

function recipientsLabel(s: ScheduleDefinition): string {
  const all = [...s.to, ...s.cc];
  if (!all.length) return 'None';
  return all.length === 1 ? all[0] : `${all[0]} +${fmtCount(all.length - 1)}`;
}

function LastRun({ run }: { run: ScheduleRunRecord | null }): JSX.Element {
  if (!run) {
    return (
      <Status tone="idle" shape="outline">
        Never
      </Status>
    );
  }
  if (run.status === 'running') {
    return <Status tone="live">{run.stage === 'email' ? 'Sending…' : 'Running…'}</Status>;
  }
  const when = run.finishedAt ?? run.startedAt;
  const ok = run.status === 'ok';
  return (
    <span className="st" title={`${run.message} (${fmtAbs(when)})`}>
      <StatusGlyph tone={ok ? 'ok' : 'error'} />
      <span className="sch-clip sch-last-text">{ok ? `Sent ${fmtRel(when)}` : /fail/i.test(run.message) ? run.message : `Failed: ${run.message}`}</span>
    </span>
  );
}

export function SchedulesView({ edit }: { edit?: string }): JSX.Element {
  const { navigate } = useRouter();
  const toast = useToast();
  const [state, setState] = useState<SchedulesState | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [targets, setTargets] = useState<SavedConnection[]>([]);
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [editing, setEditing] = useState<ScheduleInfo | 'new' | null>(null);
  const [deleting, setDeleting] = useState<ScheduleInfo | null>(null);
  const [busyDelete, setBusyDelete] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await window.ntnx.listSchedules();
      setState(s);
      setLoadError(null);
      return s;
    } catch (e) {
      setLoadError(errorMessage(e));
      setState((x) => x ?? { schedules: [], background: false, loginItemSupported: false });
      return null;
    }
  }, []);

  useEffect(() => {
    void load().then((s) => {
      if (edit === 'new') setEditing('new');
      else if (edit && s) setEditing(s.schedules.find((x) => x.id === edit) ?? null);
    });
    window.ntnx.listConnections().then(setTargets).catch(() => setTargets([]));
    window.ntnx
      .getSmtpSettings()
      .then((s) => setConfigured(s.configured))
      .catch(() => setConfigured(null));
    return window.ntnx.onSchedulesChanged(setState);
  }, []);

  const byId = useMemo(() => new Map(targets.map((t) => [t.id, t])), [targets]);
  const schedules = state?.schedules ?? [];

  const targetsCell = (s: ScheduleInfo) => {
    const labels = s.targetIds.map((id) => {
      const t = byId.get(id);
      return t ? targetLabel(t) : 'Deleted target';
    });
    const text = labels.join(', ');
    return (
      <span className="sch-clip sch-targets-text" title={text}>
        {text}
        {s.targetIds.length > 1 && <span className="muted"> · {s.combine ? 'Combined' : 'Separate'}</span>}
      </span>
    );
  };

  const runNow = async (s: ScheduleInfo) => {
    try {
      const next = await window.ntnx.runScheduleNow(s.id);
      setState(next);
      toast.show({ tone: 'ok', title: `Run queued · ${s.name}`, hint: 'It collects fresh, then emails the reports.' });
    } catch (e) {
      toast.show({ tone: 'error', title: `Couldn’t run ${s.name}`, detail: errorMessage(e) });
    }
  };

  const setEnabled = async (s: ScheduleInfo, enabled: boolean) => {
    try {
      setState(await window.ntnx.setScheduleEnabled(s.id, enabled));
    } catch (e) {
      toast.show({ tone: 'error', title: `Couldn’t ${enabled ? 'enable' : 'disable'} ${s.name}`, detail: errorMessage(e) });
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusyDelete(true);
    try {
      setState(await window.ntnx.deleteSchedule(deleting.id));
      toast.show({ tone: 'ok', title: `Deleted ${deleting.name}` });
      setDeleting(null);
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t delete the schedule', detail: errorMessage(e) });
    } finally {
      setBusyDelete(false);
    }
  };

  const rowActions = (s: ScheduleInfo): MenuItem[] => [
    { label: 'Run now', onSelect: () => void runNow(s), disabled: s.state !== 'idle' || s.problems.length > 0 },
    { label: 'Edit…', onSelect: () => setEditing(s) },
    'separator',
    { label: 'Delete…', danger: true, onSelect: () => setDeleting(s) },
  ];

  const newButton = (
    <Button variant="primary" icon={PlusIcon} onClick={() => setEditing('new')} disabled={!targets.length}>
      New schedule
    </Button>
  );

  const header = (
    <PageHeader eyebrow="Collect" title="Schedules" description="Collect fresh at set times and email the reports." actions={schedules.length ? newButton : undefined} />
  );

  const emailCallout =
    configured === false ? (
      <Callout
        tone="warn"
        title="Set up email first."
        actions={
          <Button size="sm" onClick={() => navigate({ screen: 'settings' })}>
            Open Settings
          </Button>
        }
      >
        Schedules email their reports. Add your mail server in Settings, under Email (SMTP).
      </Callout>
    ) : null;

  const dialogs = (
    <>
      {editing && (
        <ScheduleEditor
          schedule={editing === 'new' ? null : editing}
          schedules={schedules}
          targets={targets}
          onClose={() => setEditing(null)}
          onSaved={(saved, all, created) => {
            setState((s) => (s ? { ...s, schedules: all } : s));
            setEditing(null);
            toast.show({ tone: 'ok', title: `${created ? 'Created' : 'Saved'} ${saved.name}`, detail: saved.nextRun ? `Next run ${fmtAbs(saved.nextRun)}` : 'Disabled' });
            void load();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          open
          destructive
          busy={busyDelete}
          title={`Delete ${deleting.name}?`}
          confirmLabel="Delete schedule"
          onClose={() => setDeleting(null)}
          onConfirm={() => void confirmDelete()}
          body="It stops running. Snapshots it collected and reports it already sent are kept."
        />
      )}
    </>
  );

  if (state === null) {
    return (
      <>
        {header}
        <LoadingPage label="Loading schedules…" />
      </>
    );
  }

  const problems = schedules.filter((s) => s.problems.length);

  return (
    <>
      {header}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {loadError && <ErrorState title="Couldn’t load the schedules." error={loadError} onRetry={() => void load()} />}
        {emailCallout}
        {problems.map((s) => (
          <Callout
            key={s.id}
            tone="warn"
            title={`${s.name} can’t run as saved.`}
            actions={
              <Button size="sm" onClick={() => setEditing(s)}>
                Edit schedule
              </Button>
            }
          >
            {s.problems.join(' ')}
          </Callout>
        ))}
        {schedules.length === 0 ? (
          <Panel>
            <EmptyState
              illustration="tasks"
              title="No schedules yet"
              description={
                targets.length
                  ? 'A schedule collects a saved target fresh at a set time, builds the reports and emails them.'
                  : 'Add a target first: every schedule collects from a target saved on the Targets page.'
              }
              action={targets.length ? newButton : <Button variant="primary" onClick={() => navigate({ screen: 'targets', edit: 'new' })}>Add a target</Button>}
            />
          </Panel>
        ) : (
          <Panel aria-label="Schedules table" className="schedules-panel" style={{ minWidth: 0 }}>
            <div className="scroll-x">
              <DataTable<ScheduleInfo>
                ariaLabel="Schedules"
                primary
                className="tbl-schedules"
                rows={schedules}
                rowKey={(s) => s.id}
                onRowOpen={(s) => setEditing(s)}
                rowActions={rowActions}
                rowActionsLabel={(s) => `Actions for ${s.name}`}
                rowClassName={(s) => (s.problems.length ? 'r-warn' : s.lastRun?.status === 'failed' ? 'r-error' : undefined)}
                kebabAlwaysVisible
                columns={[
                  {
                    key: 'name',
                    header: 'Name',
                    className: 'nm',
                    render: (s) => (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, maxWidth: '100%' }}>
                        <button type="button" className="lnk sch-clip sch-name-text" onClick={() => setEditing(s)} title={s.name}>
                          {s.name}
                        </button>
                        {s.state === 'queued' && <Badge>Queued</Badge>}
                      </span>
                    ),
                  },
                  { key: 'targets', header: 'Targets', render: targetsCell },
                  { key: 'when', header: 'When', render: (s) => <span className="sch-clip sch-when-text" title={describeFrequency(s.frequency)}>{describeFrequency(s.frequency)}</span> },
                  {
                    key: 'to',
                    header: 'Recipients',
                    colClassName: 'col-recipients',
                    render: (s) => (
                      <span className="mono t12 sch-clip sch-to-text" title={[...s.to, ...s.cc].join(', ')}>
                        {recipientsLabel(s)}
                      </span>
                    ),
                  },
                  {
                    key: 'next',
                    header: 'Next run',
                    render: (s) =>
                      s.nextRun ? (
                        <span className="mono t12">{fmtAbs(s.nextRun)}</span>
                      ) : (
                        <Status tone="idle" shape="outline">
                          Disabled
                        </Status>
                      ),
                  },
                  { key: 'last', header: 'Last run', className: 'sch-last', render: (s) => <LastRun run={s.lastRun} /> },
                  {
                    key: 'enabled',
                    header: 'Enabled',
                    render: (s) => <Switch checked={s.enabled} onChange={(v) => void setEnabled(s, v)} label={`Enable ${s.name}`} />,
                  },
                ]}
                rowInlineActions={(s) => (
                  <Button size="sm" onClick={() => void runNow(s)} disabled={s.state !== 'idle' || s.problems.length > 0} aria-label={`Run ${s.name} now`} className="sch-run-inline" style={{ marginRight: 4 }}>
                    Run now
                  </Button>
                )}
              />
            </div>
          </Panel>
        )}
        {schedules.length > 0 && (
          <p className="hint" style={{ margin: 0 }}>
            {state.background
              ? 'While a schedule is enabled, closing the window keeps NTNXVision running in the menu bar or system tray. Missed runs run once at the next launch.'
              : 'No schedule is enabled. Enabled schedules keep NTNXVision running in the menu bar or system tray when the window is closed.'}
          </p>
        )}
      </div>
      {dialogs}
    </>
  );
}

// ─── editor ────────────────────────────────────────────────────────────

type Errors = Partial<Record<ScheduleField, string>>;
type Kind = ScheduleFrequency['kind'];

function ScheduleEditor({
  schedule,
  schedules,
  targets,
  onClose,
  onSaved,
}: {
  schedule: ScheduleInfo | null;
  schedules: ScheduleInfo[];
  targets: SavedConnection[];
  onClose(): void;
  onSaved(saved: ScheduleInfo, all: ScheduleInfo[], created: boolean): void;
}): JSX.Element {
  const f0 = schedule?.frequency;
  const [name, setName] = useState(schedule?.name ?? '');
  const [targetIds, setTargetIds] = useState<string[]>(schedule?.targetIds.length ? schedule.targetIds : [targets[0]?.id ?? '']);
  const [combine, setCombine] = useState(schedule?.combine ?? true);
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>(() => {
    const init = schedule?.formats ?? (['excel', 'pdf'] as ExportFormat[]);
    return { excel: init.includes('excel'), pdf: init.includes('pdf'), word: init.includes('word') };
  });
  const [sections, setSections] = useState<Record<ExportSection, boolean>>(
    () => Object.fromEntries(REPORT_SECTIONS.map((s) => [s.id, !schedule?.sections || schedule.sections.includes(s.id)])) as Record<ExportSection, boolean>,
  );
  const [kind, setKind] = useState<Kind>(f0?.kind ?? 'weekly');
  const [time, setTime] = useState(f0?.time ?? '07:00');
  const [days, setDays] = useState<number[]>(f0?.kind === 'weekly' ? f0.days : [1]);
  const [day, setDay] = useState(String(f0?.kind === 'monthly' ? f0.day : 1));
  const [to, setTo] = useState((schedule?.to ?? []).join(', '));
  const [cc, setCc] = useState((schedule?.cc ?? []).join(', '));
  const [subject, setSubject] = useState(schedule?.subject ?? DEFAULT_EMAIL_SUBJECT);
  const [body, setBody] = useState(schedule?.body ?? DEFAULT_EMAIL_BODY);
  const [enabled, setEnabled] = useState(schedule?.enabled ?? true);
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);

  const pcs = targets.filter((t) => t.mode === 'prism_central');
  const first = targets.find((t) => t.id === targetIds[0]);
  const multi = targetIds.length > 1;
  const canAddPc = first?.mode === 'prism_central' && pcs.some((t) => !targetIds.includes(t.id));

  const frequency = (): ScheduleFrequency =>
    kind === 'daily' ? { kind, time } : kind === 'weekly' ? { kind, time, days: [...days].sort() } : { kind, time, day: Number(day) };

  const definition = (): ScheduleDefinition => ({
    name: name.trim(),
    enabled,
    targetIds: targetIds.filter(Boolean),
    combine: multi ? combine : false,
    formats: REPORT_FORMATS.filter((f) => fmt[f.id]).map((f) => f.id),
    sections: REPORT_SECTIONS.filter((s) => sections[s.id]).map((s) => s.id),
    word: undefined,
    to: parseAddressList(to),
    cc: parseAddressList(cc),
    subject,
    body,
    frequency: frequency(),
  });

  const next = useMemo(() => nextRunAfter(frequency(), new Date()), [kind, time, days, day]);
  const clear = (...keys: ScheduleField[]) => setErrors((x) => ({ ...x, ...Object.fromEntries(keys.map((k) => [k, undefined])), form: undefined }));

  const save = async () => {
    if (saving) return;
    const def = definition();
    const others = schedules.filter((s) => s.id !== schedule?.id).map((s) => s.name);
    const e = validateSchedule(def, targets, others);
    setErrors(e);
    if (Object.values(e).some(Boolean)) return;
    setSaving(true);
    try {
      const r = await window.ntnx.saveSchedule({ ...def, id: schedule?.id ?? '' });
      if (r.ok && r.schedule) onSaved(r.schedule, r.schedules, !schedule);
      else setErrors(r.errors ?? { form: 'The schedule was not saved.' });
    } catch (err) {
      setErrors({ form: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const setTarget = (i: number, id: string) => {
    setTargetIds((ids) => {
      const nextIds = ids.map((x, j) => (j === i ? id : x));
      // A Prism Element target runs on its own.
      return targets.find((t) => t.id === nextIds[0])?.mode === 'prism_element' ? [nextIds[0]] : nextIds;
    });
    clear('targets');
  };

  const optionLabel = (t: SavedConnection) => `${targetLabel(t)} · ${t.host}`;
  const history = schedule?.history.slice(0, 5) ?? [];

  return (
    <Dialog
      open
      onClose={onClose}
      width={720}
      title={schedule ? `Edit ${schedule.name}` : 'New schedule'}
      subtitle={schedule ? `${describeFrequency(schedule.frequency)}${schedule.nextRun ? ` · next run ${fmtAbs(schedule.nextRun)}` : ' · disabled'}` : undefined}
      onConfirm={() => void save()}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            <Button variant="primary" onClick={() => void save()} disabled={saving} kbd="⌘↵" data-testid="schedule-save">
              {schedule ? 'Save schedule' : 'Create schedule'}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, marginTop: 16 }}>
        {errors.form && <Callout tone="error" title={errors.form} />}
        {schedule?.problems.length ? <Callout tone="warn" title="This schedule can’t run as saved.">{schedule.problems.join(' ')}</Callout> : null}
        <div className="email-fields">
          <Field id="sch-name" label="Name" error={errors.name} hint="Snapshots are saved under this name (with -2, -3 for later runs).">
            <input
              className="input"
              value={name}
              placeholder="e.g. Weekly estate report"
              data-autofocus
              onChange={(e) => (setName(e.target.value), clear('name'))}
            />
          </Field>
          <div>
            <span className="lbl" id="sch-enabled-lbl">
              Enabled
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 'var(--control-h)' }}>
              <Switch checked={enabled} onChange={setEnabled} labelledBy="sch-enabled-lbl" />
              <span className="hint">{enabled ? 'Runs at the times below.' : 'Kept, but does not run.'}</span>
            </div>
          </div>
        </div>

        <div>
          <label className="lbl" htmlFor="sch-target">
            {multi ? 'Prism Centrals' : 'Target'}
          </label>
          <div className="sch-targets">
            {targetIds.map((id, i) => (
              <div className="sch-target-row" key={i}>
                <select
                  id={i === 0 ? 'sch-target' : `sch-target-${i}`}
                  className="input"
                  value={id}
                  aria-label={i === 0 ? undefined : `Prism Central ${i + 1}`}
                  aria-invalid={!!errors.targets}
                  onChange={(e) => setTarget(i, e.target.value)}
                >
                  {!id && <option value="">Choose a target…</option>}
                  {(i === 0 && !multi ? targets : pcs)
                    .filter((t) => t.id === id || !targetIds.includes(t.id))
                    .map((t) => (
                      <option key={t.id} value={t.id}>
                        {optionLabel(t)}
                      </option>
                    ))}
                  {id && !targets.some((t) => t.id === id) && <option value={id}>Deleted target</option>}
                </select>
                {multi && (
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    icon={XIcon}
                    aria-label={`Remove ${first && i === 0 ? targetLabel(first) : `Prism Central ${i + 1}`}`}
                    onClick={() => (setTargetIds((ids) => ids.filter((_, j) => j !== i)), clear('targets'))}
                  />
                )}
              </div>
            ))}
          </div>
          {errors.targets && <div className="field-err">{errors.targets}</div>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', marginTop: 8 }}>
            {canAddPc && (
              <Button
                variant="ghost"
                size="sm"
                icon={PlusIcon}
                onClick={() => {
                  const add = pcs.find((t) => !targetIds.includes(t.id));
                  if (add) setTargetIds((ids) => [...ids, add.id]);
                  clear('targets');
                }}
              >
                Add another Prism Central
              </Button>
            )}
            {multi && (
              <Segmented<'combined' | 'separate'>
                aria-label="Reports for several Prism Centrals"
                value={combine ? 'combined' : 'separate'}
                onChange={(v) => setCombine(v === 'combined')}
                options={[
                  { value: 'combined', label: 'One combined report' },
                  { value: 'separate', label: 'A report per target' },
                ]}
              />
            )}
          </div>
          <div className="hint below">
            {first?.mode === 'prism_central'
              ? 'Collects the clusters last chosen for each target (new clusters included), like New collection.'
              : 'Collects the cluster directly over Prism Element.'}
          </div>
        </div>

        <div>
          <span className="lbl" id="sch-kind">
            When
          </span>
          <div className="sch-when">
            <Segmented<Kind>
              aria-labelledby="sch-kind"
              value={kind}
              onChange={(k) => (setKind(k), clear('days', 'day'))}
              options={[
                { value: 'daily', label: 'Daily' },
                { value: 'weekly', label: 'Weekly' },
                { value: 'monthly', label: 'Monthly' },
              ]}
            />
            {kind === 'weekly' && (
              <div className="sch-days" role="group" aria-label="Days of the week">
                {WEEK_ORDER.map((d) => (
                  <button
                    key={d}
                    type="button"
                    className="sch-day"
                    aria-pressed={days.includes(d)}
                    aria-label={DAY_NAMES[d]}
                    onClick={() => {
                      setDays((xs) => (xs.includes(d) ? xs.filter((x) => x !== d) : [...xs, d]));
                      clear('days');
                    }}
                  >
                    {DAY_SHORT[d]}
                  </button>
                ))}
              </div>
            )}
            {kind === 'monthly' && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <label htmlFor="sch-day" className="t12 muted">
                  Day
                </label>
                <input
                  id="sch-day"
                  className="input mono"
                  style={{ width: 72 }}
                  type="number"
                  min={1}
                  max={31}
                  value={day}
                  aria-invalid={!!errors.day}
                  onChange={(e) => (setDay(e.target.value), clear('day'))}
                />
              </span>
            )}
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
              <label htmlFor="sch-time" className="t12 muted">
                at
              </label>
              <input
                id="sch-time"
                className="input mono"
                style={{ width: 120 }}
                type="time"
                value={time}
                aria-invalid={!!errors.time}
                onChange={(e) => (setTime(e.target.value), clear('time'))}
              />
            </span>
          </div>
          {(errors.days || errors.day || errors.time) && <div className="field-err">{errors.days ?? errors.day ?? errors.time}</div>}
          <div className="mono t12 muted below" style={{ marginTop: 8 }} data-testid="schedule-next">
            {next ? `${describeFrequency(frequency())} · local time · next run ${fmtAbs(next)}` : kind === 'weekly' && !days.length ? 'Choose the days it runs' : 'Enter a valid time'}
            {kind === 'monthly' && Number(day) >= 29 ? ' · shorter months run on their last day' : ''}
          </div>
        </div>

        <fieldset className="plain">
          <legend className="lbl">Reports</legend>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 8 }}>
            {REPORT_FORMATS.map((f) => (
              <FormatCard key={f.id} title={f.label} description={f.desc} checked={fmt[f.id]} onChange={(v) => (setFmt((p) => ({ ...p, [f.id]: v })), clear('formats'))} />
            ))}
          </div>
          {errors.formats && <div className="field-err">{errors.formats}</div>}
        </fieldset>

        <div role="group" aria-labelledby="sch-sections">
          <ChecklistHeader label="Sections in PDF and Word" id="sch-sections" variant="lbl">
            <SelectAllLink
              selected={REPORT_SECTIONS.filter((s) => sections[s.id]).length}
              total={REPORT_SECTIONS.length}
              noun="sections"
              disabled={!fmt.pdf && !fmt.word}
              onSelectAll={() => setSections(Object.fromEntries(REPORT_SECTIONS.map((s) => [s.id, true])) as Record<ExportSection, boolean>)}
            />
          </ChecklistHeader>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: '0 16px' }}>
            {REPORT_SECTIONS.map((s) => (
              <Checkbox key={s.id} checked={sections[s.id]} onChange={(v) => setSections((p) => ({ ...p, [s.id]: v }))} disabled={!fmt.pdf && !fmt.word}>
                {s.label}
              </Checkbox>
            ))}
          </div>
        </div>

        <div className="email-fields">
          <Field id="sch-to" label="To" error={errors.to} hint="Separate addresses with commas.">
            <input
              className="input mono"
              value={to}
              placeholder="ops@example.com"
              spellCheck={false}
              autoCapitalize="off"
              onChange={(e) => (setTo(e.target.value), clear('to'))}
              onBlur={() => to.trim() && setErrors((x) => ({ ...x, to: addressIssue(parseAddressList(to), true) ?? undefined }))}
            />
          </Field>
          <Field id="sch-cc" label="CC (optional)" error={errors.cc}>
            <input
              className="input mono"
              value={cc}
              spellCheck={false}
              autoCapitalize="off"
              onChange={(e) => (setCc(e.target.value), clear('cc'))}
              onBlur={() => setErrors((x) => ({ ...x, cc: addressIssue(parseAddressList(cc), false) ?? undefined }))}
            />
          </Field>
          <Field id="sch-subject" label="Subject" error={errors.subject} className="span-all">
            <input className="input" value={subject} onChange={(e) => (setSubject(e.target.value), clear('subject'))} />
          </Field>
          <Field id="sch-body" label="Message" className="span-all" hint="Placeholders: {target}, {date}, {snapshot}, {summary}">
            <textarea className="input" rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
          </Field>
        </div>

        {history.length > 0 && (
          <div>
            <div className="lbl">Recent runs</div>
            <ul className="sch-history" aria-label="Recent runs">
              {history.map((h) => (
                <li key={h.id}>
                  <span className="mono t12">{fmtAbs(h.startedAt)}</span>
                  <span className="t12 muted">{TRIGGER_LABEL[h.trigger]}</span>
                  <span className="msg">
                    <Status tone={h.status === 'ok' ? 'ok' : h.status === 'running' ? 'live' : 'error'}>{h.message}</Status>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Dialog>
  );
}
