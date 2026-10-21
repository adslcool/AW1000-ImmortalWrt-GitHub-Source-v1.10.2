AW1000 Si3226x DSP 补丁（从原厂模块提取）

文件：si3226x_patch.bin
大小：908 字节
补丁 ID：0x05262017
数据字：191（190 个非零程序字及结尾零）
入口表：16 项
辅助 RAM：16 项，保留值为 0 的配置
SHA256：a8c305841adf327e180bff6cbac92ada5a0625ca2d9e8c22733e4b3f2b8cad99

来源：
https://github.com/kylemd/Arcadyan-AW1000-IPQ807x-Firmware-Dump
提交：cd8d23529716ac0d1773a61cf31a9685f9eb2778
原厂 ubi/ubi0_1.gz.001 与 .002 合并并 gzip 解压后的 SquashFS 根文件系统。
文件 /lib/modules/4.4.60/slic.ko，RevCPatch 符号。
第二来源 /lib/modules/4.4.60/ve_vtsp_hw.ko 导出的文件逐字节一致。

格式对应：
https://github.com/Perz3us/openwrt-aw1000-phone
提交：2d3d16cf2c7d8d74e5cbc1e0f8f6461d5198c613
FXSP 小端格式，已通过 tools/mkpatch.py --check、fxsd/src/patch.c 原始 C 解析器。
将提取数组重建为临时 C 数据后，使用原始 mkpatch.py 转换，输出逐字节一致。
未进行实机加载或通话验证。补丁 ID 一致不代表已经证明全部硬件功能正常。

安装：
将 si3226x_patch.bin 上传至路由器的 /etc/fxs/si3226x_patch.bin。
例如在电脑终端执行（替换路由器地址）：
  ssh root@192.168.1.1 'mkdir -p /etc/fxs'
  scp si3226x_patch.bin root@192.168.1.1:/etc/fxs/si3226x_patch.bin
完成匹配内核的 kmod-aw1000-fxs、fxsd、QModem 和项目要求的模组音频配置后，
再通过 LuCI 电话页面启动，或执行 /etc/init.d/fxsd start。
此文件仅为 SLIC 补丁，不是刷机固件。

复现：
安装 Python pyelftools 后：
  python3 extract_patch.py slic.ko si3226x_patch.bin
具体 ELF 偏移、数据及校验和见 extraction.json。

补丁二进制来源于 Skyworks 原厂代码；不因本次提取改变原有权利归属。
