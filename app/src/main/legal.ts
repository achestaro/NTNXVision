// SPDX-License-Identifier: MIT
// Legal documents for the About & legal screen. electron-builder copies LICENSE, NOTICE and
// THIRD_PARTY_NOTICES.md into the app's resources folder (package.json → build.extraResources);
// in development they're read from the repository root.

import { app, ipcMain } from 'electron';
import { secureIpc } from './security';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { IPC, LegalDocuments } from '../shared/ipc';

function legalDir(): string {
  return app.isPackaged ? process.resourcesPath : join(app.getAppPath(), '..');
}

async function readOrNull(path: string): Promise<string | null> {
  try {
    return await readFile(path, 'utf8');
  } catch {
    return null;
  }
}

export async function getLegalDocuments(): Promise<LegalDocuments> {
  const dir = legalDir();
  const [license, notice, thirdPartyNotices] = await Promise.all(
    ['LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'].map((f) => readOrNull(join(dir, f))),
  );
  return { version: app.getVersion(), license, notice, thirdPartyNotices, source: dir };
}

export function registerLegalHandlers(): void {
  secureIpc(ipcMain).handle(IPC.getLegalDocuments, () => getLegalDocuments());
}
