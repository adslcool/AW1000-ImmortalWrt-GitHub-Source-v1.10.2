"""Install pinned, non-prerelease mwan3-nft and matching LuCI from local archives."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def install(tree):
    lock=json.loads((ROOT/'sources.lock.json').read_text())['mwan3_nft']
    report={}
    for name,item in lock.items():
        archive=ROOT/item['archive']
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=item['sha256']:
            raise RuntimeError('mwan3-nft archive checksum mismatch: '+name)
        target=tree/'package/custom'/name
        target.mkdir(parents=True)
        seen=set()
        with tarfile.open(archive,'r:xz') as bundle:
            for member in bundle.getmembers():
                relative=PurePosixPath(member.name)
                if not member.isfile() or relative.is_absolute() or '..' in relative.parts or member.name in seen:
                    raise RuntimeError('Unsafe mwan3-nft archive entry')
                seen.add(member.name)
                path=target.joinpath(*relative.parts);path.parent.mkdir(parents=True,exist_ok=True)
                with bundle.extractfile(member) as stream:path.write_bytes(stream.read())
                path.chmod(member.mode&0o777)
        if name=='luci-app-mwan3':
            makefile=target/'Makefile';text=makefile.read_text()
            if text.count('include ../../luci.mk')!=1:raise RuntimeError('mwan3 LuCI layout changed')
            makefile.write_text(text.replace('include ../../luci.mk','include $(TOPDIR)/feeds/luci/luci.mk'))
        if name=='mwan3':
            makefile=target/'Makefile';text=makefile.read_text()
            if text.count('PKG_BUILD_DEPENDS:=libnetfilter_conntrack libmnl')!=1:
                raise RuntimeError('mwan3 build dependency layout changed')
            makefile.write_text(text.replace('PKG_BUILD_DEPENDS:=libnetfilter_conntrack libmnl',
                                            'PKG_BUILD_DEPENDS:=libnetfilter-conntrack libmnl'))
        if name=='mwan3':
            runtime=target/'files/lib/mwan3/mwan3.sh'
            text=runtime.read_text()
            anchor='\t[ -n "$iface" ] || return 0\n\t[ "$metric" -gt'
            if text.count(anchor)!=1:
                raise RuntimeError('mwan3 policy eligibility layout changed')
            text=text.replace(anchor, '\t[ -n "$iface" ] || return 0\n'
                '\t# AW1000 SIM hard exclusion survives tracker connected events.\n'
                '\t[ "$(cat /var/run/aw1000-internet/blocked 2>/dev/null)" != "$iface" ] || return 0\n'
                '\t[ "$metric" -gt')
            runtime.write_text(text)
            tracker=target/'files/usr/sbin/mwan3track'
            text=tracker.read_text()
            anchor='\t\tconfig_get STATUS $INTERFACE initial_state "online"\n\tfi\n'
            if text.count(anchor)!=1:
                raise RuntimeError('mwan3 tracker initial state layout changed')
            tracker.write_text(text.replace(anchor,anchor+
                '\t# AW1000: SIM recovery always starts with native offline scoring.\n'
                '\t[ "$(cat /var/run/aw1000-internet/blocked 2>/dev/null)" != "$INTERFACE" ] || STATUS=offline\n'))

        report[name]={'commit':item['commit'],'sha256':item['sha256'],
                      'makefile_sha256':hashlib.sha256((target/'Makefile').read_bytes()).hexdigest()}
    return report
