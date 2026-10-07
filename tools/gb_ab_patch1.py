
#!/usr/bin/env python3
"""Add cover + entry_id to each /api/audiobooks row, patching the plugin
by AST to keep the edit structural (every text-anchor edit this session has
bitten: multiple matches, wrong function, stale file)."""
import ast, json, sys
from pathlib import Path

P = Path("/usr/local/bin/GoodBooks/plugins/audiobook/__init__.py")
src = P.read_text()
tree = ast.parse(src)

# find the api_audiobooks function, then the row-building loop
fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "api_audiobooks":
        fn = node; break
assert fn is not None, "api_audiobooks not found"
print("function found at line", fn.lineno)
# locate the 'out.append(row)' statement and the eid variable
for node in ast.walk(fn):
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "append":
        print("append at line", node.lineno, ast.dump(node.args[0])[:60])
