// electron-builder `afterSign` step (SEC-12): give the PyInstaller sidecar its own
// hardened-runtime entitlements (build/entitlements.sidecar.plist) instead of the Electron
// app's. electron-builder signs every nested binary with one inherit plist, so after it has
// signed the app we re-sign the sidecar's Mach-O files with the identity that signed the
// app, then re-seal the outer .app (not deep: the nested signatures stay).
//
// Skips cleanly when not building for macOS or when there is no sidecar. Without a real
// identity (unsigned / ad-hoc local and fork builds) the hardened runtime is not enforced,
// so entitlements have no effect; the app is only sealed ad-hoc so it can launch.

const { Buffer } = require('node:buffer');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const BUILD_DIR = __dirname;
const MACHO_MAGICS = new Set(['feedface', 'feedfacf', 'cefaedfe', 'cffaedfe', 'cafebabe', 'bebafeca']);

function run(cmd, args) {
  const res = spawnSync(cmd, args, { encoding: 'utf-8' });
  return { status: res.status, out: `${res.stdout || ''}${res.stderr || ''}` };
}

function isMachO(file) {
  let fd;
  try {
    fd = fs.openSync(file, 'r');
    const buf = Buffer.alloc(4);
    if (fs.readSync(fd, buf, 0, 4, 0) !== 4) return false;
    return MACHO_MAGICS.has(buf.toString('hex'));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

/** Every regular (non-symlink) Mach-O file under dir. */
function machoFiles(dir) {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) found.push(...machoFiles(full));
    else if (entry.isFile() && isMachO(full)) found.push(full);
  }
  return found;
}

async function signSidecar(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== 'darwin') return;

  const appPath = path.join(appOutDir, `${packager.appInfo.productFilename}.app`);
  const sidecarDir = path.join(appPath, 'Contents', 'Resources', 'sidecar');
  if (!fs.existsSync(sidecarDir)) return;

  const info = run('codesign', ['-dvv', appPath]);
  const identity = /Authority=(Developer ID Application: .+|Apple Development: .+|Mac Developer: .+)/.exec(info.out)?.[1];
  if (info.status !== 0 || /Signature=adhoc/.test(info.out) || !identity) {
    // Flipping Electron fuses rewrites the main executable, which leaves an unsigned build
    // unlaunchable on Apple Silicon (the kernel kills it). Seal it ad-hoc so local and fork
    // builds keep running; a real certificate replaces this in release builds.
    const adhoc = run('codesign', ['--force', '--deep', '--sign', '-', appPath]);
    if (adhoc.status !== 0) throw new Error(`ad-hoc signing failed:\n${adhoc.out}`);
    console.log('  • no signing certificate: app sealed ad-hoc; sidecar entitlements apply to certificate-signed builds only');
    return;
  }

  let keychainArgs = [];
  try {
    const signing = await packager.codeSigningInfo?.value;
    if (signing?.keychainFile) keychainArgs = ['--keychain', signing.keychainFile];
  } catch {
    /* default keychain search list */
  }

  const entitlements = path.join(BUILD_DIR, 'entitlements.sidecar.plist');
  const mainEntitlements = path.join(BUILD_DIR, 'entitlements.mac.plist');
  const files = machoFiles(sidecarDir);
  // The executable last, so libraries and extension modules are sealed first.
  const exe = path.join(sidecarDir, 'ntnxvision-sidecar');
  const ordered = [...files.filter((f) => f !== exe), ...files.filter((f) => f === exe)];
  const common = ['--force', '--timestamp', '--options', 'runtime', '--sign', identity, ...keychainArgs];

  console.log(`  • signing the sidecar (${ordered.length} binaries) with its own entitlements`);
  for (const f of ordered) {
    const args = f === exe ? [...common, '--entitlements', entitlements, f] : [...common, f];
    const res = run('codesign', args);
    if (res.status !== 0) throw new Error(`codesign failed for ${f}:\n${res.out}`);
  }
  const outer = run('codesign', [...common, '--entitlements', mainEntitlements, appPath]);
  if (outer.status !== 0) throw new Error(`re-signing the app failed:\n${outer.out}`);
  const verify = run('codesign', ['--verify', '--deep', '--strict', appPath]);
  if (verify.status !== 0) throw new Error(`the re-signed app does not verify:\n${verify.out}`);
}

exports.default = signSidecar;
