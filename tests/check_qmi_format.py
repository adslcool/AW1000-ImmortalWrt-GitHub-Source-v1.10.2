#!/usr/bin/env python3
"""Compile a host-only check using the patched, pinned vendor CM source."""
from pathlib import Path
import subprocess
import sys
import tempfile

tree = Path(sys.argv[1] if len(sys.argv) > 1 else "work/immortalwrt").resolve()
source = tree / "feeds/qmodem/application/quectel_CM_5G_M/src"
harness = Path(__file__).with_name("cm_format_harness.c")
with tempfile.TemporaryDirectory(prefix="aw1000-qmi-format-") as directory:
    binary = Path(directory) / "check"
    subprocess.run(["gcc", "-O2", "-ffunction-sections", "-fdata-sections",
                    "-I", str(source), str(harness), "-Wl,--gc-sections",
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
