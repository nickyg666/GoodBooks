from pathlib import Path
import subprocess
import logging

logger = logging.getLogger(__name__)

def convert_to_epub(src, dest=None, ebook_convert_path="ebook-convert"):
    src = Path(src)
    if dest is None:
        dest = src.with_suffix(".epub")
    else:
        dest = Path(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ebook_convert_path, str(src), str(dest)]
    logger.debug(f"Running conversion command: {' '.join(cmd)}")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    logger.debug(f"ebook-convert return code: {proc.returncode}")
    if proc.stdout:
        logger.debug(f"ebook-convert output: {proc.stdout}")

    if proc.returncode != 0:
        raise RuntimeError(f"ebook-convert failed with code {proc.returncode}:\n{proc.stdout}")

    if not dest.exists():
        raise RuntimeError(f"ebook-convert reported success but output file '{dest}' is missing")

    logger.info(f"Successfully converted {src} to {dest}")
    return dest
