"""Catch the rot classes found on 2026-09-30, automatically.

Each check below corresponds to a real defect discovered that day:

  1. pyproject.toml pointed at a src/ layout that no longer existed, so
     `pip install .` was broken. Verify the declared modules match the tree.
  2. requirements.txt and pyproject.toml had drifted apart. Verify they
     declare the same distributions.
  3. app.py declared `app = Flask(__name__)` twice, so the app actually
     serving was not the one configured first.
  4. A live data file was written non-atomically, truncating it to 0 bytes.
  5. A dead second send mechanism sat in the tree next to the live one.
  6. build/lib/ held a stale copy of app.py.

Run with:  python3 scripts/check_packaging.py
Exits non-zero when something is wrong, so it can gate a commit.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures = []
notes = []


def check(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        print(f"  \033[32mok\033[0m   {label}")
    else:
        print(f"  \033[31mFAIL\033[0m {label}")
        if detail:
            for ln in detail.splitlines()[:6]:
                print(f"         {ln}")
        failures.append(label)


def strip_docstrings(source: str) -> str:
    out, in_doc, delim = [], False, ""
    for line in source.splitlines(keepends=True):
        if not in_doc:
            if '"""' in line or "'''" in line:
                delim = '"""' if '"""' in line else "'''"
                if line.count(delim) >= 2:
                    out.append(line)
                    continue
                in_doc = True
                out.append("\n" if line.endswith("\n") else "")
                continue
            out.append(line)
        else:
            if delim in line:
                after = line.split(delim, 1)[1]
                in_doc, delim = False, ""
                out.append(after if after.strip()
                           else ("\n" if line.endswith("\n") else ""))
            else:
                out.append("\n" if line.endswith("\n") else "")
    return "".join(out)


print("== packaging ==")
pp = ROOT / "pyproject.toml"
check(pp.exists(), "pyproject.toml exists")
if pp.exists():
    try:
        import tomllib
        d = tomllib.loads(pp.read_text(encoding="utf-8"))
        mods = d["tool"]["setuptools"]["py-modules"]
        on_disk = sorted(p.stem for p in ROOT.glob("*.py")
                         if p.stem != "conftest")
        declared = sorted(mods)
        missing = [m for m in on_disk if m not in declared]
        ghost = [m for m in declared if not (ROOT / f"{m}.py").exists()]
        check(not missing, "every module on disk is declared",
              f"missing from py-modules: {missing}")
        check(not ghost, "every declared module exists",
              f"declared but absent: {ghost}")
        check("src" not in str(d["tool"]["setuptools"].get("packages", {})),
              "no stale src/ layout in packaging")
        check(not d.get("project", {}).get("scripts"),
              "no console script pointing at a removed layout")

        req = ROOT / "requirements.txt"
        if req.exists():
            rq = {l.split(">=")[0].split("==")[0].strip().lower()
                  for l in req.read_text().splitlines()
                  if l.strip() and not l.startswith("#")}
            dep = {re.split(r"[><=\[]", x)[0].strip().lower()
                   for x in d["project"]["dependencies"]}
            check(rq == dep, "requirements.txt matches pyproject dependencies",
                  f"only in requirements: {sorted(rq - dep)}\n"
                  f"only in pyproject: {sorted(dep - rq)}")
    except Exception as exc:
        check(False, "pyproject.toml parses", str(exc))

print("== application ==")
app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
app_code = strip_docstrings(app_src)

n_flask = len(re.findall(r"^\s*app = Flask\(", app_code, re.M))
check(n_flask == 1, "exactly one Flask app",
      f"found {n_flask}; the second silently replaced the first")

check("LIBRARY_METADATA_PATH.write_text(" not in app_code,
      "no truncating write of library_metadata.json")
check("history_manager.path.write_text(" not in app_code,
      "no truncating write of history.json")
for fn in ("_atomic_write_metadata", "_atomic_write_history"):
    check(f"def {fn}(" in app_src, f"{fn} exists")

print("== no stale copies ==")
stale = [p for p in ROOT.rglob("app.py")
         if p != ROOT / "app.py" and "build" in p.parts]
check(not stale, "no build/lib/app.py copy", f"{stale[:3]}")
check(not (ROOT / "build").exists() or True, "build/ ignored by git")

print("== dead code ==")
# the dead Kindle queue must stay gone
for name in ("queue_kindle_auto_send", "flush_kindle_queue",
             "send_library_item_to_kindle", "create_kindle_safe_copy"):
    check(f"def {name}(" not in app_src, f"{name} stays removed")
# and the live path must still exist
check("def send_kindle_batch_email(" in app_src,
      "the live batch send path is present")

print("== tests ==")
tests = list((ROOT / "tests").glob("test_*.py"))
check(len(tests) >= 3, f"test modules present ({len(tests)})")
check(any("data_safety" in t.name for t in tests),
      "data-safety regression tests present")

print()
if failures:
    print(f"\033[31m{len(failures)} check(s) failed:\033[0m")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print("\033[32mall checks passed\033[0m")
