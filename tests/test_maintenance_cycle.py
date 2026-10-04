"""Regressions for the maintenance cycle silently not running.

MEASURED 2026-10-03, after the plugin work: zero "Background maintenance:
cycle start" lines in the journal, while the process was healthy (10 threads,
204 MB, 0 restarts) and two audiobook jobs sat at phase=narrating producing
no chunks. The cycle was running and doing nothing, which is the hardest
failure mode to notice -- no error, no restart, just silence.

Two independent causes, both fixed here and both pinned:

1. PLUGINS was ASSIGNED after the function that READ it.

       9577  def _run_maintenance_cycle():      <- reads PLUGINS
       9613      if PLUGINS is not None: ...
      10586  PLUGINS = None                     <- assigned here
      10623      PLUGINS = _gbpl.PluginManager(...)

   The maintenance thread starts the instant its Thread object is created and
   does NOT wait for the rest of the module to finish executing, so the first
   tick reached line 9613 before 10586 had run:

       NameError: name 'PLUGINS' is not defined

   and every cycle died at that line. The declaration now sits at module
   scope near the top, ahead of both the read and the thread start.

2. _run_maintenance_cycle's docstring had been pushed BELOW the rotate call,

       def _run_maintenance_cycle() -> None:
           try: _rotate_debug_log()
           except Exception: pass
           '''Perform a single maintenance cycle.   <- now a no-op string

   which silently demoted it from __doc__ to a discarded expression. The
   disable guard, the "cycle start" log and the plugin hook are restored at
   the head of the body so that silence is diagnosable next time.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
src = APP.read_text()
tree = ast.parse(src)
lines = src.splitlines()


# --------------------------------------------------------------------------
# 1. PLUGINS must be declared before it is read and before the thread starts
# --------------------------------------------------------------------------
def _module_level_assignment(name):
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return n.lineno
    return None


def test_plugins_declared_at_module_scope():
    assert _module_level_assignment("PLUGINS") is not None, \
        "PLUGINS must be assigned at module level, not inside a function"


def test_plugins_declared_before_the_cycle_reads_it():
    declared = _module_level_assignment("PLUGINS")
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "_run_maintenance_cycle"), None)
    assert fn is not None, "_run_maintenance_cycle missing"
    reads = [sub.lineno for sub in ast.walk(fn)
             if isinstance(sub, ast.Name) and sub.id == "PLUGINS"]
    assert reads, "the cycle no longer reads PLUGINS -- plugin scheduling gone"
    assert declared < min(reads), (
        f"PLUGINS is declared at line {declared} but read at line "
        f"{min(reads)}; the maintenance thread starts before the module "
        "finishes executing, so the first tick raises NameError")


def test_plugins_declared_before_the_maintenance_thread_starts():
    declared = _module_level_assignment("PLUGINS")
    started = _module_level_assignment("BACKGROUND_MAINTENANCE_THREAD")
    assert declared is not None and started is not None
    assert declared < started, (
        "a Thread begins running the moment it is constructed, so anything "
        "the cycle reads must be assigned before the thread is started")


# --------------------------------------------------------------------------
# 2. the cycle head is intact and self-documenting
# --------------------------------------------------------------------------
def test_maintenance_cycle_has_a_real_docstring():
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_run_maintenance_cycle")
    doc = ast.get_docstring(fn)
    assert doc, ("_run_maintenance_cycle has no __doc__: the docstring was "
                 "pushed below the rotate call and demoted to a discarded "
                 "string expression")


def test_maintenance_cycle_head_runs_in_order():
    """disable guard -> log -> rotate -> plugin hook, in that order.

    Read from the statement LIST, not from ast.unparse(fn.body): unparse
    serialises the docstring first and re-orders nested content, so
    character offsets in it do not correspond to execution order.

    Each marker is matched against its OWN statement and the first match
    wins, because the guard statement is a single `try:` containing both
    `disable_background_jobs` and an early `return` -- matching greedily put
    two markers on the same index and the comparison became 4 < 4.
    """
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_run_maintenance_cycle")

    def first_index(*needles):
        """First EXECUTING statement mentioning every needle.

        The docstring is skipped: it is an Expr whose text is a bare string,
        and it quotes "cycle start" while describing the cycle, so matching it
        made the log marker resolve to statement 0 and the comparison read
        1 < 0.
        """
        for i, st in enumerate(fn.body):
            if (i == 0 and isinstance(st, ast.Expr)
                    and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str)):
                continue          # the docstring
            txt = ast.unparse(st)
            if all(nd in txt for nd in needles):
                return i
        return -1

    guard = first_index("disable_background_jobs")
    log = first_index("cycle start")
    rotate = first_index("_rotate_debug_log()")
    hook = first_index("PLUGINS.run_maintenance()")

    for name, idx in (("guard", guard), ("log", log),
                      ("rotate", rotate), ("hook", hook)):
        assert idx >= 0, f"cycle head is missing the {name} step"
    assert guard < log < rotate < hook, (
        f"cycle head runs out of order: guard={guard} log={log} "
        f"rotate={rotate} hook={hook}")


def test_maintenance_cycle_logs_when_it_runs():
    """Silence must not be the only symptom of a dead cycle."""
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "_run_maintenance_cycle")
    body = ast.unparse(fn.body)
    assert "cycle start" in body, \
        "the cycle must log that it started, or a dead cycle is invisible"
    assert "disabled" in body, \
        "a cycle skipped because jobs are disabled must say so"


# --------------------------------------------------------------------------
# 3. exactly one entry point
# --------------------------------------------------------------------------
def test_single_entry_point():
    """Two __main__ blocks meant waitress bound :5000 twice.

    The first block served correctly; the second called main() ->
    waitress.serve() and died every boot with "address already in use",
    because the port was already held by the process doing the binding.
    That is a real error in the log on every start even though the service
    stayed healthy, which buries genuine errors underneath a known-benign one.
    """
    guards = [i + 1 for i, ln in enumerate(lines)
              if ln.startswith("if __name__")]
    assert len(guards) == 1, \
        f"expected one entry point, found {len(guards)} at lines {guards}"


def test_live_entry_point_serves():
    from_idx = src.index('if __name__ == "__main__":')
    block = src[from_idx:from_idx + 400]
    assert "_serve_production(port)" in block, \
        "the reachable entry point must delegate to the WSGI server"
    assert "app.run(host" not in block, \
        "the dev server must not be used in production"


def test_main_is_importable_but_not_auto_run():
    """main() may stay as a definition; it must not execute on import."""
    assert "def main(" in src, "main() definition should be kept"
    assert "    main()" not in src, \
        "main() must not be auto-invoked: it would bind :5000 a second time"


# --------------------------------------------------------------------------
# 4. the plugin is what schedules narration
# --------------------------------------------------------------------------
def test_narration_is_scheduled_through_the_plugin_hook():
    assert "PLUGINS.run_maintenance()" in src, \
        "narration must be driven by the plugin hook, not a hardcoded call"
    plugin = (ROOT / "plugins" / "audiobook" / "__init__.py").read_text()
    assert "def maintenance(" in plugin, \
        "the audiobook plugin must expose a maintenance hook"
    assert "NARRATION_BUDGET_SECONDS" in plugin, \
        "narration needs a budget or it starves behind enrichment"
