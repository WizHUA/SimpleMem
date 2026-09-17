"""Run the real host validator; never substitute the isolated test SDK shim."""

import argparse
import importlib
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", required=True, type=Path)
    args = parser.parse_args()
    root = args.host_root.resolve()
    api_path = root / "server/engines/memory_plugin_api.py"
    if not api_path.is_file():
        print(json.dumps({"status": "blocked", "reason": "real host SDK missing", "path": str(api_path)}))
        return 2
    sys.path.insert(0, str(root))
    try:
        sdk = importlib.import_module("server.engines.memory_plugin_api")
        loaded = Path(sdk.__file__).resolve()
        if loaded != api_path:
            raise RuntimeError("Imported SDK does not belong to the requested host")
        bridge = importlib.import_module("agent_memory.integrations.rag_sdk")
        violations = sdk.validate_plugin(bridge.engine_plugin)
    except Exception as error:  # noqa: BLE001 - command boundary reports host import/validation failures
        print(json.dumps({"status": "error", "error_type": type(error).__name__}))
        return 1
    print(
        json.dumps(
            {
                "status": "passed" if not violations else "failed",
                "host_sdk": str(loaded),
                "violations": violations,
                "scope": "contract only; registration, authenticated HTTP and SSE remain separate gates",
            },
            ensure_ascii=False,
        )
    )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
