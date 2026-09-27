"""Every browser module must parse as an ES module. One syntax error blanks a whole page.

Skipped where Node isn't installed (e.g. on the Pi)."""
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
FILES = sorted([*STATIC.glob("*.js"), *STATIC.glob("js/*.js"), *STATIC.glob("vendor/*.mjs")])
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_module_parses(path):
    r = subprocess.run([NODE, "--input-type=module", "--check"], input=path.read_bytes(), capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")


def test_public_script_parses():
    if NODE is None:
        pytest.skip("node not installed")
    r = subprocess.run([NODE, "--check", str(STATIC / "public" / "book.js")], capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
