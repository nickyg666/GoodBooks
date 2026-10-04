#!/usr/bin/env python3
"""Hunt for the same CLASS of defect across the whole service.

The bugs that keep recurring here are all structural, not logic:

  1. DUPLICATED BLOCKS from scripted edits that anchored on a string still
     present in the already-patched file.
  2. ORDERING: a name used before it is assigned at module scope (PLUGINS).
  3. DEAD ENTRY POINTS: more than one __main__, unreachable functions.
  4. API CALLS THAT CANNOT WORK: a function called but never defined.
  5. JINJA IMBALANCE after template surgery.
  6. ROUTES DECLARED BUT SHADOWED or duplicated.

This checks each of those mechanically over app.py, the templates and the
plugin, so the next one is found by a command rather than by accident.
"""
import ast
import collections
import re
import sys
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
APP = ROOT / "app.py"
TPL = ROOT / "templates" / "library.html"
PLUGIN = ROOT / "plugins" / "audiobook" / "__init__.py"

app_src = APP.read_text()
app_tree = ast.parse(app_src)
tpl_src = TPL.read_text()

problems = []
info = []


def note(bucket, msg):
    problems.append((bucket, msg))


# ---------------------------------------------------------------- 1. dupes
def dupe_defs(tree):
    """Top-level names defined more than once (the scripted-edit signature)."""
    names = collections.Counter()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[n.name] += 1
    return {k: v for k, v in names.items() if v > 1}


def dupe_routes(tree):
    """Same URL+method registered twice: Flask silently keeps the first."""
    seen = collections.Counter()
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
                    and d.func.attr in ("route", "post", "get", "delete",
                                        "put", "patch"):
                path = None
                methods = None
                if d.args and isinstance(d.args[0], ast.Constant):
                    path = str(d.args[0].value)
                for kw in d.keywords:
                    if kw.arg == "methods":
                        try:
                            methods = tuple(sorted(
                                ast.literal_eval(kw.value)))
                        except Exception:
                            methods = None
                key = (d.func.attr, path, methods)
                seen[key] += 1
    return {k: v for k, v in seen.items() if v > 1 and k[1]}


