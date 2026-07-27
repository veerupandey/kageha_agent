#!/usr/bin/env python3
import argparse, json
from kageha.devices import bravia

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pin", required=True)
    p.add_argument("--host", default="")
    args = p.parse_args()
    h = bravia.resolve_host(args.host)
    if not h:
        print(json.dumps({"ok": False, "error": "No Bravia host"}))
        return 1
    out = bravia.pair_finish(h, args.pin)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1

if __name__ == "__main__":
    raise SystemExit(main())
