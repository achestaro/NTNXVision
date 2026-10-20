// electron-builder `afterPack` hook (runs before signing): give the macOS app its Icon Composer
// icon. build/Assets.car is compiled from build/AppIcon.icon by `npm run icons` (actool, Xcode
// 26+) and committed, so packaging needs no Xcode. CFBundleIconName makes macOS 26+ draw it
// natively (Liquid Glass, dark, tinted, no grey "legacy icon" tile); older macOS keeps using
// CFBundleIconFile (build/icon.icns, drawn on Apple's icon grid).

const { execFileSync } = require('node:child_process');
const { copyFileSync, existsSync } = require('node:fs');
const path = require('node:path');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin' && context.electronPlatformName !== 'mas') return;
  const car = path.join(__dirname, 'Assets.car');
  if (!existsSync(car)) throw new Error('build/Assets.car is missing: run `npm run icons` on macOS with Xcode 26+.');
  const contents = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents');
  copyFileSync(car, path.join(contents, 'Resources', 'Assets.car'));
  execFileSync('plutil', ['-replace', 'CFBundleIconName', '-string', 'AppIcon', path.join(contents, 'Info.plist')]);
};
