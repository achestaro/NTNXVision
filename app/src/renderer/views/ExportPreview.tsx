// "What lands in the export folder" (mock B1): the files an export with the dialog's
// current options writes, as the sidecar names them (prefix, snapshot, date, collision
// suffix), with sizes (exact for the diagrams bundle's .vsdx, .drawio and .svg files,
// "about" otherwise), and the Edited / Stale state of each figure file.

import React, { useState } from 'react';
import { CaretDownIcon, CaretRightIcon, FileIcon, FileZipIcon, FolderIcon } from '@phosphor-icons/react';
import type { ExportPreview as Preview, ExportPreviewFile } from '@shared/ipc';
import { Badge, Icon, InlineEmpty } from '../components';
import { fmtBytes, fmtCount } from '../lib/format';

function size(f: { size_bytes: number | null; approx: boolean }): string {
  if (f.size_bytes === null || f.size_bytes === undefined) return 'N/A';
  return f.approx ? `about ${fmtBytes(f.size_bytes)}` : fmtBytes(f.size_bytes);
}

function Tags({ tags }: { tags?: string[] }): JSX.Element | null {
  if (!tags?.length) return null;
  return (
    <>
      {tags.includes('edited') && <Badge className="acc">Edited</Badge>}
      {tags.includes('stale') && <Badge className="warn">Stale</Badge>}
    </>
  );
}

function Row({ f, depth, hl }: { f: ExportPreviewFile; depth: number; hl?: boolean }): JSX.Element {
  const [open, setOpen] = useState(false);
  const kids = f.children ?? [];
  const name = (
    <>
      <Icon icon={f.folder ? FolderIcon : FileIcon} size={14} />
      <span className="t" title={f.name}>
        {f.name}
      </span>
      <Tags tags={f.tags} />
    </>
  );
  return (
    <>
      <div className={hl ? 'exp-row hl' : 'exp-row'} role="row">
        <span className="exp-name" style={{ paddingLeft: depth * 16 }} role="cell">
          {kids.length ? (
            <button type="button" className="exp-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
              <Icon icon={open ? CaretDownIcon : CaretRightIcon} size={12} />
              {name}
            </button>
          ) : (
            name
          )}
        </span>
        <span className="exp-size" role="cell">
          {f.folder && kids.length ? `${fmtCount(kids.length)} files` : size(f)}
        </span>
        <span className="exp-kind" role="cell">
          {f.detail && !f.folder ? `${f.kind}, ${f.detail}` : f.folder ? f.detail ?? f.kind : f.kind}
        </span>
      </div>
      {open && kids.map((k) => <Row key={k.name} f={k} depth={depth + 1} />)}
    </>
  );
}

const CARDS: { title: string; text: string }[] = [
  { title: 'Visio (.vsdx)', text: 'One page per figure. Real shapes with text, connectors glued to shapes, Shape Data with the collected identity.' },
  { title: 'draw.io (.drawio)', text: 'One tab per figure in one file. Containers keep their children; connectors stay attached.' },
  { title: 'SVG', text: 'Live text in Figtree with Calibri and Segoe UI fallbacks; the report embeds an outlined copy.' },
  { title: 'PNG', text: '200 dpi, for slides and tools that cannot read SVG.' },
];

export function ExportPreviewPanel({ preview, loading, error }: { preview: Preview | null; loading: boolean; error: string | null }): JSX.Element {
  const d = preview?.diagrams ?? null;
  const head = d?.name ?? preview?.files[0]?.name ?? null;
  return (
    <aside className="exp-preview" aria-label="What lands in the export folder" aria-busy={loading} data-testid="export-preview">
      <div className="eyebrow">What lands in the export folder</div>
      {head && <h3 className="mono">{head}</h3>}
      <p className="hint" style={{ margin: 0 }}>
        {d
          ? `Written next to the report with the same name stem${d.package === 'folder' ? ', as a folder' : ''}. Every file is built from the same scene as the report figure, so they match one to one.`
          : 'Names follow the prefix, the snapshot and today’s date. An existing file is never overwritten: -2, -3 is added.'}
        {loading ? ' Updating…' : ''}
      </p>
      {error && <InlineEmpty>{error}</InlineEmpty>}
      {preview && (preview.files.length > 0 || d) ? (
        <div className="exp-tree" role="table" aria-label="Files">
          <div className="exp-tree-head" role="row">
            <span role="columnheader">Name</span>
            <span role="columnheader" style={{ textAlign: 'right' }}>
              Size
            </span>
            <span role="columnheader">Kind</span>
          </div>
          {preview.files.map((f) => (
            <Row key={f.name} f={f} depth={0} />
          ))}
          {d && (
            <>
              <div className="exp-row hl" role="row">
                <span className="exp-name" role="cell">
                  <Icon icon={d.package === 'folder' ? FolderIcon : FileZipIcon} size={14} />
                  <span className="t" title={d.name}>
                    {d.name}
                  </span>
                </span>
                <span className="exp-size" role="cell">
                  {size(d)}
                </span>
                <span className="exp-kind" role="cell">
                  {d.package === 'folder' ? 'Folder' : 'Zip'}, {fmtCount(d.count)} figure{d.count === 1 ? '' : 's'}
                </span>
              </div>
              {d.entries.map((e) => (
                <Row key={e.name} f={e} depth={1} />
              ))}
            </>
          )}
        </div>
      ) : (
        !loading && !error && <InlineEmpty>Choose a format to see the files.</InlineEmpty>
      )}
      {d && (
        <div className="exp-cards">
          {CARDS.map((c) => (
            <div key={c.title} className="exp-card">
              <strong>{c.title}</strong>
              {c.text}
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
