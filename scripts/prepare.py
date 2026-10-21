#!/usr/bin/env python3
"""Resolve an official stable tag and construct a new, pinned build tree."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import compat
import features
import phone
import mwan

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=None, capture=False, env=None):
    return subprocess.run(args, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, env=env).stdout


def stable_tag(ref, remote_output=None):
    if ref == "latest":
        if remote_output is None:
            remote_output = run(["git", "ls-remote", "--tags", "--refs",
                                 "https://github.com/immortalwrt/immortalwrt.git", "v*"], capture=True)
        tags = []
        for line in remote_output.splitlines():
            match = re.fullmatch(r"[a-f0-9]+\s+refs/tags/(v(\d+)\.(\d+)\.(\d+))", line)
            if match:
                tags.append((tuple(map(int, match.group(2, 3, 4))), match.group(1)))
        if not tags:
            raise ValueError("没有找到 ImmortalWrt 正式稳定版标签")
        return max(tags)[1]
    ref = ref if ref.startswith("v") else "v" + ref
    if not re.fullmatch(r"v\d+\.\d+\.\d+", ref):
        raise ValueError("IMMORTALWRT_REF 只能是 latest 或正式版本，如 v25.12.2")
    return ref


def clone(url, ref, dest, sparse=None):
    if dest.exists():
        raise RuntimeError("目标目录已存在，拒绝复用旧源码：" + str(dest))
    dest.mkdir(parents=True)
    run(["git", "init", "-q", str(dest)])
    run(["git", "remote", "add", "origin", url], dest)
    if sparse:
        run(["git", "sparse-checkout", "init", "--cone"], dest)
        run(["git", "sparse-checkout", "set", sparse], dest)
    run(["git", "fetch", "--depth", "1", "origin", ref], dest)
    run(["git", "-c", "advice.detachedHead=false", "checkout", "--detach", "FETCH_HEAD"], dest)
    return run(["git", "rev-parse", "HEAD"], dest, capture=True).strip()


def install_filetransfer(vendor, tree):
    target = tree / "package/custom/luci-app-filetransfer"
    shutil.copytree(vendor / "applications/luci-app-filetransfer", target)
    makefile = target / "Makefile"
    original = makefile.read_text()
    if "include ../../luci.mk" not in original or "+luci-compat" not in original:
        raise RuntimeError("FileTransfer 上游目录结构已变化，需检查适配")
    makefile.write_text(original.replace("include ../../luci.mk", "include $(TOPDIR)/feeds/luci/luci.mk")
                       .replace("LuCI page for IPK upload", "LuCI file transfer (APK/IPK aware)"))
    # Full replacement of one upstream Lua model, retaining author attribution.
    shutil.copyfile(ROOT / "files/filetransfer.lua", target / "luasrc/model/cbi/filetransfer.lua")


def select_package_providers(tree, providers):
    """Explicitly replace already-installed feed packages, then verify links.

    feeds install -f only overrides core packages; it does not replace a
    package already installed from another feed. Uninstall is required first.
    """
    selected = {}
    for package, feed in sorted(providers.items()):
        if not all(re.fullmatch(r"[a-zA-Z0-9_.+-]+", item) for item in (package, feed)):
            raise ValueError("Invalid package provider")
        candidates = list((tree / "feeds" / feed).rglob(package + "/Makefile"))
        if len(candidates) != 1:
            raise RuntimeError(f"Cannot find unique {package} source in {feed}")
        source = candidates[0].parent.resolve()
        run(["./scripts/feeds", "uninstall", package], tree)
        run(["./scripts/feeds", "install", "-p", feed, package], tree)
        links = list((tree / "package/feeds").glob("*/" + package))
        if len(links) != 1 or not links[0].is_symlink() or links[0].resolve() != source:
            raise RuntimeError(f"Package provider selection failed: {package} from {feed}")
        selected[package] = {"feed": feed, "path": str(source.relative_to(tree.resolve()))}
    return selected


def install_feeds(tree, providers=None):
    # Matching third-party packages take precedence over other feeds.
    for name in ("helloworld", "qmodem"):
        run(["./scripts/feeds", "install", "-a", "-f", "-p", name], tree)
    available = set()
    for index in (tree / "feeds").glob("*.index"):
        available.update(re.findall(r"^Package: (.+)$", index.read_text(), re.MULTILINE))
    requested = re.findall(r"^CONFIG_PACKAGE_([^=]+)=y$",
                           (ROOT / "config/aw1000.config").read_text(), re.MULTILINE)
    wanted = sorted((set(requested) & available) - {"mwan3", "luci-app-mwan3"})
    run(["./scripts/feeds", "install", *wanted], tree)
    # A feed's recursive -f dependency installation may have linked the old
    # official mwan3 over our custom package. Remove only feed symlinks.
    for name in ("mwan3", "luci-app-mwan3"):
        run(["./scripts/feeds", "uninstall", name], tree)
        if list((tree / "package/feeds").glob("*/" + name)):
            raise RuntimeError("Conflicting mwan3 feed provider remains: " + name)
    return select_package_providers(tree, providers or {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default=os.getenv("IMMORTALWRT_REF", "latest"))
    args = parser.parse_args()
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    ref = stable_tag(args.ref)
    print("ImmortalWrt 正式版：" + ref, flush=True)
    tree = ROOT / "work/immortalwrt"
    commit = clone(lock["immortalwrt"], ref, tree)
    if ref == lock["checked_release"] and commit != lock["checked_release_commit"]:
        raise RuntimeError("已经核对过的正式版标签指向发生改变，请检查上游")
    original_feeds = (tree / "feeds.conf.default").read_text()
    with (tree / "feeds.conf.default").open("a") as stream:
        for name, item in lock["feeds"].items():
            stream.write(f"\nsrc-git {name} {item['url']}^{item['commit']}\n")
    vendor = ROOT / "work/vendor/lean-luci"
    item = lock["filetransfer"]
    clone(item["url"], item["commit"], vendor, sparse=item["path"])
    install_filetransfer(vendor, tree)
    phone_patch = phone.install(tree)
    mwan_patch = mwan.install(tree)
    run(["./scripts/feeds", "update", "-a"], tree,
        env={**os.environ, "MAKEFLAGS": "-j4"})
    patches = compat.apply(tree)
    features.prepare(tree)
    patches["aw1000_phone_integration"] = phone_patch
    patches["mwan3_nft_integration"] = mwan_patch
    # Install requested feature roots and let feeds install their dependencies.
    # Installing every unrelated official package is unnecessary for one device.
    providers = install_feeds(tree, lock.get("package_providers", {}))
    feed_commits = {}
    for path in sorted((tree / "feeds").iterdir()):
        if path.is_dir() and (path / ".git").exists():
            feed_commits[path.name] = run(["git", "rev-parse", "HEAD"], path, capture=True).strip()
    for name, item in lock["feeds"].items():
        if feed_commits.get(name) != item["commit"]:
            raise RuntimeError("第三方 feed 提交不匹配：" + name)
    resolved = {"immortalwrt_ref": ref, "immortalwrt_commit": commit,
                "official_feeds_config": original_feeds,
                "feed_commits": feed_commits, "addon_lock": lock,
                "package_providers": providers,
                "local_compatibility_patches": patches}
    (ROOT / "work/sources-resolved.json").write_text(json.dumps(resolved, indent=2) + "\n")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write("release=" + ref + "\n")


if __name__ == "__main__":
    main()
