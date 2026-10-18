// Packaging smoke test: `NTNXVision --smoke` (or NTNXVISION_SMOKE=1) starts the main
// process, checks the native keyring module loads and round-trips a value, pings the Python sidecar through the
// normal SidecarClient, prints one JSON line and exits — 0 on success, 1 on failure.
// No window is created. Used by CI against the packaged app and by scripts/build-*.

import { app } from 'electron';
import { randomBytes } from 'node:crypto';
import { KeyringModule, makeKeychain } from './keychain';
import type { SidecarClient } from './sidecar';

const TIMEOUT_MS = 60_000;

export function isSmokeRun(): boolean {
  return process.argv.includes('--smoke') || process.env.NTNXVISION_SMOKE === '1';
}

/** Write, read back through a fresh entry, delete: "ok" or what went wrong. */
export function keychainRoundTrip(mod: KeyringModule): string {
  const kc = makeKeychain(mod, 'NTNXVision');
  const account = `smoke:${randomBytes(6).toString('hex')}`;
  const value = randomBytes(16).toString('hex');
  try {
    kc.set(account, value);
    const back = kc.get(account);
    return back === value ? 'ok' : `read back ${back === null ? 'nothing' : `${back.length} chars`}`;
  } catch (err) {
    return String(err);
  } finally {
    try {
      kc.delete(account);
    } catch {
      // nothing was written
    }
  }
}

export async function runSmoke(sidecar: SidecarClient, checkRenderer: () => Promise<string>): Promise<void> {
  const report: Record<string, unknown> = {
    smoke: 'ntnxvision',
    version: app.getVersion(),
    packaged: app.isPackaged,
    platform: `${process.platform}-${process.arch}`,
  };
  let ok = true;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('@napi-rs/keyring') as KeyringModule;
    report.keyring = 'ok';
    // A real write, then a read through a new entry (as after a restart): @napi-rs/keyring
    // 1.3.0 to 2.1.1 stored empty credentials on Windows and only the writing entry saw
    // the value. Required on Windows; on macOS a CI keychain may refuse, so it is reported only.
    report.keyringRoundTrip = keychainRoundTrip(mod);
    if (process.platform === 'win32' && report.keyringRoundTrip !== 'ok') ok = false;
  } catch (err) {
    report.keyring = String(err);
    ok = false;
  }
  try {
    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error(`no ping reply within ${TIMEOUT_MS} ms`)), TIMEOUT_MS),
    );
    report.ping = await Promise.race([sidecar.call('ping'), timeout]);
    if ((report.ping as { pong?: boolean })?.pong !== true) ok = false;
  } catch (err) {
    report.ping = String(err);
    ok = false;
  }
  // The real window settings load the built renderer over app://, it renders, no CSP violation.
  try {
    report.renderer = await checkRenderer();
  } catch (err) {
    report.renderer = String(err);
    ok = false;
  }
  report.ok = ok;
  console.log(JSON.stringify(report));
  sidecar.stop();
  app.exit(ok ? 0 : 1);
}
