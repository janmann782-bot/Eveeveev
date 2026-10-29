"""Scan tracked files; report paths only, never matched credential values."""
import os
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = (rb"sk-(?:xt-|or-v1-)[A-Za-z0-9_-]{20,}",
            rb"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b")


def scan():
    paths=subprocess.check_output(["git","ls-files","-z"]).split(b"\0")
    found=[]
    configured=[os.getenv(k,"").encode() for k in ("BOT_TOKEN","XKIRO_API_KEY") if os.getenv(k)]
    for name in paths:
        if not name:
            continue
        p=Path(os.fsdecode(name))
        if not p.is_file():
            continue
        data=p.read_bytes()
        if any(re.search(pattern,data) for pattern in PATTERNS) or any(k in data for k in configured):
            found.append(str(p))
    if found:
        print("Potential credentials in:",", ".join(found))
        return 1
    print("Tracked credential scan passed")
    return 0


if __name__=="__main__":
    sys.exit(scan())
