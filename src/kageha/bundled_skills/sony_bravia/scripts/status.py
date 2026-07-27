#!/usr/bin/env python3
import argparse, json
from kageha.devices import bravia

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="")
    args = p.parse_args()
    print(json.dumps(bravia.status_report(args.host), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
