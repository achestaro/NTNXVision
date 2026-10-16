"""PyInstaller entry point — runs the sidecar directly (no CLI arg parsing needed).

``--demo`` (or NTNXVISION_DEMO=1 in the environment) runs it against the bundled synthetic
Nutanix estate instead of the network; see ntnxvision_core.synthetic.demo.
"""

import os
import sys

if __name__ == "__main__":
    if "--demo" in sys.argv[1:]:
        os.environ["NTNXVISION_DEMO"] = "1"
    from ntnxvision_core.sidecar import serve_stdio

    serve_stdio()