def dupe_log_lines(tree):
    """The same log message emitted from two branches of one function."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        counts = collections.Counter()
        for sub in ast.walk(n):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in ("info", "warning", "error", "debug")
                    and sub.args and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)):
                counts[sub.args[0].value] += 1
        for msg, c in counts.items():
            if c > 1 and msg.strip():
                out.append((n.name, msg, c))
    return out


dup_defs = dupe_defs(app_tree)
if dup_defs:
    note("duplicate top-level def", str(dup_defs))

dup_routes = dupe_routes(app_tree)
for key, c in dup_routes.items():
    note("duplicate route", f"{key} x{c}")

dup_logs = dupe_log_lines(app_tree)
for fn, msg, c in dup_logs:
    info.append(f"    log message repeated {c}x in {fn}: {msg[:58]!r}")

# ---------------------------------------------------- 2. ordering at module scope
module_assigns = {}
for n in app_tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                module_assigns.setdefault(t.id, n.lineno)

# A module-level global read that sits ABOVE its assignment is only a hazard
# if the reader can run DURING import. Request handlers and
# @app.context_processor functions run long after the module finishes, so
# inject_settings() reading settings_manager at line 198 (assigned at 211) is
# harmless -- verified: it is a context processor.
#
# The real hazard is the maintenance thread: it starts at a module-level
# assignment and its first tick can reach code before later module lines run.
# That is exactly what broke PLUGINS and the log-rotation constants.
IMPORT_TIME_READERS = {
    "_background_maintenance_worker",
    "_run_maintenance_cycle",
    "_advance_audiobook_job",
    "_enforce_plugin_state",
    "start_background_maintenance_thread",
}

for name, declared in sorted(module_assigns.items(),
                             key=lambda kv: kv[1]):
    for n in ast.walk(app_tree):
        if isinstance(n, ast.FunctionDef) and n.name in IMPORT_TIME_READERS:
            for sub in ast.walk(n):
                if isinstance(sub, ast.Name) and sub.id == name \
                        and isinstance(sub.ctx, ast.Load):
                    if sub.lineno < declared:
                        note("module ordering",
                             f"{name} read in {n.name}() at line "
                             f"{sub.lineno} but assigned at line {declared}")
                    break

# ---------------------------------------------------- 3. entry points / dead code
guards = [i + 1 for i, ln in enumerate(app_src.split("\n"))
          if ln.startswith("if __name__")]
if len(guards) != 1:
    note("entry points", f"{len(guards)} __main__ guards at lines {guards}")

# a Thread created at module level can run before later module-level code
# A thread started at module level can run before later module lines, so
# anything it REACHES must already exist. What it reaches is the maintenance
# path, not the whole file -- so the rule is: does the maintenance cycle or
# the plugin hook path read this name?
#
# Counting every later assignment produced false positives: _AUDIOBOOK_EXTS
# and REFERENCE_DIR are read only by audiobook_index, a REQUEST handler that
# runs long after import, so neither can race the thread. Verified by
# scripts/gb_check_remaining.py.
MAINTENANCE_REACHERS = ("_background_maintenance_worker", "_run_maintenance_cycle",
                        "_advance_audiobook_job", "_enforce_plugin_state",
                        "start_background_maintenance_thread")
thread_lines = [(tid, n.lineno) for n in app_tree.body
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name) and "THREAD" in t.id
                for tid in [t.id]]
reacher_reads = set()
for _n in ast.walk(app_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name in MAINTENANCE_REACHERS:
        for _x in ast.walk(_n):
            if isinstance(_x, ast.Name) and isinstance(_x.ctx, ast.Load):
                reacher_reads.add(_x.id)
for tid, tline in thread_lines:
    for name, dline in module_assigns.items():
        if dline > tline and name in reacher_reads:
            note("thread ordering",
                 f"{tid} starts at line {tline} but the maintenance path "
                 f"reads {name}, assigned at {dline}")

# ---------------------------------------------------- 4. undefined calls
def called_names(tree):
    out = collections.Counter()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out[n.func.id] += 1
    return out


defined = {n.name for n in ast.walk(app_tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
for n in ast.walk(app_tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(n.name)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names:
            defined.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
        defined.add(n.id)
    elif isinstance(n, ast.arg):
        defined.add(n.arg)
    elif isinstance(n, ast.ExceptHandler) and n.name:
        defined.add(n.name)
    elif isinstance(n, ast.alias):
        defined.add((n.asname or n.name).split(".")[0])
import builtins
defined |= set(dir(builtins))

for name, c in called_names(app_tree).items():
    if name.startswith("__"):
        continue
    if name not in defined:
        note("possibly undefined call", f"{name}() called {c}x")

# the plugin must not import the host
for tree, label in ((ast.parse(PLUGIN.read_text()), "plugin"),):
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "app":
                    note("plugin safety", "plugin imports app")
        elif isinstance(n, ast.ImportFrom) and (n.module or "") == "app":
            note("plugin safety", "plugin does 'from app import ...'")

# ---------------------------------------------------- 5. jinja balance
for label, src in (("library.html", tpl_src),):
    o = len(re.findall(r"{%-?\s*if\b", src))
    e = src.count("{% endif")
    of = len(re.findall(r"{%-?\s*for\b", src))
    ef = src.count("{% endfor")
    ob = src.count("{% block")
    eb = src.count("{% endblock")
    info.append(f"    {label}: if {o}/{e}  for {of}/{ef}  block {ob}/{eb}")
    if o != e:
        note("jinja balance", f"{label}: {o} if vs {e} endif")
    if of != ef:
        note("jinja balance", f"{label}: {of} for vs {ef} endfor")

# ---------------------------------------------------- 6. unreachable defs
called = called_names(app_tree)
never = sorted(n for n in
               (m.name for m in ast.walk(app_tree)
                if isinstance(m, ast.FunctionDef)
                and m.name.startswith("_"))
               if n not in called)
if never:
    info.append(f"    private functions never called: {never}")

# ---------------------------------------------------- report
print("=== audit ===")
for line in info:
    print(line)
print()
if not problems:
    print("NO STRUCTURAL PROBLEMS FOUND")
else:
    by = collections.defaultdict(list)
    for bucket, msg in problems:
        by[bucket].append(msg)
    for bucket in sorted(by):
        print(f"[{bucket}]")
        for m in by[bucket][:12]:
            print(f"   - {m}")
        if len(by[bucket]) > 12:
            print(f"   ... {len(by[bucket])-12} more")
    print()
    print(f"TOTAL: {len(problems)}")
sys.exit(1 if problems else 0)# Same reasoning: a thread started at module level can run before later
# module lines, so what matters is whether the MAINTENANCE PATH can reach the
# name -- not whether some later assignment exists at all.
#
# Counting every later assignment produced two false positives:
# _AUDIOBOOK_EXTS and REFERENCE_DIR are read only by audiobook_index, a
# request handler, so neither can race the thread. Verified by
# scripts/gb_check_remaining.py; REFERENCE_DIR turned out to be genuinely
# dead and was removed.
MAINTENANCE_REACHERS = ("_background_maintenance_worker",
                        "_run_maintenance_cycle",
                        "_advance_audiobook_job",
                        "_enforce_plugin_state",
                        "start_background_maintenance_thread")
thread_lines = []
for n in app_tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name) and "THREAD" in t.id:
                thread_lines.append((t.id, n.lineno))
reacher_reads = set()
for fn in ast.walk(app_tree):
    if isinstance(fn, ast.FunctionDef) and fn.name in MAINTENANCE_REACHERS:
        for x in ast.walk(fn):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                reacher_reads.add(x.id)
for tid, tline in thread_lines:
    for name, dline in module_assigns.items():
        if dline > tline and name in reacher_reads:
            note("thread ordering",
                 f"{tid} starts at line {tline} but the maintenance path "
                 f"reads {name}, assigned at {dline}")

# --------------------------------------------------------------------------- 

ROOT = Path("/usr/local/bin/GoodBooks")
APP = ROOT / "app.py"
TPL = ROOT / "templates" / "library.html"
PLUGIN = ROOT / "plugins" / "audiobook" / "__init__.py"

app_src = APP.read_text()
app_tree = ast.parse(app_src)
tpl_src = TPL.read_text()

problems = []
info = []


def note(bucket, msg):
    problems.append((bucket, msg))


# ---------------------------------------------------------------- 1. dupes
def dupe_defs(tree):
    """Top-level names defined more than once (the scripted-edit signature)."""
    names = collections.Counter()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[n.name] += 1
    return {k: v for k, v in names.items() if v > 1}


def dupe_routes(tree):
    """Same URL+method registered twice: Flask silently keeps the first."""
    seen = collections.Counter()
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) \
                    and d.func.attr in ("route", "post", "get", "delete",
                                        "put", "patch"):
                path = None
                methods = None
                if d.args and isinstance(d.args[0], ast.Constant):
                    path = str(d.args[0].value)
                for kw in d.keywords:
                    if kw.arg == "methods":
                        try:
                            methods = tuple(sorted(
                                ast.literal_eval(kw.value)))
                        except Exception:
                            methods = None
                key = (d.func.attr, path, methods)
                seen[key] += 1
    return {k: v for k, v in seen.items() if v > 1 and k[1]}


def dupe_log_lines(tree):
    """The same log message emitted from two branches of one function."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        counts = collections.Counter()
        for sub in ast.walk(n):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in ("info", "warning", "error", "debug")
                    and sub.args and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)):
                counts[sub.args[0].value] += 1
        for msg, c in counts.items():
            if c > 1 and msg.strip():
                out.append((n.name, msg, c))
    return out


