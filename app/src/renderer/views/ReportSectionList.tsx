// The report sections list shared by Settings > Reports ("Custom section order") and the
// Export dialog ("Sections and order"): one row per report section in document order. The
// fixed sections (Executive Summary first, the appendix last) are shown in place but never
// move. With `reorderable`, the movable rows can be dragged (a grip on the left) or moved
// with the up / down buttons, which are the keyboard path: the moved row keeps focus and a
// live region announces its new position. With `selected`, every row has a checkbox; a
// section the snapshot has no data for is disabled with the reason beside it.

import React, { useId, useState } from 'react';
import { ArrowDownIcon, ArrowUpIcon, DotsSixVerticalIcon } from '@phosphor-icons/react';
import type { ReportSectionId } from '@shared/ipc';
import { REPORT_SECTION_CATALOG, moveSection, moveSectionTo, sectionInfo } from '@shared/reportSections';
import { Badge, Button, CheckboxBox, Icon } from '../components';

export interface SectionAvailability {
  available: boolean;
  hint: string | null;
}

export function ReportSectionList({
  order,
  onOrderChange,
  reorderable,
  selected,
  onToggle,
  availability,
  label,
  testId,
}: {
  /** The movable sections, in order (normalizeSectionOrder). */
  order: ReportSectionId[];
  onOrderChange?(next: ReportSectionId[]): void;
  reorderable: boolean;
  /** Included sections; omit for a list without checkboxes. */
  selected?: Partial<Record<ReportSectionId, boolean>>;
  onToggle?(id: ReportSectionId, on: boolean): void;
  /** Per-section data in the snapshot; omitted = every section available. */
  availability?: Partial<Record<ReportSectionId, SectionAvailability>>;
  /** Accessible name of the list. */
  label: string;
  testId?: string;
}): JSX.Element {
  const id = useId();
  const [dragging, setDragging] = useState<ReportSectionId | null>(null);
  const [over, setOver] = useState<ReportSectionId | null>(null);
  const [announce, setAnnounce] = useState('');
  const first = REPORT_SECTION_CATALOG.filter((s) => s.fixed === 'first').map((s) => s.id);
  const last = REPORT_SECTION_CATALOG.filter((s) => s.fixed === 'last').map((s) => s.id);
  const rows = [...first, ...order, ...last];

  const move = (sid: ReportSectionId, delta: -1 | 1) => {
    const next = moveSection(order, sid, delta);
    onOrderChange?.(next);
    const pos = next.indexOf(sid) + 1;
    setAnnounce(`${sectionInfo(sid)?.label ?? sid} moved to position ${pos} of ${next.length}.`);
    // At either end the pressed button turns disabled and would drop focus: hand it over.
    requestAnimationFrame(() => {
      const btn = (d: number) => document.querySelector<HTMLButtonElement>(`[data-move="${sid}:${d}"]`);
      if (btn(delta)?.disabled) btn(-delta)?.focus();
    });
  };

  const drop = (target: ReportSectionId) => {
    if (dragging && dragging !== target) {
      const next = moveSectionTo(order, dragging, target);
      onOrderChange?.(next);
      setAnnounce(`${sectionInfo(dragging)?.label ?? dragging} moved to position ${next.indexOf(dragging) + 1} of ${next.length}.`);
    }
    setDragging(null);
    setOver(null);
  };

  return (
    <div data-testid={testId}>
      <ol className="secl" aria-label={label}>
        {rows.map((sid) => {
          const info = sectionInfo(sid)!;
          const movable = !info.fixed && reorderable;
          const i = order.indexOf(sid);
          const avail = availability?.[sid];
          const off = avail ? !avail.available : false;
          const hintId = `${id}-${sid}-hint`;
          return (
            <li
              key={sid}
              className={['secl-row', info.fixed && 'fixed', dragging === sid && 'dragging', over === sid && dragging !== sid && 'over'].filter(Boolean).join(' ')}
              data-testid={`section-${sid}`}
              draggable={movable}
              onDragStart={movable ? (e) => {
                setDragging(sid);
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', sid);
              } : undefined}
              onDragOver={movable && dragging ? (e) => {
                e.preventDefault();
                setOver(sid);
              } : undefined}
              onDragLeave={movable ? () => setOver((o) => (o === sid ? null : o)) : undefined}
              onDrop={movable ? (e) => {
                e.preventDefault();
                drop(sid);
              } : undefined}
              onDragEnd={() => {
                setDragging(null);
                setOver(null);
              }}
            >
              {reorderable && (
                <span className="secl-grip" aria-hidden="true">
                  {!info.fixed && <Icon icon={DotsSixVerticalIcon} size={16} />}
                </span>
              )}
              {selected && (
                <CheckboxBox
                  checked={!off && !!selected[sid]}
                  disabled={off}
                  onChange={(v) => onToggle?.(sid, v)}
                  aria-label={info.label}
                  aria-describedby={off ? hintId : undefined}
                  data-testid={`section-check-${sid}`}
                />
              )}
              <span className={['secl-label', off && 'dis'].filter(Boolean).join(' ')}>
                {info.label}
                {off && avail?.hint && (
                  <span className="secl-hint" id={hintId}>
                    {avail.hint}
                  </span>
                )}
              </span>
              {info.fixed && <Badge>{info.fixed === 'first' ? 'Always first' : 'Always last'}</Badge>}
              {movable && (
                <span className="secl-move">
                  <Button
                    size="sm"
                    variant="ghost"
                    iconOnly
                    icon={ArrowUpIcon}
                    aria-label={`Move ${info.label} up`}
                    disabled={i <= 0}
                    data-move={`${sid}:-1`}
                    onClick={() => move(sid, -1)}
                  />
                  <Button
                    size="sm"
                    variant="ghost"
                    iconOnly
                    icon={ArrowDownIcon}
                    aria-label={`Move ${info.label} down`}
                    disabled={i >= order.length - 1}
                    data-move={`${sid}:1`}
                    onClick={() => move(sid, 1)}
                  />
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <div className="sr-only" aria-live="polite">
        {announce}
      </div>
    </div>
  );
}
