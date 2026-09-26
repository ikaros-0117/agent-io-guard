"""Run private LiteLLM and the public Chat-only ingress as one local deployment."""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--backend-port", type=int, default=4001)
    args = parser.parse_args()
    if args.port == args.backend_port:
        parser.error("public and backend ports must be different")
    litellm_bin = shutil.which("litellm") or str(Path(sys.executable).with_name("litellm"))
    if not Path(litellm_bin).is_file():
        parser.error("litellm console script not found on PATH")
    backend = subprocess.Popen([
        litellm_bin, "--config", args.config,
        "--host", "127.0.0.1", "--port", str(args.backend_port),
    ])
    gateway = None
    def _shutdown(_signal: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown)
    try:
        environment = {**os.environ, "LITELLM_BACKEND_URL": f"http://127.0.0.1:{args.backend_port}"}
        gateway = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "chat_only_gateway:app",
            "--host", args.host, "--port", str(args.port),
        ], cwd=Path(__file__).resolve().parent, env=environment)
        while True:
            if backend.poll() is not None:
                return backend.returncode or 1
            if gateway.poll() is not None:
                return gateway.returncode or 1
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        for process in (gateway, backend):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (gateway, backend):
            if process is not None:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    sys.exit(main())