dup_defs = dupe_defs(app_tree)
if dup_defs:
    note("duplicate top-level def", str(dup_defs))

dup_routes = dupe_routes(app_tree)
for key, c in dup_routes.items():
    note("duplicate route", f"{key} x{c}")

dup_logs = dupe_log_lines(app_tree)
for fn, msg, c in dup_logs:
    info.append(f"    log message repeated {c}x in {fn}: {msg[:58]!r}")

# ---------------------------------------------------- 2. ordering at module scope
module_assigns = {}
for n in app_tree.body:
    if isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                module_assigns.setdefault(t.id, n.lineno)

# A module-level global read that sits ABOVE its assignment is only a hazard
# if the reader can run DURING import. Request handlers and
# @app.context_processor functions run long after the module finishes, so
# inject_settings() reading settings_manager at line 198 (assigned at 211) is
# harmless -- verified: it is a context processor.
#
# The real hazard is the maintenance thread: it starts at a module-level
# assignment and its first tick can reach code before later module lines run.
# That is exactly what broke PLUGINS and the log-rotation constants.
IMPORT_TIME_READERS = {
    "_background_maintenance_worker",
    "_run_maintenance_cycle",
    "_advance_audiobook_job",
    "_enforce_plugin_state",
    "start_background_maintenance_thread",
}

for name, declared in sorted(module_assigns.items(),
                             key=lambda kv: kv[1]):
    for n in ast.walk(app_tree):
        if isinstance(n, ast.FunctionDef) and n.name in IMPORT_TIME_READERS:
            for sub in ast.walk(n):
                if isinstance(sub, ast.Name) and sub.id == name \
                        and isinstance(sub.ctx, ast.Load):
                    if sub.lineno < declared:
                        note("module ordering",
                             f"{name} read in {n.name}() at line "
                             f"{sub.lineno} but assigned at line {declared}")
                    break

