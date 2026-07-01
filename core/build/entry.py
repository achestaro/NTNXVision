"""PyInstaller entry point — runs the sidecar directly (no CLI arg parsing needed)."""

from ntnxvision_core.sidecar import serve_stdio

if __name__ == "__main__":
    serve_stdio()
