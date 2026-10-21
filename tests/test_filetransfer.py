import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LUA = shutil.which("lua5.1") or shutil.which("lua")


class FileTransfer(unittest.TestCase):
    @unittest.skipUnless(LUA, "Lua interpreter is required for FileTransfer integration checks")
    def test_success_message_is_not_overwritten_when_file_formvalue_is_empty(self):
        for mode in ("upload_submit", "empty_submit"):
            with self.subTest(mode=mode):
                result = subprocess.run([LUA, str(ROOT / "tests/filetransfer_harness.lua"),
                    str(ROOT / "files/filetransfer.lua"), "apk", mode],
                    check=True, capture_output=True, text=True)
                self.assertIn("PASS: multipart upload result message", result.stdout)

    @unittest.skipUnless(LUA, "Lua interpreter is required for FileTransfer integration checks")
    def test_upload_callback_uses_supported_mode_and_preserves_binary_content(self):
        result=subprocess.run([LUA,str(ROOT/"tests/filetransfer_harness.lua"),
            str(ROOT/"files/filetransfer.lua"),"apk","upload"],check=True,capture_output=True,text=True)
        self.assertIn("PASS: real upload callback",result.stdout)

    @unittest.skipUnless(LUA, "Lua interpreter is required for FileTransfer integration checks")
    def test_correct_package_manager_and_literal_filename(self):
        for manager in ("apk", "opkg"):
            with self.subTest(manager=manager), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                log = path / "argv.jsonl"
                dummy = path / manager
                dummy.write_text("#!" + sys.executable + "\nimport os,json,sys\n"
                                 "with open(os.environ['PACKAGE_TEST_LOG'],'a') as stream:\n"
                                 " stream.write(json.dumps(sys.argv[1:])+'\\n')\n")
                dummy.chmod(0o755)
                subprocess.run([LUA, str(ROOT / "tests/filetransfer_harness.lua"),
                                str(ROOT / "files/filetransfer.lua"), manager], cwd=path,
                               env={**os.environ, "PATH": directory + ":" + os.environ["PATH"],
                                    "PACKAGE_TEST_LOG": str(log)}, check=True,
                               capture_output=True, text=True)
                argv = [json.loads(line) for line in log.read_text().splitlines()]
                if manager == "apk":
                    self.assertEqual(argv, [
                        ["add", "--allow-untrusted", "/tmp/upload/bad ' $(touch INJECTED).apk"],
                        ["add", "--allow-untrusted", "/tmp/upload/safe.apk"]])
                else:
                    self.assertEqual(argv, [["install", "/tmp/upload/legacy.ipk"]])
                self.assertFalse((path / "INJECTED").exists())


if __name__ == "__main__":
    unittest.main()
