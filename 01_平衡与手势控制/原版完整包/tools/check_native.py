"""Read-only requalification of one frozen ADB1 native build; never launches tools."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    # Use the exact executed driver and its bound sibling helpers. The copied
    # driver has no actions on import; accept() only rereads local evidence.
    import sys
    sys.path.insert(0, str(build))
    try:
        record = json.loads((build/'RESULT.json').read_text(encoding='utf-8'))
        driver = build/'EXECUTED_BUILD_DRIVER.py'
        import hashlib
        if hashlib.sha256(driver.read_bytes()).hexdigest() != record['driver_sha256']:
            raise ValueError('Executed driver hash changed')
        spec = importlib.util.spec_from_file_location('executed_native_driver', driver)
        if spec is None or spec.loader is None:
            raise ValueError('Cannot load executed driver')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The executed copy lives in the native stage, while the numerical
        # baseline belongs to this task's review directory.
        module.ROOT = Path(__file__).resolve().parents[1]
        result = module.accept(build, record)
        print(json.dumps(dict(recheck='PASS', **result), indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps(dict(recheck='FAIL', accepted=False, reason=str(error)), indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
