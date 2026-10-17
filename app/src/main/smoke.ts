// Packaging smoke test: `NTNXVision --smoke` (or NTNXVISION_SMOKE=1) starts the main
// process, checks the native keyring module loads, pings the Python sidecar through the
// normal SidecarClient, prints one JSON line and exits — 0 on success, 1 on failure.
// No window is created. Used by CI against the packaged app and by scripts/build-*.

import { app } from 'electron';
import type { SidecarClient } from './sidecar';

const TIMEOUT_MS = 60_000;

export function isSmokeRun(): boolean {
  return process.argv.includes('--smoke') || process.env.NTNXVISION_SMOKE === '1';
}

export async function runSmoke(sidecar: SidecarClient): Promise<void> {
  const report: Record<string, unknown> = {
    smoke: 'ntnxvision',
    version: app.getVersion(),
    packaged: app.isPackaged,
    platform: `${process.platform}-${process.arch}`,
  };
  let ok = true;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    require('@napi-rs/keyring');
    report.keyring = 'ok';
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
  report.ok = ok;
  console.log(JSON.stringify(report));
  sidecar.stop();
  app.exit(ok ? 0 : 1);
}
