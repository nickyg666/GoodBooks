"""Regressions for the mono / 64k-cap / format work.

Each test here exists because the thing it covers actually broke:
  * _cap_bitrate inflated a 32k request to 64k (both branches returned 64k),
    which would silently double the file size.
  * output_path() kept the old signature after gb_abjob.py started passing
    fmt= -- py_compile cannot see that, it is a runtime TypeError.
  * mux_with_chapters() took bitrate where callers passed fmt=, same trap.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

gb_audiobook = pytest.importorskip("gb_audiobook")
gb_abjob = pytest.importorskip("gb_abjob")


# --------------------------------------------------------------------------
# bitrate clamping
# --------------------------------------------------------------------------
@pytest.mark.parametrize("requested,expected", [
    ("32k", "32k"),      # below the ceiling is honoured, not raised
    ("48k", "48k"),
    ("64k", "64k"),
    ("96k", "64k"),      # above the ceiling is reduced
    ("128k", "64k"),
    ("192k", "64k"),
    ("banana", "64k"),
    ("", "64k"),
    (None, "64k"),
])
def test_cap_bitrate_clamps_and_never_inflates(requested, expected):
    assert gb_audiobook._cap_bitrate(requested) == expected


def test_cap_bitrate_is_a_cap_not_a_default():
    """The whole point: a low request must survive."""
    assert gb_audiobook._cap_bitrate("32k") == "32k"
    assert gb_audiobook._cap_bitrate("192k") != "192k"


def test_mux_signature_accepts_fmt():
    import inspect
    params = inspect.signature(gb_audiobook.mux_with_chapters).parameters
    assert "fmt" in params, "mux_with_chapters must accept fmt="
    assert params["bitrate"].default is not None


def test_output_path_signature_accepts_fmt():
    import inspect
    params = inspect.signature(gb_abjob.output_path).parameters
    assert "fmt" in params, "output_path must accept fmt="


def test_output_path_extension_follows_format(tmp_path):
    for fmt, ext in (("m4b", ".m4b"), ("mp3", ".mp3")):
        out = gb_abjob.output_path(tmp_path / "Book.epub", "", "", "64k", fmt)
        assert out.suffix == ext, f"{fmt} produced {out.name}"


# --------------------------------------------------------------------------
# every caller keyword is accepted by the definition it calls
# --------------------------------------------------------------------------
def _kwargs_used(tree, fname):
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if fn == fname:
                used |= {k.arg for k in n.keywords if k.arg}
    return used


def _params(fname, modules=("gb_abjob.py", "gb_audiobook.py")):
    """Find a function definition across modules.

    mux_with_chapters is DEFINED in gb_audiobook.py but CALLED from
    gb_abjob.py. Resolving the signature in the calling module's tree alone
    reports it as undefined, which is a false positive -- so every module is
    searched and the union is taken.
    """
    out = set()
    for mod in modules:
        tree = ast.parse((ROOT / mod).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == fname:
                out = ({a.arg for a in n.args.args} |
                       {a.arg for a in n.args.kwonlyargs})
    return out


@pytest.mark.parametrize("module,fname", [
    ("gb_abjob.py", "output_path"),
    ("gb_abjob.py", "mux_with_chapters"),
    ("gb_abjob.py", "enqueue"),
])
def test_no_call_passes_an_unaccepted_keyword(module, fname):
    """py_compile will NOT catch a kwargs/signature mismatch.

    gb_format_job.py rewired callers to pass fmt= while the definitions still
    had the old signature, and every module still compiled. This is the guard.
    """
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    params = _params(fname)
    assert params, f"{fname}() definition not found in any module"
    bad = _kwargs_used(tree, fname) - params
    assert not bad, f"{fname}() called with unsupported kwargs: {sorted(bad)}"


# --------------------------------------------------------------------------
# real encoder output
# --------------------------------------------------------------------------
def test_real_mux_is_mono_capped_and_chaptered(tmp_path):
    ffmpeg = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
    if ffmpeg.returncode != 0:
        pytest.skip("ffmpeg not installed")
    import math

    raw = tmp_path / "mono.raw"
    b = bytearray()
    for i in range(24000 * 6):
        v = int(12000 * math.sin(i * 0.02))
        b += max(-32768, min(32767, v)).to_bytes(2, "little", signed=True)
    raw.write_bytes(bytes(b))

    wav = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar",
                    "24000", "-ac", "1", "-i", str(raw), str(wav)], check=True)
    mp3 = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav),
                    "-c:a", "libmp3lame", "-b:a", "64k", str(mp3)], check=True)

    chapters = [(0, 3, "One"), (3, 6, "Two")]
    for fmt, ext, want_codec in (("m4b", "m4b", "aac"), ("mp3", "mp3", "mp3")):
        out = tmp_path / f"o.{ext}"
        assert gb_audiobook.mux_with_chapters(
            mp3, out, chapters, "T", "A", bitrate="192k", fmt=fmt), \
            f"{fmt} mux failed"
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", str(out)], capture_output=True, text=True).stdout
        import json
        st = [s for s in json.loads(probe)["streams"]
              if s["codec_type"] == "audio"][0]
        assert st["codec_name"] == want_codec, f"{fmt}: {st['codec_name']}"
        assert int(st["channels"]) == 1, f"{fmt} is not mono"
        assert int(st.get("bit_rate", 0)) // 1000 <= 70, \
            f"{fmt} exceeded the 64k cap: {st.get('bit_rate')}"
