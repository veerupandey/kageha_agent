#!/usr/bin/env python3
import argparse, json
from kageha.devices import bravia

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="")
    args = p.parse_args()
    h = bravia.resolve_host(args.host)
    if not h:
        print(json.dumps({"ok": False, "error": "No Bravia host"}))
        return 1
    print(json.dumps(bravia.pair_start(h), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
