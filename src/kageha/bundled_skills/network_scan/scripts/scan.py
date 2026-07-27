#!/usr/bin/env python3
import argparse, json
from kageha.devices.network_scan import scan_lan

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--focus", default="all", choices=("all", "tv", "quick"))
    args = p.parse_args()
    print(json.dumps(scan_lan(focus=args.focus), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
