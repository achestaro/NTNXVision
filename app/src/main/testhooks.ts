// Test hooks (SEC-07). The NTNXVISION_* overrides exist for tests and debugging only.
// A packaged app ignores them, so a variable set in a launcher or login profile cannot
// redirect the sidecar, the keychain, the user data folder or the UI.

import { app } from 'electron';

export function testEnv(name: string): string | undefined {
  return app.isPackaged ? undefined : process.env[name];
}

/** Automated end-to-end runs (unpackaged only): never open Finder, files or browsers. */
export const e2eRun = testEnv('NTNXVISION_E2E') === '1';
