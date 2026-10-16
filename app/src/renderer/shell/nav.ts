// Rail groups and items — the same as the mocks — plus their go-to shortcuts.

import {
  ArchiveIcon,
  ArrowsDownUpIcon,
  DownloadSimpleIcon,
  GearSixIcon,
  SquaresFourIcon,
  StackIcon,
  TableIcon,
} from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import type { Screen } from '../lib/router';

export interface NavItem {
  screen: Screen;
  label: string;
  icon: PhosphorIcon;
  /** Second key of the "g <key>" go-to shortcut. */
  key?: string;
  /** Screens that keep this item highlighted. */
  also?: Screen[];
}

export const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  { label: '', items: [{ screen: 'overview', label: 'Overview', icon: SquaresFourIcon, key: 'o' }] },
  {
    label: 'Collect',
    items: [
      { screen: 'new', label: 'New collection', icon: DownloadSimpleIcon, key: 'n', also: ['collecting', 'results'] },
      { screen: 'multi', label: 'Multiple PCs', icon: StackIcon, key: 'm' },
    ],
  },
  {
    label: 'Explore',
    items: [
      { screen: 'inventory', label: 'Inventory', icon: TableIcon, key: 'i' },
      { screen: 'snapshots', label: 'Snapshots', icon: ArchiveIcon, key: 's' },
      { screen: 'compare', label: 'Compare', icon: ArrowsDownUpIcon, key: 'c' },
    ],
  },
];

export const SETTINGS_ITEM: NavItem = { screen: 'settings', label: 'Settings', icon: GearSixIcon, key: ',', also: ['legal'] };

export const ALL_NAV: NavItem[] = [...NAV_GROUPS.flatMap((g) => g.items), SETTINGS_ITEM];
