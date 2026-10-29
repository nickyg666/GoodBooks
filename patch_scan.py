#!/usr/bin/env python3
"""Swap the whole scan_process() body for the cover-matcher version."""
import sys

PATH = "/usr/local/bin/GoodBooks/app.py"

with open(PATH, "r") as f:
    lines = f.readlines()

dec = None
for i, l in enumerate(lines):
    if l.strip().startswith('@app.route("/scan/process"'):
        dec = i
        break
if dec is None:
    print("DECORATOR_NOT_FOUND")
    sys.exit(1)

defi = None
for i in range(dec + 1, len(lines)):
    if lines[i].strip().startswith("def scan_process("):
        defi = i
        break
if defi is None:
    print("DEF_NOT_FOUND")
    sys.exit(1)

def_indent = len(lines[defi]) - len(lines[defi].lstrip())
end = None
for i in range(defi + 1, len(lines)):
    s = lines[i].lstrip()
    if not s:
        continue
    ci = len(lines[i]) - len(s)
    if ci < def_indent:
        end = i
        break
    if ci == def_indent and (s.startswith("@app.route") or s.startswith("def ")
                             or s.startswith("class ")):
        end = i
        break
if end is None:
    end = len(lines)

base_indent = lines[defi][:def_indent]

NEW = '''    """
    Process camera image: upload to x0.at, reverse search, return book matches.
    """
    try:
        image_data = None
        if "image" in request.files:
            file = request.files["image"]
            if file.filename:
                image_data = file.read()
        elif request.is_json:
            data = request.get_json()
            image_b64 = data.get("image_base64", "").strip()
            if image_b64:
                try:
                    image_data = base64.b64decode(image_b64)
                except Exception:
                    pass

        if not image_data:
            return jsonify({"success": False, "error": "No image provided"}), 400

        # Upload to x0.at
        image_url = _upload_image_to_host(image_data)
        if not image_url:
            return jsonify({"success": False, "error": "Failed to upload image to host"}), 500

        # Identify the cover through the cover matcher: crop, pHash the scan,
        # run Yandex reverse image search on the crop, pHash-rank the hits,
        # and OCR the best clean copy for a title query. Google Lens is gone
        # from this chain -- it bot-blocks and /searchbyimage died in 2019.
        match_detail = {}
        try:
            import gb_covermatch
            match_detail = gb_covermatch.identify_cover(image_data, timeout=45)
        except Exception as exc:
            logger.warning("Cover matcher failed: %s", exc)
            match_detail = {}

        queries = list(match_detail.get("queries") or [])

        results = []
        if queries:
            seen = set()
            for query in queries[:3]:
                if not query or query in seen:
                    continue
                seen.add(query)
                search_options = SearchOptions(
                    query=query,
                    language="en",
                    max_rows=10,
                    max_results=10,
                    resolve_downloads=False,
                )
                search_results, _ = aa_search_with_retry(source, query, options=search_options)
                results.extend(search_results or [])

        return jsonify({
            "success": True,
            "image_url": image_url,
            "queries": queries,
            "results": results,
            "match_detail": match_detail,
        })
    except Exception as exc:
        logger.exception("Scan process failed")
        return jsonify({"success": False, "error": str(exc)}), 500
'''

new_lines = lines[:dec] + [lines[dec], lines[defi]]
for line in NEW.splitlines(keepends=True):
    new_lines.append((base_indent + line) if not line.endswith("\n")
                     else base_indent + line)
new_lines.append("\n")
new_lines.extend(lines[end:])

with open(PATH, "w") as f:
    f.writelines(new_lines)
print("PATCHED OK")