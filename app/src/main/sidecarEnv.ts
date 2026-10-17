// The environment a packaged sidecar inherits (SEC-07). Pure, so it is unit-tested.

/**
 * Environment variables a packaged sidecar inherits (SEC-07): the platform basics, locale,
 * temp folders and the proxy / CA settings a corporate network needs. Everything else is
 * dropped, so NTNXVISION_DATA_DIR, PYTHON* and similar overrides set in the launching
 * environment cannot steer the frozen core. Unpackaged runs keep the full environment.
 */
const ENV_ALLOW = new Set(
  [
    'PATH', 'HOME', 'USER', 'LOGNAME', 'USERPROFILE', 'USERNAME', 'USERDOMAIN', 'APPDATA', 'LOCALAPPDATA',
    'TEMP', 'TMP', 'TMPDIR', 'LANG', 'LANGUAGE', 'TZ', 'SystemRoot', 'SystemDrive', 'windir', 'ComSpec',
    'PATHEXT', 'ProgramData', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'COMPUTERNAME',
    'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
    'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
  ].map((n) => n.toUpperCase()),
);

export function allowlistedEnv(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(source)) {
    if (v === undefined) continue;
    const up = k.toUpperCase();
    if (ENV_ALLOW.has(up) || up.startsWith('LC_')) out[k] = v;
  }
  return out;
}
