// OS keychain access through @napi-rs/keyring (keyring-rs), keeping the entries keytar wrote.
// Pinned to 1.2.0: 1.3.0 to 2.1.1 store an empty blob on Windows (the writing Entry caches
// the value, so a same-object round trip passes while every later read is empty). The
// packaged --smoke round-trips through fresh entries to catch that.
// Kept free of Electron imports so it runs under plain Node in the tests.
//
// keytar (archived 2022) wrote, for (service, account):
// - macOS: a generic password, service + account, password as UTF-8. keyring-rs uses the
//   same item shape, so the plain Entry(service, account) reads and writes them.
// - Windows: a generic credential with TargetName "service/account", UserName = account and
//   the password as raw UTF-8 bytes. keyring-rs would default to TargetName "account.service"
//   and a UTF-16 blob, which cannot read those. So on Windows the keytar layout is used
//   explicitly (Entry.withTarget + the raw secret), and saved passwords keep working with no
//   migration and no re-entry (and a downgrade to a keytar build would still work).
// - Linux (unsupported, dev only): Secret Service with keytar's attributes; the plain Entry.

export interface KeyringEntry {
  getPassword(): string | null | undefined;
  setPassword(password: string): void;
  getSecret(): ArrayLike<number> | null | undefined;
  setSecret(secret: Uint8Array): void;
  deletePassword(): unknown;
}

export interface KeyringModule {
  Entry: {
    new (service: string, username: string): KeyringEntry;
    withTarget(target: string, service: string, username: string): KeyringEntry;
  };
}

export interface Keychain {
  get(account: string): string | null;
  set(account: string, secret: string): void;
  delete(account: string): void;
}

export function makeKeychain(mod: KeyringModule, service: string, platform: string = process.platform): Keychain {
  const entry = (account: string): KeyringEntry =>
    platform === 'win32' ? mod.Entry.withTarget(`${service}/${account}`, service, account) : new mod.Entry(service, account);
  return {
    get(account) {
      const e = entry(account);
      if (platform !== 'win32') return e.getPassword() ?? null;
      const raw = e.getSecret();
      return raw ? Buffer.from(Array.from(raw)).toString('utf-8') : null;
    },
    set(account, secret) {
      const e = entry(account);
      if (platform === 'win32') e.setSecret(Buffer.from(secret, 'utf-8'));
      else e.setPassword(secret);
    },
    delete(account) {
      entry(account).deletePassword();
    },
  };
}
