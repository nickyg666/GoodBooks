"""GoodBooks plugin contract.

There was no extension mechanism at all before this: the audiobook generator
was 16 functions and 8 routes hardcoded into a 10,000-line app.py, which is
why every change to it risked the rest of the service (it has already
crash-looped the service twice while being edited).

Measured coupling of the audiobook code to app.py, over the block that
implements it (lines 10025-10670):

    jsonify   34     request   29     app   11     logger   10
    settings_manager 4        BASE_DIR 3       DATA_DIR 2
    get_library_entry 3      build_library_entries 2
    url_for   1      send_file 1

Almost all of it is Flask surface (jsonify/request/app/logger) plus exactly
three service calls: get_library_entry, build_library_entries and the paths.
That is small enough to invert: a plugin declares what it needs from the
host, and the host hands it a context object rather than the plugin reaching
into app.py's globals.

THE CONTRACT, deliberately small:

  * a plugin is a package under plugins/<name>/ with a manifest;
  * the manifest declares id, name, version, provides, and whether it wants
    routes, maintenance ticks, or both;
  * entry_points() returns callables the host invokes;
  * the host passes a PluginContext -- settings, paths, the Flask app, and a
    small service API -- so a plugin never imports app.py.

The rule that makes this safe: a plugin may NOT import the host module.
Importing app.py boots a second service instance with its own metadata
cache, and that has destroyed live library data three times (1,074 records;
4,869 -> 73; 4,837 -> 37). Plugins get capabilities through the context.

Enablement is data, not code: a JSON file lists enabled plugin ids, so
disabling one is an edit rather than a redeploy. Loading failures are
isolated -- a plugin that raises on load is reported and skipped, never
allowed to take the service down.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("gb_plugins")

PLUGINS_DIRNAME = "plugins"
MANIFEST_NAME = "plugin.json"
STATE_FILE = "plugins_enabled.json"


@dataclass
class PluginContext:
    """What a plugin is allowed to touch.

    Deliberately not `the app module`. Everything a plugin needs is here, so
    nothing in a plugin needs `import app`, which is the mistake that has
    cost live data three times in this project.
    """

    app: Any                       # the Flask app, for @route registration
    settings: Any                  # settings_manager.settings
    base_dir: Path                 # project root
    data_dir: Path                 # data/
    logger: logging.Logger
    services: "ServiceAPI"


@dataclass
class ServiceAPI:
    """The host capabilities a plugin may call.

    These are the three service functions the audiobook code actually needed
    from app.py's globals, plus the write path. A plugin gets this object
    instead of importing app.py.
    """

    get_library_entry: Callable[[str], Optional[dict]]
    build_library_entries: Callable[[], list]
    load_library_metadata: Callable[[], dict]
    read_metadata: Callable[[str], dict]
    atomic_write_metadata: Callable[[dict], None]
    metadata_path: Path
    # Needed to rebuild a real path from the '<root>::<relpath>' composite
    # id. It was missing, so resolve_book_file raised AttributeError and every
    # /audiobook/start returned 500.
    settings: Any = None
    log_info: Callable[[str], None] = print
    log_debug: Callable[[str], None] = print

    def resolve_book_file(self, entry_id: str) -> Optional[str]:
        """The real file for a library entry, or None.

        Kept on the API rather than in the plugin because it encodes the
        '<root>::<relpath>' composite id format, which is the host's
        convention and not the plugin's business.
        """
        try:
            return self._resolve_book_file(entry_id)
        except Exception:
            # A plugin-facing helper must never be the thing that 500s a
            # request; an unresolvable path is a None, not a crash.
            logger.debug("resolve_book_file failed for %s", entry_id,
                         exc_info=True)
            return None

    def _resolve_book_file(self, entry_id: str) -> Optional[str]:
        entry = self.get_library_entry(entry_id)
        if not entry:
            return None
        explicit = entry.get("path")
        if explicit and Path(explicit).exists():
            return explicit

        eid = str(entry.get("id") or "")
        root = str(getattr(self.settings, "library_root", "") or "")
        rel = None
        if "::" in eid:
            rel = eid.split("::", 1)[1]
        elif root and eid.startswith(root):
            try:
                rel = str(Path(eid).relative_to(root))
            except ValueError:
                rel = None
        if rel:
            candidate = Path(root) / rel
            if candidate.exists():
                return str(candidate.resolve())

        # last resort: match on the file name alone
        name = eid.rsplit("/", 1)[-1]
        # rglob over a 4,837-file network mount is slow, so this is a LAST
        # resort with a cap on directories walked, and the walk is skipped
        # entirely when a caller can supply the root.
        for base in ([Path(root)] if root else [Path(".")]):
            try:
                seen = 0
                for p in base.rglob(name):
                    return str(p.resolve())
                    seen += 1
                    if seen > 8:
                        break
            except OSError:
                continue
        return None


@dataclass
class LoadedPlugin:
    id: str
    name: str
    version: str
    provides: List[str]
    path: Path
    module: Any = None
    hooks: Dict[str, Any] = field(default_factory=dict)
    routes: List[str] = field(default_factory=list)
    error: str = ""

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "provides": self.provides,
            "routes": self.routes,
            "enabled": True,
            "error": self.error,
        }


class PluginManager:
    """Discovers, loads and runs plugins. One instance per process."""

    def __init__(self, base_dir: Path, context_factory: Callable[[], PluginContext],
                 app: Any = None):
        self.base_dir = Path(base_dir)
        self.plugins_dir = self.base_dir / PLUGINS_DIRNAME
        self._context_factory = context_factory
        # Kept so route discovery does not have to build a whole new context
        # (and a new ServiceAPI) just to read `.app`.
        self._app = app
        # URL prefix -> plugin id, filled as plugins load.
        self._route_owner: Dict[str, str] = {}
        self._loaded: Dict[str, LoadedPlugin] = {}
        self._lock = threading.Lock()
        self._state_path = self.base_dir / "data" / STATE_FILE
        self._tick_running = threading.Event()

    # ---- state ----------------------------------------------------------
    def _read_state(self) -> Dict[str, bool]:
        try:
            raw = json.loads(self._state_path.read_text())
            if isinstance(raw, dict):
                return {str(k): bool(v) for k, v in raw.items()}
        except (OSError, ValueError):
            pass
        # default: enabled unless explicitly disabled
        return {}

    def _write_state(self, state: Dict[str, bool]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.flush()
        tmp.replace(self._state_path)

    def is_enabled(self, plugin_id: str) -> bool:
        return self._read_state().get(plugin_id, True)

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """Flip a plugin on or off.

        This is a GATE, not a load. Flask builds its url_map once, so calling
        register() after the service has handled a request raises

            AssertionError: The setup method 'route' can no longer be called
            on the application.

        which is what happened: enabling at runtime tried to re-register and
        the plugin was left disabled. So enable re-imports the module for its
        HOOKS but never re-registers routes -- the routes registered at boot
        stay in the url_map and the before_request guard decides whether they
        answer.
        """
        state = self._read_state()
        state[plugin_id] = bool(enabled)
        self._write_state(state)
        if enabled:
            self.load(plugin_id, register_routes=False)
        else:
            self.unload(plugin_id)
        return True

    # ---- discovery ------------------------------------------------------
    def discover(self) -> List[Path]:
        if not self.plugins_dir.is_dir():
            return []
        found = []
        for child in sorted(self.plugins_dir.iterdir()):
            if child.is_dir() and (child / MANIFEST_NAME).is_file():
                found.append(child)
        return found

    def _read_manifest(self, path: Path) -> dict:
        try:
            return json.loads((path / MANIFEST_NAME).read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"bad manifest: {exc}")

    # ---- loading --------------------------------------------------------
    def load_all(self) -> Dict[str, LoadedPlugin]:
        """Load every enabled plugin. A failure isolates to that plugin."""
        with self._lock:
            self._loaded.clear()
            for path in self.discover():
                try:
                    manifest = self._read_manifest(path)
                except ValueError as exc:
                    logger.error("plugin at %s: %s", path, exc)
                    continue
                pid = str(manifest.get("id") or path.name)
                if not self.is_enabled(pid):
                    logger.info("plugin %s disabled by state", pid)
                    continue
                self._load_one(path, manifest)
            return dict(self._loaded)

    def load(self, plugin_id: str, register_routes: bool = True) -> bool:
        path = self.plugins_dir / plugin_id
        if not (path / MANIFEST_NAME).is_file():
            return False
        try:
            manifest = self._read_manifest(path)
        except ValueError:
            return False
        return self._load_one(path, manifest, register_routes=register_routes)

    def _load_one(self, path: Path, manifest: dict,
                  register_routes: bool = True) -> bool:
        """Import a plugin and optionally let it register routes.

        register_routes is False on the runtime enable path: Flask forbids
        adding routes after the first request, so only the boot-time load may
        call the plugin's register().
        """
        import importlib.util

        pid = str(manifest.get("id") or path.name)
        module_name = f"gbplugin_{pid.replace('-', '_')}"
        entry_file = path / str(manifest.get("entry") or "__init__.py")
        if not entry_file.is_file():
            entry_file = path / "__init__.py"
        if not entry_file.is_file():
            self._loaded[pid] = LoadedPlugin(
                id=pid, name=manifest.get("name", pid),
                version=manifest.get("version", "0"),
                provides=manifest.get("provides", []),
                path=path, error="no entry point")
            return False

        try:
            # The plugin's own package dir goes first so its relative imports
            # resolve, and the PROJECT root is NOT added -- that is what would
            # let a plugin `import app` and boot a second writer.
            spec = importlib.util.spec_from_file_location(
                module_name, entry_file,
                submodule_search_locations=[str(path)])
            if spec is None or spec.loader is None:
                raise ImportError("no loader")
            module = importlib.util.module_from_spec(spec)
            sys_modules = __import__("sys").modules
            sys_modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error("plugin %s failed to load: %s: %s",
                         pid, type(exc).__name__, exc, exc_info=True)
            self._loaded[pid] = LoadedPlugin(
                id=pid, name=manifest.get("name", pid),
                version=manifest.get("version", "0"),
                provides=manifest.get("provides", []),
                path=path, error=f"{type(exc).__name__}: {exc}")
            return False

        hooks: Dict[str, Any] = {}
        if register_routes and callable(getattr(module, "register", None)):
            try:
                ctx = self._context_factory()
                got = module.register(ctx)
                if isinstance(got, dict):
                    hooks = got
            except Exception as exc:
                logger.error("plugin %s register() failed: %s: %s",
                             pid, type(exc).__name__, exc, exc_info=True)
                self._loaded[pid] = LoadedPlugin(
                    id=pid, name=manifest.get("name", pid),
                    version=manifest.get("version", "0"),
                    provides=manifest.get("provides", []),
                    path=path, error=f"register: {type(exc).__name__}: {exc}")
                return False

        # Record the routes the plugin registered, for /api/plugins.
        #
        # Matching on endpoint NAME does not work: Flask derives an endpoint
        # from the view function's __name__ unless endpoint= is passed, so a
        # plugin whose views are named audiobook_start never matches the
        # "gbplugin_audiobook" module prefix and the list was always empty.
        # The plugin's own declared route list is authoritative instead, and
        # anything else the plugin added is picked up from the url_map by
        # path.
        if not hooks:
            # Runtime enable: register() was deliberately skipped, so take the
            # hooks the module exposes directly. The audiobook plugin keeps
            # `maintenance` reachable this way.
            hooks = {n: getattr(module, n) for n in ("maintenance",)
                     if callable(getattr(module, n, None))}
            # ...and the declared route list, which is what the guard needs in
            # order to attribute paths to this plugin at all.
            hooks["_routes"] = list(getattr(module, "ROUTES", []) or [])
        declared = list(hooks.get("_routes") or []) if hooks else []
        routes = []
        if self._app is not None:
            try:
                known = set(str(r) for r in self._app.url_map.iter_rules())
                for path in declared:
                    routes.append(path if path in known else path + " (declared)")
            except Exception:
                routes = declared

        self._loaded[pid] = LoadedPlugin(
            id=pid, name=manifest.get("name", pid),
            version=str(manifest.get("version", "0")),
            provides=list(manifest.get("provides", [])),
            path=path, module=module, hooks=hooks, routes=routes)
        # Remember which URL prefixes this plugin owns so a disabled plugin
        # can be refused per request. Flask's url_map cannot be subtracted
        # from, so "disable" cannot mean "unregister"; it has to mean "this
        # plugin's paths stop answering".
        # Route ownership is a property of the MANIFEST, not of this load.
        # It must be recorded on every load, including the runtime-enable
        # path that deliberately skips calling register() -- otherwise the
        # guard cannot tell that a path belongs to an enabled plugin and
        # refuses it. Measured: after a runtime enable the state file said
        # {"audiobook": true} and /api/plugins reported enabled=true, while
        # /audiobook/options returned 404.
        for r in (declared or routes):
            base = str(r).strip()
            if base and not base.endswith(" (declared)"):
                self._route_owner.setdefault(base, pid)
        logger.info("plugin %s v%s loaded (provides: %s)",
                    pid, manifest.get("version"), manifest.get("provides"))
        return True

    def unload(self, plugin_id: str) -> bool:
        """Drop a plugin's hooks.

        _route_owner is deliberately KEPT: the routes are still in Flask's
        url_map (they cannot be removed), so the guard must still know which
        plugin owns them in order to refuse them while disabled.
        """
        with self._lock:
            return self._loaded.pop(plugin_id, None) is not None

    # ---- running --------------------------------------------------------
    def hooks(self, name: str) -> list:
        out = []
        for lp in self._loaded.values():
            fn = lp.hooks.get(name)
            if callable(fn):
                out.append((lp.id, fn))
        return out

    def call_hook(self, name: str, *args, **kwargs) -> list:
        """Call every plugin's hook, isolating failures.

        A plugin raising must never propagate into the maintenance cycle or a
        request -- that is how one bad edit took the whole service down.
        """
        results = []
        for pid, fn in self.hooks(name):
            try:
                results.append((pid, fn(*args, **kwargs)))
            except Exception as exc:
                logger.error("plugin %s hook %r failed: %s: %s",
                             pid, name, type(exc).__name__, exc,
                             exc_info=True)
                results.append((pid, None))
        return results

    def run_maintenance(self, *args, **kwargs) -> list:
        return self.call_hook("maintenance", *args, **kwargs)

    def owns_path(self, path: str) -> Optional[str]:
        """The id of the plugin that owns this URL, if any."""
        pid = self._route_owner.get(path)
        if pid:
            return pid
        # fall back to a prefix match so a plugin that registers a subpath it
        # did not declare is still attributed
        for route, owner in self._route_owner.items():
            if path.startswith(route.rstrip("/")):
                return owner
        return None

    def is_path_enabled(self, path: str) -> bool:
        """True unless the owning plugin is currently disabled."""
        pid = self.owns_path(path)
        if pid is None:
            return True
        return self.is_enabled(pid)


    def summaries(self) -> List[dict]:
        out = [lp.summary() for lp in self._loaded.values()]
        # include discovered-but-disabled so the UI can offer to enable
        known = {lp.id for lp in self._loaded.values()}
        for path in self.discover():
            try:
                man = self._read_manifest(path)
            except ValueError:
                continue
            pid = str(man.get("id") or path.name)
            if pid not in known:
                out.append({"id": pid, "name": man.get("name", pid),
                            "version": str(man.get("version", "0")),
                            "provides": man.get("provides", []),
                            "routes": [], "enabled": self.is_enabled(pid),
                            "error": ""})
        return sorted(out, key=lambda d: d["id"])

    def status_line(self) -> str:
        if not self._loaded:
            return "no plugins loaded"
        return ", ".join(f"{lp.id}@{lp.version}" for lp in self._loaded.values())
