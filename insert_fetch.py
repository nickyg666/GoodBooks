#!/usr/bin/env python3
"""Insert the /scan/fetch endpoint after scan_process() in app.py."""
import sys

PATH = "/usr/local/bin/GoodBooks/app.py"

with open(PATH, "r") as f:
    lines = f.readlines()

# Find the end of scan_process() — the next line that starts with '@app.route'
# or 'def ' at the same indentation as the def line.
def_idx = None
for i, l in enumerate(lines):
    if l.strip().startswith("def scan_process("):
        def_idx = i
        break
if def_idx is None:
    print("scan_process not found")
    sys.exit(1)

def_indent = len(lines[def_idx]) - len(lines[def_idx].lstrip())
end_idx = None
for i in range(def_idx + 1, len(lines)):
    s = lines[i].lstrip()
    if not s:
        continue
    ci = len(lines[i]) - len(s)
    if ci < def_indent:
        end_idx = i
        break
    if ci == def_indent and (s.startswith("@app.route") or s.startswith("def ")
                             or s.startswith("class ")):
        end_idx = i
        break
if end_idx is None:
    end_idx = len(lines)

base_indent = lines[def_idx][:def_indent]

NEW = '''@app.route("/scan/fetch", methods=["POST"])
def scan_fetch():
    """Download a scan & steal result into the library under the scan+steal
    placeholder user, then return the library entry so the caller can confirm
    the match and send it to a real user.

    Body (JSON): {"md5": "...", "title": "...", "author": "..."}
    """
    try:
        data = request.get_json(silent=True) or {}
        md5 = (data.get("md5") or "").strip()
        title = (data.get("title") or "").strip()
        author = (data.get("author") or "").strip()

        if not md5:
            return jsonify({"success": False, "error": "No md5 provided"}), 400

        cached = source.cached_result(md5)
        if not cached:
            # Re-run a search to populate the cache, then try again.
            try:
                results, _ = aa_search_with_retry(source, md5)
            except Exception as exc:
                logger.warning("AA refetch failed for %s: %s", md5, exc)
                results = []
            if not results:
                return jsonify({"success": False,
                                "error": "No cached result and AA refetch failed"}), 404
            cached = results[0]
            source._cache[md5] = cached

        best = dict(cached)
        if title:
            best["title"] = title
        if author:
            best["author"] = author

        file_format = (best.get("selected_format")
                       or (best.get("formats") or [None])[0])
        if not file_format:
            return jsonify({"success": False,
                            "error": "No downloadable format for this result"}), 400

        # Download into the default directory (the scan+steal placeholder
        # is not a real user, so the file lands in the default save dir).
        dest_dir = resolve_download_dir(settings_manager.settings.default_download_dir)
        try:
            saved_path = source.download(best, file_format, dest_dir)
        except Exception as exc:
            logger.exception("scan_fetch download failed")
            return jsonify({"success": False, "error": str(exc)}), 500

        cover = normalize_cover_url(best.get("cover", ""))
        description = strip_html_tags(best.get("description", "")).strip()
        history_manager.record(
            "scan+steal",
            best.get("title", saved_path.stem),
            cover,
            best.get("author", ""),
            best.get("selected_format", ""),
            "scan",
            description,
            str(saved_path),
        )
        upsert_library_metadata_for_download(saved_path, best)

        entry_id = get_library_entry_id(saved_path)
        return jsonify({
            "success": True,
            "library_id": entry_id,
            "title": best.get("title", saved_path.stem),
            "author": best.get("author", ""),
            "format": file_format,
            "cover_url": cover,
            "file_path": str(saved_path),
        })
    except Exception as exc:
        logger.exception("scan_fetch failed")
        return jsonify({"success": False, "error": str(exc)}), 500
'''

new_lines = lines[:end_idx]
new_lines.append("\n")
for line in NEW.splitlines(keepends=True):
    new_lines.append((base_indent + line) if not line.endswith("\n")
                     else base_indent + line)
new_lines.append("\n")
new_lines.extend(lines[end_idx:])

with open(PATH, "w") as f:
    f.writelines(new_lines)
print("INSERTED /scan/fetch")