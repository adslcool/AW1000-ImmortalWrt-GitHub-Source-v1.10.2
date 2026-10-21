#!/usr/bin/env python3
"""Check the downloaded Xray go.mod against the selected feed's Go compiler."""
import hashlib
import features
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def assignment(text, key):
    match = re.search(r"^" + re.escape(key) + r"[ \t]*:=[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    if not match or not match[1] or "$" in match[1]:
        raise RuntimeError("Cannot resolve literal Makefile value: " + key)
    return match[1]


def version(value):
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
        raise RuntimeError("Unsupported Go version: " + value)
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def check(tree):
    tree = Path(tree)
    links = list((tree / "package/feeds").glob("*/xray-core"))
    source = tree / "feeds/packages/net/xray-core"
    if len(links) != 1 or not links[0].is_symlink() or links[0].resolve() != source.resolve():
        raise RuntimeError("Xray must be installed exclusively from the official packages feed")
    recipe = (source / "Makefile").read_text()
    pkg_version = assignment(recipe, "PKG_VERSION")
    if not re.fullmatch(r"\d+(?:\.\d+)+", pkg_version):
        raise RuntimeError("Unsupported Xray source version")
    # Fail closed if a future recipe switches to a different Go host compiler.
    dependencies = assignment(recipe, "PKG_BUILD_DEPENDS").split()
    if "golang/host" not in dependencies or any(
            re.fullmatch(r"golang[0-9].*/host", item) for item in dependencies):
        raise RuntimeError("Xray Go host dependency changed; update compatibility check")
    archive = tree / "dl" / f"xray-core-{pkg_version}.tar.gz"
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != assignment(recipe, "PKG_HASH"):
        raise RuntimeError("Xray source archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(f"Xray-core-{pkg_version}/go.mod")
        if not member.isfile() or member.size > 1024 * 1024:
            raise RuntimeError("Invalid Xray go.mod archive member")
        go_mod = tar.extractfile(member).read().decode("utf-8")
    match = re.search(r"^go\s+(\d+\.\d+(?:\.\d+)?)\s*$", go_mod, re.MULTILINE)
    if not match:
        raise RuntimeError("Xray go.mod has no supported Go directive")
    required = match[1]
    go_root = tree / "feeds/packages/lang/golang"
    major_minor = assignment((go_root / "golang-values.mk").read_text(), "GO_DEFAULT_VERSION")
    if not re.fullmatch(r"\d+\.\d+", major_minor):
        raise RuntimeError("Unsupported default Go compiler")
    compiler = (go_root / ("golang" + major_minor) / "Makefile").read_text()
    if re.search(r"^GO_VERSION_RC[ \t]*:=[ \t]*\S", compiler, re.MULTILINE):
        raise RuntimeError("Prerelease Go compilers require manual verification")
    actual = assignment(compiler, "GO_VERSION_MAJOR_MINOR") + "." + assignment(compiler, "GO_VERSION_PATCH")
    if version(actual) < version(required):
        raise RuntimeError(f"Xray {pkg_version} requires Go >= {required}, feed provides {actual}")
    return {"package": "xray-core", "feed": "packages", "version": pkg_version,
            "source_sha256": digest, "required_go": required, "feed_go": actual,
            "go_version_compatible": True}


def main():
    if not features.build_options(ROOT)['include_ssr_plus']:
        report = {'skipped': True, 'reason': 'SSR Plus+ / Xray not selected'}
        (ROOT / 'work/xray-go-compatibility.json').write_text(json.dumps(report) + '\n')
        print('SKIP: SSR Plus+ / Xray not selected')
        return
    report = check(ROOT / "work/immortalwrt")
    (ROOT / "work/xray-go-compatibility.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"PASS: Xray {report['version']} requires Go {report['required_go']}; "
          f"feed provides Go {report['feed_go']}")


if __name__ == "__main__":
    main()
