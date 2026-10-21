import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_xray_go
import prepare


class XrayCompatibility(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)
        self.source = self.tree / "feeds/packages/net/xray-core"
        self.source.mkdir(parents=True)
        self.link = self.tree / "package/feeds/packages/xray-core"
        self.link.parent.mkdir(parents=True)
        self.link.symlink_to(self.source)
        go_root = self.tree / "feeds/packages/lang/golang"
        (go_root / "golang1.26").mkdir(parents=True)
        (go_root / "golang-values.mk").write_text("GO_DEFAULT_VERSION:=1.26\n")
        (go_root / "golang1.26/Makefile").write_text(
            "GO_VERSION_MAJOR_MINOR:=1.26\nGO_VERSION_PATCH:=8\nGO_VERSION_RC:=\n")
        self.archive = self.tree / "dl/xray-core-26.3.27.tar.gz"
        self.archive.parent.mkdir()

    def write_source(self, go):
        data = f"module github.com/xtls/xray-core\n\ngo {go}\n".encode()
        with tarfile.open(self.archive, "w:gz") as tar:
            member = tarfile.TarInfo("Xray-core-26.3.27/go.mod")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.source / "Makefile").write_text(
            f"PKG_VERSION:=26.3.27\nPKG_HASH:={digest}\nPKG_BUILD_DEPENDS:=golang/host\n")

    def test_official_go_requirement_is_compatible(self):
        self.write_source("1.26")
        self.assertEqual(check_xray_go.check(self.tree)["feed_go"], "1.26.8")

    def test_new_minor_or_patch_requirement_fails_before_compilation(self):
        for required in ("1.27", "1.26.9"):
            with self.subTest(required=required):
                self.write_source(required)
                with self.assertRaisesRegex(RuntimeError, "requires Go >="):
                    check_xray_go.check(self.tree)

    def test_corrupt_source_is_rejected(self):
        self.write_source("1.26")
        with self.archive.open("ab") as stream:
            stream.write(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            check_xray_go.check(self.tree)

    def test_duplicate_provider_is_rejected(self):
        self.write_source("1.26")
        other = self.tree / "package/feeds/helloworld/xray-core"
        other.parent.mkdir()
        other.symlink_to(self.source)
        with self.assertRaisesRegex(RuntimeError, "exclusively"):
            check_xray_go.check(self.tree)

    def test_provider_replacement_uninstalls_existing_feed_and_is_repeatable(self):
        self.write_source("1.26")
        old_source = self.tree / "feeds/helloworld/xray-core"
        old_source.mkdir(parents=True)
        old_link = self.tree / "package/feeds/helloworld/xray-core"
        old_link.parent.mkdir()
        self.link.unlink()
        old_link.symlink_to(old_source)

        # Model feeds' documented cross-feed behavior: install alone keeps an
        # existing feed package, even with -f. Only uninstall removes it.
        def feeds(args, cwd):
            links = list((cwd / "package/feeds").glob("*/xray-core"))
            if args[1] == "uninstall":
                for link in links:
                    link.unlink()
            elif not links:
                self.link.symlink_to(self.source)

        with patch.object(prepare, "run", side_effect=feeds) as run:
            for _ in range(2):
                report = prepare.select_package_providers(self.tree, {"xray-core": "packages"})
                self.assertEqual(report["xray-core"]["feed"], "packages")
                self.assertEqual(self.link.resolve(), self.source)
            self.assertEqual(run.call_args_list[0].args[0], ["./scripts/feeds", "uninstall", "xray-core"])

    def test_missing_official_source_does_not_silently_keep_helloworld(self):
        with patch.object(prepare, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "unique xray-core source"):
                prepare.select_package_providers(self.tree, {"xray-core": "packages"})
            run.assert_not_called()

    def test_cli_skips_source_check_when_ssr_not_selected(self):
        import json
        root=self.tree/'project'
        (root/'work').mkdir(parents=True)
        (root/'work/build-features.json').write_text(json.dumps({'include_ssr_plus':False,'include_openclash':True}))
        with patch.object(check_xray_go,'ROOT',root), patch.object(check_xray_go,'check',side_effect=AssertionError('Unexpected Xray download check')):
            check_xray_go.main()
        report=json.loads((root/'work/xray-go-compatibility.json').read_text())
        self.assertTrue(report['skipped'])
