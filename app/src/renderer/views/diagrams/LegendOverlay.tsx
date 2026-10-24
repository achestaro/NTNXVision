// The figure's legend as an overlay in the canvas's bottom-left corner: always in view
// while the figure is panned or zoomed (it sits on the stage, not on the scrolling
// sheet), one entry per row, at a fixed calm size. It reads the legend the engine drew
// into the scene (ids "legend:<i>" and "legend:<i>:label"), so text edits and the
// figure's own colours carry over; double-click a label to rewrite it. The exports draw
// the same legend at the figure's bottom left (core: diagrams/build.legend).

import React, { useMemo, useState } from 'react';
import type { DiagramScene, SceneStyle } from '@shared/ipc';

interface Entry {
  index: number;
  kind: 'box' | 'line';
  style: SceneStyle;
  labelId: string;
  text: string;
}

export function legendEntries(sc: DiagramScene): Entry[] {
  const out: Entry[] = [];
  const nodes = new Map(sc.nodes.map((n) => [n.id, n]));
  const edges = new Map(sc.edges.map((e) => [e.id, e]));
  for (let i = 0; ; i++) {
    const id = `legend:${i}`;
    const label = nodes.get(`${id}:label`);
    const swatch = nodes.get(id);
    const line = edges.get(id);
    if (!label || (!swatch && !line)) break;
    out.push({ index: i, kind: swatch ? 'box' : 'line', style: (swatch ?? line)!.style, labelId: label.id, text: label.lines.map((t) => t.text).join(' ') });
  }
  return out;
}

function Sample({ e }: { e: Entry }): JSX.Element {
  const st = e.style;
  // The exports' sample is 7.7 x 4.2 pt; drawn here at a fixed 12 x 7 px
  // (about 30% under the old legend at a typical fit).
  const k = 12 / 7.7;
  return (
    <svg className="dg-lg-sample" width={12} height={7} viewBox="0 0 12 7" aria-hidden="true">
      {e.kind === 'box' ? (
        <rect
          x={0.5}
          y={0.5}
          width={11}
          height={6}
          rx={st.radius ? Math.min(st.radius * k, 3) : 0}
          fill={st.fill ?? 'none'}
          stroke={st.stroke ?? undefined}
          strokeWidth={st.stroke ? Math.max(0.8, Math.min(st.stroke_w * k, 1.6)) : undefined}
          strokeDasharray={st.stroke && st.dash ? `${st.dash[0] * k * 0.6} ${st.dash[1] * k * 0.6}` : undefined}
        />
      ) : (
        <line
          x1={0}
          y1={3.5}
          x2={12}
          y2={3.5}
          stroke={st.stroke ?? '#000'}
          strokeWidth={Math.max(1, Math.min(st.stroke_w * k, 2.2))}
          strokeDasharray={st.dash ? `${st.dash[0] * k * 0.6} ${st.dash[1] * k * 0.6}` : undefined}
        />
      )}
    </svg>
  );
}

export function LegendOverlay({ scene, onText }: { scene: DiagramScene; onText?(key: string, line: number, value: string | null): void }): JSX.Element | null {
  const entries = useMemo(() => legendEntries(scene), [scene]);
  const [editing, setEditing] = useState<{ id: string; draft: string } | null>(null);
  if (!entries.length) return null;
  const commit = () => {
    if (!editing) return;
    const e = entries.find((x) => x.labelId === editing.id);
    const text = editing.draft.replace(/\s+/g, ' ').trim();
    setEditing(null);
    if (e && text && text !== e.text.replace(/…$/, '')) onText?.(e.labelId, 0, text);
  };
  return (
    <div className="dg-legend-overlay" role="group" aria-label="Legend" data-testid="dg-legend-overlay" onPointerDown={(e) => e.stopPropagation()}>
      {entries.map((e) => (
        <div key={e.index} className="dg-lg-row">
          <Sample e={e} />
          {editing?.id === e.labelId ? (
            <input
              className="dg-lg-input"
              aria-label="Text on the figure"
              autoFocus
              value={editing.draft}
              maxLength={200}
              onFocus={(ev) => ev.currentTarget.select()}
              onChange={(ev) => setEditing({ id: e.labelId, draft: ev.target.value })}
              onBlur={commit}
              onKeyDown={(ev) => {
                ev.stopPropagation();
                if (ev.key === 'Enter') commit();
                else if (ev.key === 'Escape') setEditing(null);
              }}
            />
          ) : (
            <span className="dg-lg-text" title="Double-click to edit" onDoubleClick={onText ? () => setEditing({ id: e.labelId, draft: e.text.replace(/…$/, '') }) : undefined}>
              {e.text}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
