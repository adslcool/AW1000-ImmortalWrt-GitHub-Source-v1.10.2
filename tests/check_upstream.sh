#!/bin/sh
# Pure source checks: no host route/firewall operations.
set -eu
tree=${1:-work/immortalwrt}
rules="$tree/feeds/helloworld/luci-app-ssr-plus/root/usr/bin/ssr-rules"
grep -qx 'FWMARK="0x5250"' "$rules"
[ "$(grep -c 'ip rule.*fwmark.*FWMARK.*0x0000ffff' "$rules")" = 5 ]
(
    . "$tree/package/custom/mwan3/files/lib/mwan3/common.sh"
    [ $((0x00ff0000 & 0xffff)) = 0 ]
    [ "$(mwan3_nft_mark_expr 0x10000 0x00ff0000)" = 'meta mark set meta mark & 0xff00ffff | 0x10000' ]
    combined=$(((0x5250 & 0xff00ffff) | 0x10000))
    [ $((combined & 0xffff)) = $((0x5250)) ]
)
grep -q 'PKG_VERSION:=3.6.12' "$tree/package/custom/mwan3/Makefile"
grep -q 'include $(TOPDIR)/feeds/luci/luci.mk' "$tree/package/custom/luci-app-mwan3/Makefile"
echo 'PASS: pinned native nft mwan3; disjoint marks and SSR TPROXY mask'
