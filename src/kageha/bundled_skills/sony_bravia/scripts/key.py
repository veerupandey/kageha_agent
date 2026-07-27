#!/usr/bin/env python3
import argparse, json
from kageha.devices import bravia

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True)
    p.add_argument("--host", default="")
    args = p.parse_args()
    out = bravia.send_key(args.key, args.host)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1

if __name__ == "__main__":
    raise SystemExit(main())
