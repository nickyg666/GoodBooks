
import json, subprocess
from pathlib import Path
rows=json.loads(subprocess.run(["curl","-s","http://127.0.0.1:5000/api/audiobooks"],capture_output=True,text=True).stdout)["audiobooks"]
meta=json.load(open("data/library_metadata.json"))
for r in rows:
    res=r.get("result") or ""
    stem=Path(res).name.split("__")[0] if res else (r.get("title") or "")[:40]
    hits=[k for k,v in meta.items() if stem[:28].lower() in (v.get("title") or "").lower()]
    cov=[meta[h].get("cover") for h in hits if meta[h].get("cover")]
    print(f'  {r["title"][:46]:46} cover={"YES" if cov else "NO "}  {str(cov[0])[:66] if cov else ""}')
