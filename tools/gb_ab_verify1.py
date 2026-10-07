#!/usr/bin/env python3
"""Verify the cover/entry_id patch compiles, restart, and probe the endpoint."""
import json
import subprocess

def sh(cmd, timeout=150):
    r = subprocess.run(["sshpass", "-p", "1", "ssh", "-o", "StrictHostKeyChecking=no",
                        "-o", "LogLevel=ERROR", "das@192.168.0.9", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout or r.stderr

print(sh("cd /usr/local/bin/GoodBooks && python3 -m py_compile plugins/audiobook/__init__.py && echo COMPILE_OK"))
print(sh("sudo -n systemctl restart GoodBooks && sleep 6 && systemctl is-active GoodBooks"))

probe = sh("curl -s --max-time 20 http://127.0.0.1:5000/api/audiobooks")
try:
    d = json.loads(probe)
    for r in d["audiobooks"]:
        t = r.get("title", "")[:40]
        print(f"  {t:40} cover={str(bool(r.get('cover'))):5} "
              f"entry={str(bool(r.get('entry_id'))):5} "
              f"cover_val={str(r.get('cover'))[:48]}")
except Exception as e:
    print("  probe not json:", e, probe[:150])
