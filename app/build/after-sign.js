// electron-builder `afterSign` hook: sidecar entitlements first (re-signs the sidecar and
// re-seals the app), then notarization of the final signed app.

const signSidecar = require('./sign-sidecar').default;
const notarizeHook = require('./notarize').default;

exports.default = async function afterSign(context) {
  await signSidecar(context);
  await notarizeHook(context);
};
