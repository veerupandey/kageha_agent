#!/usr/bin/env python3
import argparse
import asyncio
import json

from kageha.devices import android_tv


async def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True)
    args = p.parse_args()
    out = await android_tv.send_key(args.key)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
