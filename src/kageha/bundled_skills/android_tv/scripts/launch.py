#!/usr/bin/env python3
import argparse
import asyncio
import json

from kageha.devices import android_tv


async def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--package", default="")
    p.add_argument("--name", default="")
    args = p.parse_args()
    out = await android_tv.launch_package(package=args.package, name=args.name)
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
