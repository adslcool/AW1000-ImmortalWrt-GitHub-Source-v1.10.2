import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_project as project

collect = project.load("collect")


class Collection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tree = self.root / "work/immortalwrt"
        self.metadata = self.tree / "tmp/.packageinfo"
        self.metadata.parent.mkdir(parents=True)
        self.metadata.write_text("Package: oniguruma\nABI-Version: 5\n\n"
                                 "Package: libfoo1\nABI-Version: 2\n\n"
                                 "Package: kmod-test\nABI-Version: 5\n\n"
                                 "Package: jq-full\nProvides: jq\n")

    def test_exact_abi_names_and_digit_separator(self):
        names, aliases = collect.manifest_identities(
            {"oniguruma5", "libfoo1-2", "kmod-test5"}, self.metadata)
        self.assertIn("oniguruma", names)
        self.assertIn("libfoo1", names)
        self.assertNotIn("kmod-test", names)
        self.assertEqual(aliases, {"oniguruma": "oniguruma5", "libfoo1": "libfoo1-2"})

    def test_wrong_abi_and_prefix_are_not_accepted(self):
        names, aliases = collect.manifest_identities(
            {"oniguruma6", "oniguruma-debug", "libfoo12"}, self.metadata)
        self.assertNotIn("oniguruma", names)
        self.assertNotIn("libfoo1", names)
        self.assertEqual(aliases, {})

    def test_virtual_provides_does_not_expand(self):
        names, _ = collect.manifest_identities({"jq-full"}, self.metadata)
        self.assertEqual(names, {"jq-full"})

    def fixture(self, omit_package=False, omit_library=False, options=None):
        options = options or {"include_ssr_plus": True, "include_openclash": True}
        required, runtime = collect.selected_requirements(options)
        target = self.tree / "bin/targets/qualcommax/ipq807x"
        target.mkdir(parents=True)
        packages = required - {"oniguruma"}
        if not omit_package:
            packages = packages | {"oniguruma5"}
        (target / "test-arcadyan_aw1000.manifest").write_text(
            "".join(f"{name} - 1.0\n" for name in sorted(packages)))
        (target / "test-arcadyan_aw1000-sysupgrade.bin").write_bytes(b"fixture image")
        rootfs = self.tree / "build_dir/target-test/root-qualcommax"
        for name in runtime + ["lib/modules/test/aw1000-fxs.ko"]:
            path = rootfs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        shutil.copyfile(project.ROOT / "firmware/si3226x/si3226x_patch.bin",
                        rootfs / "etc/fxs/si3226x_patch.bin")
        if not omit_library:
            library = rootfs / "usr/lib/libonig.so.5.5.0"
            library.parent.mkdir(parents=True, exist_ok=True)
            library.write_bytes(b"fixture library")
        core = rootfs / "etc/openclash/core/clash_meta"
        core.parent.mkdir(parents=True, exist_ok=True)
        if options['include_openclash']:
            core.symlink_to('/usr/libexec/mihomo')
        work = self.root / "work"
        (work / "build-features.json").write_text(json.dumps(options))
        (work / "selected-packages.json").write_text(json.dumps(sorted(required)))
        for name in ("sources-resolved.json", "xray-go-compatibility.json"):
            (work / name).write_text("{}\n")
        (self.tree / ".config").write_text("CONFIG_TARGET_qualcommax=y\n")

    def run_collection(self):
        result = subprocess.CompletedProcess([], 0, stdout="CONFIG_TARGET_qualcommax=y\n")
        with patch.object(collect, "ROOT", self.root), \
                patch.object(collect, "build_date", return_value="20261020"), \
                patch.object(collect.subprocess, "run", return_value=result), \
                contextlib.redirect_stdout(io.StringIO()):
            collect.main()

    def test_collection_accepts_abi_manifest_and_emits_report(self):
        self.fixture()
        self.run_collection()
        output = self.root / "output"
        report = json.loads((output / "verification.json").read_text())
        self.assertEqual(report["abi_package_aliases"], {"oniguruma": "oniguruma5"})
        self.assertIn("oniguruma5 - 1.0", (output / "firmware.manifest").read_text())
        self.assertTrue((output / "test-arcadyan_aw1000-sysupgrade-20261020.bin").is_file())
        self.assertIn("verification.json", (output / "sha256sums").read_text())
        self.assertEqual(report['images'],['test-arcadyan_aw1000-sysupgrade-20261020.bin'])
        self.assertEqual(report['build_date'],'20261020')
        self.assertTrue((self.tree/'bin/targets/qualcommax/ipq807x/test-arcadyan_aw1000-sysupgrade.bin').exists())
        import hashlib
        for line in (output/'sha256sums').read_text().splitlines():
            digest,name=line.split('  ',1)
            self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(),digest)


    def test_missing_package_still_rejected(self):
        self.fixture(omit_package=True)
        with self.assertRaisesRegex(RuntimeError, "oniguruma"):
            self.run_collection()
        self.assertFalse((self.root / "output").exists())

    def test_missing_phone_translation_still_rejected(self):
        self.fixture()
        (self.tree / "build_dir/target-test/root-qualcommax/usr/lib/lua/luci/i18n/fxs.zh-cn.lmo").unlink()
        with self.assertRaisesRegex(RuntimeError, "根文件系统"):
            self.run_collection()
        self.assertFalse((self.root / "output").exists())

    def test_missing_shared_library_still_rejected(self):
        self.fixture(omit_library=True)
        with self.assertRaisesRegex(RuntimeError, "libonig"):
            self.run_collection()
        self.assertFalse((self.root / "output").exists())

    def test_missing_openclash_core_link_rejected(self):
        self.fixture()
        (self.tree / "build_dir/target-test/root-qualcommax/etc/openclash/core/clash_meta").unlink()
        with self.assertRaisesRegex(RuntimeError, "OpenClash"):
            self.run_collection()
        self.assertFalse((self.root / "output").exists())

    def test_collection_without_proxies(self):
        self.fixture(options={'include_ssr_plus':False,'include_openclash':False})
        self.run_collection()
        report=json.loads((self.root/'output/verification.json').read_text())
        self.assertNotIn('mihomo',report['required_packages_verified'])
        self.assertNotIn('xray-core',report['required_packages_verified'])

    def test_onboarding_does_not_accidentally_require_proxy_core(self):
        packages, runtime = collect.selected_requirements({'include_ssr_plus':False,'include_openclash':False,'include_device_init':True})
        self.assertIn('aw1000-board-state', packages)
        self.assertIn('coreutils-timeout', packages)
        self.assertNotIn('mihomo', packages)
        self.assertNotIn('usr/libexec/mihomo', runtime)
        self.assertIn('usr/libexec/aw1000-board/stage1', runtime)

    def test_collection_openclash_only(self):
        self.fixture(options={'include_ssr_plus':False,'include_openclash':True})
        self.run_collection()
        report=json.loads((self.root/'output/verification.json').read_text())
        self.assertIn('mihomo',report['required_packages_verified'])
        self.assertNotIn('luci-app-ssr-plus',report['required_packages_verified'])

    def test_chinese_summary_has_settings_but_no_credentials(self):
        self.fixture()
        self.run_collection()
        (self.root/'.private').mkdir()
        (self.root/'.private/settings.json').write_text(json.dumps({'lan_ip':'192.168.45.1','admin_password':'DO_NOT_DISCLOSE_PASSWORD','root_password_hash':'DO_NOT_DISCLOSE_HASH'}))
        (self.root/'sources.lock.json').write_text(json.dumps({'build_project_version':'1.8.2'}))
        (self.root/'output/sources-resolved.json').write_text(json.dumps({'immortalwrt_ref':'v25.12.2'}))
        summary=collect.build_summary(self.root)
        self.assertIn('192.168.45.1',summary)
        self.assertIn('仅有线网络',summary)
        self.assertIn('v25.12.2',summary)
        self.assertIn('| 包含 SSR Plus+ | 是 |',summary)
        self.assertIn('| 包含 OpenClash | 是 |',summary)
        self.assertNotIn('DO_NOT_DISCLOSE',summary)

    def test_firmware_date_uses_shanghai_at_utc_day_boundary(self):
        from datetime import datetime, timezone
        moment=datetime(2026,10,20,17,0,tzinfo=timezone.utc)
        with patch.object(collect,'datetime') as clock:
            clock.now.side_effect=lambda zone: moment.astimezone(zone)
            self.assertEqual(collect.build_date(),'20261021')
