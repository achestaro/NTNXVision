"""PyInstaller entry point — runs the sidecar directly (no CLI arg parsing needed).

``--tls-check`` prints the verified-TLS trust store summary and exits (0 when it is
usable): the packaging smoke test runs it.

``--demo`` (or NTNXVISION_DEMO=1 in the environment) runs it against the bundled synthetic
Nutanix estate instead of the network; see ntnxvision_core.synthetic.demo.
"""

import os
import sys

if __name__ == "__main__":
    if "--tls-check" in sys.argv[1:]:
        # Packaging self-check (no network): verified TLS has a non-empty trust store.
        import json

        from ntnxvision_core.tlsctx import trust_summary

        summary = trust_summary()
        print(json.dumps(summary))
        sys.exit(0 if summary["ok"] else 1)
    if "--demo" in sys.argv[1:]:
        os.environ["NTNXVISION_DEMO"] = "1"
    from ntnxvision_core.sidecar import serve_stdio

    serve_stdio()
