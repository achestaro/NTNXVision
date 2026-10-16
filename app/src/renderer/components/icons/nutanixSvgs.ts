// Raw SVG text of every copied Nutanix icon, bundled by Vite at build time (eager ?raw
// glob), so the packaged app renders them offline. Keyed by manifest name.

import '../../assets/nutanix-icons/nutanix-icons.css';
import type { NutanixIconName } from './nutanixManifest';

const files = import.meta.glob<string>('../../assets/nutanix-icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
});

const byName: Record<string, string> = {};
for (const [path, svg] of Object.entries(files)) {
  byName[path.slice(path.lastIndexOf('/') + 1, -'.svg'.length)] = svg;
}

export function nutanixSvg(name: NutanixIconName): string | undefined {
  return byName[name];
}
