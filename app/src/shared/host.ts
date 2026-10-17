// Host-name check shared by the main process (IPC validation) and the Targets editor.

/**
 * A hostname, IPv4 or bracket-free IPv6 literal: no scheme, path, port, query, fragment,
 * userinfo or spaces. IPv6 is accepted bare ("fd00::1"); the Python core adds the brackets
 * when it builds a URL. Underscores are allowed inside labels (seen in lab DNS names).
 */
export function isPlainHost(host: string): boolean {
  if (!host || host.length > 253) return false;
  if (/^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?)*$/.test(host)) {
    return true;
  }
  return /^[0-9A-Fa-f:.]+$/.test(host) && host.includes(':');
}
