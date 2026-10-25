// electron-builder `afterSign` hook: notarize the signed macOS .app with Apple's notary
// service, driven entirely by environment variables. Skips cleanly (exit 0, one log line)
// when not building for macOS, when credentials are absent, or when the app is unsigned —
// so local and fork/PR builds never need Apple secrets.
//
//   APPLE_ID                      Apple ID e-mail of the Developer Program account
//   APPLE_APP_SPECIFIC_PASSWORD   app-specific password for that Apple ID
//   APPLE_TEAM_ID                 10-character Developer Team ID
//   CSC_LINK (+ CSC_KEY_PASSWORD) Developer ID Application .p12 used to sign (base64 or path)
//
// Built-in electron-builder notarization is disabled (`mac.notarize: false`) because in
// 24.x it needs the team ID hard-coded in package.json; this hook reads it from env.

const path = require('node:path');
const { spawnSync } = require('node:child_process');

exports.default = async function notarizeHook(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== 'darwin') return;

  const { APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID } = process.env;
  if (!APPLE_ID || !APPLE_APP_SPECIFIC_PASSWORD || !APPLE_TEAM_ID) {
    console.log('  • notarization skipped: APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID not all set');
    return;
  }

  const appPath = path.join(appOutDir, `${packager.appInfo.productFilename}.app`);

  // Apple rejects ad-hoc / unsigned bundles; don't burn a notary round-trip on one.
  // `codesign -dv` prints its details on stderr.
  const res = spawnSync('codesign', ['-dv', '--verbose=2', appPath], { encoding: 'utf-8' });
  const details = `${res.stdout || ''}${res.stderr || ''}`;
  if (res.status !== 0 || /Signature=adhoc/.test(details) || !/Authority=Developer ID Application/.test(details)) {
    console.log('  • notarization skipped: app is not signed with a Developer ID Application certificate');
    return;
  }

  // @electron/notarize 3.x is ESM-only, so it is imported rather than required.
  const { notarize } = await import('@electron/notarize');
  console.log(`  • notarizing ${appPath} (team ${APPLE_TEAM_ID}); this can take several minutes`);
  await notarize({
    appPath,
    appleId: APPLE_ID,
    appleIdPassword: APPLE_APP_SPECIFIC_PASSWORD,
    teamId: APPLE_TEAM_ID,
  });
  console.log('  • notarization complete (ticket stapled)');
};
