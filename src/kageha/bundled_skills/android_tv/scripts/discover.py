#!/usr/bin/env python3
import json
from kageha.devices.android_tv import discover_tv_candidates

def main() -> int:
    print(json.dumps(discover_tv_candidates(), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