# ---------------------------------------------------- 3. entry points / dead code
guards = [i + 1 for i, ln in enumerate(app_src.split("\n"))
          if ln.startswith("if __name__")]
if len(guards) != 1:
    note("entry points", f"{len(guards)} __main__ guards at lines {guards}")

# a Thread created at module level can run before later module-level code
# A thread started at module level can run before later module lines, so
# anything it REACHES must already exist. What it reaches is the maintenance
# path, not the whole file -- so the rule is: does the maintenance cycle or
# the plugin hook path read this name?
#
# Counting every later assignment produced false positives: _AUDIOBOOK_EXTS
# and REFERENCE_DIR are read only by audiobook_index, a REQUEST handler that
# runs long after import, so neither can race the thread. Verified by
# scripts/gb_check_remaining.py.
MAINTENANCE_REACHERS = ("_background_maintenance_worker", "_run_maintenance_cycle",
                        "_advance_audiobook_job", "_enforce_plugin_state",
                        "start_background_maintenance_thread")
thread_lines = [(tid, n.lineno) for n in app_tree.body
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name) and "THREAD" in t.id
                for tid in [t.id]]
reacher_reads = set()
for _n in ast.walk(app_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name in MAINTENANCE_REACHERS:
        for _x in ast.walk(_n):
            if isinstance(_x, ast.Name) and isinstance(_x.ctx, ast.Load):
                reacher_reads.add(_x.id)
for tid, tline in thread_lines:
    for name, dline in module_assigns.items():
        if dline > tline and name in reacher_reads:
            note("thread ordering",
                 f"{tid} starts at line {tline} but the maintenance path "
                 f"reads {name}, assigned at {dline}")

# ---------------------------------------------------- 4. undefined calls
def called_names(tree):
    out = collections.Counter()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out[n.func.id] += 1
    return out


defined = {n.name for n in ast.walk(app_tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
for n in ast.walk(app_tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(n.name)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names:
            defined.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
        defined.add(n.id)
    elif isinstance(n, ast.arg):
        defined.add(n.arg)
    elif isinstance(n, ast.ExceptHandler) and n.name:
        defined.add(n.name)
    elif isinstance(n, ast.alias):
        defined.add((n.asname or n.name).split(".")[0])
import builtins
defined |= set(dir(builtins))

for name, c in called_names(app_tree).items():
    if name.startswith("__"):
        continue
    if name not in defined:
        note("possibly undefined call", f"{name}() called {c}x")

# the plugin must not import the host
for tree, label in ((ast.parse(PLUGIN.read_text()), "plugin"),):
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "app":
                    note("plugin safety", "plugin imports app")
        elif isinstance(n, ast.ImportFrom) and (n.module or "") == "app":
            note("plugin safety", "plugin does 'from app import ...'")

# ---------------------------------------------------- 5. jinja balance
for label, src in (("library.html", tpl_src),):
    o = len(re.findall(r"{%-?\s*if\b", src))
    e = src.count("{% endif")
    of = len(re.findall(r"{%-?\s*for\b", src))
    ef = src.count("{% endfor")
    ob = src.count("{% block")
    eb = src.count("{% endblock")
    info.append(f"    {label}: if {o}/{e}  for {of}/{ef}  block {ob}/{eb}")
    if o != e:
        note("jinja balance", f"{label}: {o} if vs {e} endif")
    if of != ef:
        note("jinja balance", f"{label}: {of} for vs {ef} endfor")

# ---------------------------------------------------- 6. unreachable defs
called = called_names(app_tree)
never = sorted(n for n in
               (m.name for m in ast.walk(app_tree)
                if isinstance(m, ast.FunctionDef)
                and m.name.startswith("_"))
               if n not in called)
if never:
    info.append(f"    private functions never called: {never}")

# ---------------------------------------------------- report
print("=== audit ===")
for line in info:
    print(line)
print()
if not problems:
    print("NO STRUCTURAL PROBLEMS FOUND")
else:
    by = collections.defaultdict(list)
    for bucket, msg in problems:
        by[bucket].append(msg)
    for bucket in sorted(by):
        print(f"[{bucket}]")
        for m in by[bucket][:12]:
            print(f"   - {m}")
        if len(by[bucket]) > 12:
            print(f"   ... {len(by[bucket])-12} more")
    print()
    print(f"TOTAL: {len(problems)}")
sys.exit(1 if problems else 0)
