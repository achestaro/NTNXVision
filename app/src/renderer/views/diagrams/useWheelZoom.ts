// Wheel and trackpad zoom for the Diagrams canvas, as in Figma and draw.io:
// * Ctrl/⌘ + wheel, and a trackpad pinch (Chromium sends it as a ctrlKey wheel), zoom
//   around the pointer: the figure point under it stays under it;
// * a plain wheel or two-finger scroll pans (the desk's own scrolling, untouched).
// Steps are batched per animation frame and proportional to the wheel distance, so a
// pinch is smooth and a mouse notch is one comfortable step; clamped to [min, max].

import { useEffect, useLayoutEffect, useRef } from 'react';

/** Zoom factor per wheel pixel (a 100 px mouse notch, clamped to 30 px, is about 1.2x). */
const PER_PX = 0.006;
const MAX_STEP_PX = 30;

export function useWheelZoom(
  deskRef: React.RefObject<HTMLElement>,
  zoom: number,
  setZoom: (z: number) => void,
  min: number,
  max: number,
): void {
  const live = useRef({ zoom, setZoom, min, max });
  live.current = { zoom, setZoom, min, max };
  /** The figure point (pt) to keep under the client point, once the new zoom is drawn. */
  const anchor = useRef<{ sx: number; sy: number; cx: number; cy: number } | null>(null);
  const pending = useRef<{ factor: number; cx: number; cy: number; frame: number } | null>(null);

  useEffect(() => {
    const desk = deskRef.current;
    if (!desk) return undefined;
    const onWheel = (ev: WheelEvent) => {
      if (!(ev.ctrlKey || ev.metaKey)) return; // plain scroll: pan
      ev.preventDefault();
      const px = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaMode === 2 ? ev.deltaY * 400 : ev.deltaY;
      const step = Math.max(-MAX_STEP_PX, Math.min(MAX_STEP_PX, px));
      const factor = Math.exp(-step * PER_PX);
      if (pending.current) {
        pending.current.factor *= factor;
        pending.current.cx = ev.clientX;
        pending.current.cy = ev.clientY;
        return;
      }
      pending.current = {
        factor,
        cx: ev.clientX,
        cy: ev.clientY,
        frame: requestAnimationFrame(() => {
          const p = pending.current;
          pending.current = null;
          if (!p) return;
          const { zoom: z, setZoom: set, min: lo, max: hi } = live.current;
          const next = Math.max(lo, Math.min(hi, Math.round(z * p.factor * 1000) / 1000));
          if (next === z) return;
          const sheet = desk.querySelector('.dg-sheet');
          const r = sheet?.getBoundingClientRect();
          if (r) anchor.current = { sx: (p.cx - r.left) / z, sy: (p.cy - r.top) / z, cx: p.cx, cy: p.cy };
          set(next);
        }),
      };
    };
    desk.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      desk.removeEventListener('wheel', onWheel);
      if (pending.current) cancelAnimationFrame(pending.current.frame);
      pending.current = null;
    };
  }, [deskRef.current]);

  // After the new zoom is laid out: scroll so the anchored figure point is under the pointer.
  useLayoutEffect(() => {
    const a = anchor.current;
    const desk = deskRef.current;
    anchor.current = null;
    if (!a || !desk) return;
    const r = desk.querySelector('.dg-sheet')?.getBoundingClientRect();
    if (!r) return;
    desk.scrollLeft += r.left + a.sx * zoom - a.cx;
    desk.scrollTop += r.top + a.sy * zoom - a.cy;
  }, [zoom]);
}
