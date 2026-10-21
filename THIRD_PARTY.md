# 上游源码

| 项目 | 来源 | 作用 |
| --- | --- | --- |
| ImmortalWrt | https://github.com/immortalwrt/immortalwrt | 官方完整系统源码、设备定义及固定 feeds |
| ImmortalWrt LuCI / Packages | 正式版源码 `feeds.conf.default` | LuCI、Argon、网络服务和常用插件 |
| QModem | https://github.com/FUjr/QModem | 模块管理、拨号、短信后台与 LuCI 新版界面 |
| SSR Plus+ / HelloWorld | https://github.com/fw876/helloworld | SSR Plus+、Mihomo 等核心与工具；Xray 使用下方官方源 |
| mwan3-nft / LuCI | https://github.com/dl12345/mwan3 与 https://github.com/dl12345/luci-app-mwan3 | 固定非测试 v3.6.12-1，GPL-2.0；源码归档随包，修正 LuCI include 与构建依赖名称 |
| Xray-core | ImmortalWrt 正式版的 `packages/net/xray-core` | v1.1.1 起显式选择官方包，避免第三方新版要求更高 Go 版本 |
| AW1000 RJ11 电话 | https://github.com/Perz3us/openwrt-aw1000-phone | v1.4.0 集成，固定提交与完整原始源码随包，GPL-2.0-only；按固件内核编译，适配实际 AT 端口与 QModem 队列 |
| USB 打印共享 | 正式版的 `packages/net/p910nd` 与 `luci/applications/luci-app-p910nd` | RAW TCP 打印服务与 LuCI 界面，USB 打印内核驱动来自系统源码 |
| FileTransfer | https://github.com/coolsnowwolf/luci/tree/master/applications/luci-app-filetransfer | FileTransfer 页面、翻译和 ACL；本工程替换 Lua model 以兼容 APK 并正确引用文件名 |

所有第三方下载提交锁定在 `sources.lock.json`。Argon 使用 ImmortalWrt 正式版自带版本，不引入 Argon Config。

v1.1 的主备采用同一锁定版本中的 `qmodem-uplink-manager` 与 `qmodem_monitor`，未切换到另一套源码。`scripts/compat.py` 在 QModem 扫描回调中接入本机型登记脚本，并把 SSR Plus+ 的 IPv4 TPROXY 路由标记改为低 16 位及显式掩码，避免与 QModem 的标记范围冲突。补丁有结构检查，实际修改文件的 SHA-256 记录在编译产物 `sources-resolved.json` 中。

FileTransfer 原作者为 yuleniwo、ayongwifi；派生源码位于 `files/filetransfer.lua`，采用 Apache-2.0，许可全文见 `LICENSES/Apache-2.0.txt`。

QModem 仓库当前许可在 MPL-2.0 文本之外注明了禁止商业使用的附加限制；其组成模块也包含各自的许可声明。请查阅实际锁定版本的 LICENSE 与组件头部。工程不会修改上游许可。

项目原创脚本的 MIT 许可不替代任何上游软件的许可。

`files/qmodem_runtime.js` 是本工程新增的 LuCI 协议适配文件，协议仍由原 QModem netifd 后端处理；没有替换成 DHCP，也没有添加第二个拨号服务。


v1.4.0 移除 `nooblk-98/luci-app-aw1k-led` 的编译与运行集成；旧版验证目录中的 LED 配置仅为历史记录。

RJ11 上游许可保存在 电话源码归档中的 `LICENSE`。上游完整源码保存在 `vendor/openwrt-aw1000-phone.tar.xz`，归档及逐文件 SHA-256 均校验，适配由 `scripts/phone.py` 完成：修正全源码树中的共享源路径、声明运行依赖、安装提供的 DSP 补丁、AT 端口自动识别及返回状态检查。服务保持上游启动保护；v1.4.1 在首次启动自动检查并按需设置 PCM，写入持久化状态防止反复重启模组。

`firmware/si3226x/si3226x_patch.bin` 来自用户提供的 `AW1000-Si3226x-Patch(1).zip`，提取说明、原始校验记录与复现脚本一并保留。它是从原厂模块提取的 Skyworks DSP 数据，不属于本工程 MIT 许可或电话项目 GPL 许可的重新授权；其原有权利归属保持不变。详情见该目录的 `README.txt` 与 `extraction.json`。


v1.5.0 主备改为上述 mwan3-nft，QModem uplink 管理器不再安装；旧版主备说明仅为历史。IPv6/DNS 联动与 QModem 健康状态适配由本项目脚本实现，不修改 mwan3 健康算法。上游原始许可完整保存在各自源码归档。

v1.6.0 保持原上游提交，增加本工程 IPv6 热备策略、无 PD 时的 nft NAT66 兜底、LED 常亮管理，以及结构受控的 QModem 会话显示／LED 映射补丁。仍不集成独立 LED 插件。

v1.6.1 为电话 LuCI 添加原创简体中文 PO 翻译，由固定 LuCI 的 po2lmo 编译为标准 LMO，直接随应用包安装；增加 QModem 已有 SIM 检查结果的只读运行时快照及蜂窝会话灯管理，不添加独立拨号或 AT 轮询。

v1.7.0 新增原创 AW1000 上网设置页面与 RPC 后端、确认回退、SIM 分类／新鲜度／USB 代次判断；新增每 5 秒经现有 AT 队列进行的 CPIN 查询。mwan3 原归档和固定提交不变，解包后对策略成员处理加入结构受控的 SIM 硬性排除条件，仍由原生 mwan3 计算和发布 IPv4 路由策略。

v1.7.5 恢复上游 fxsd 原生运行指示灯配置，初始化时强制稳态触发器，fxsd 包发行号升为 2；Mobile signal 改为跟随蜂窝数据状态。文件管理模型修复 nixio open 权限的八进制表示，新增实际上传回调回归。原始上游归档、提交和二进制 DSP 补丁未更换。

## v1.7.7 新增组件

CoreMark 来自锁定 packages feed（eembc/coremark，Apache-2.0），MosDNS 5.3.4 来自锁定 helloworld feed（IrineSistiana/mosdns，GPL-3.0），OpenClash 0.47.156 来自锁定 LuCI feed（vernesong/OpenClash）；Mihomo 使用既有 helloworld 的 1.19.32。没有引入滚动 master 下载或不明来源的预编译核心。具体源码、许可证和下载校验继续由各 feed Makefile 管理。

## v1.7.8

xupnpd 来自锁定 packages feed，源提交 `2bc1e741e0efe04cb3150430ff25410093618b4f`（GPLv2）。LuCI xupnpd 管理页面为本工程生成，未引入额外滚动 feed。

## v1.10.0 初始化与语言支持

`aw1000-board-state`为本工程MIT许可的首次初始化组件，静态链接目标系统自带OpenSSL的libcrypto（Apache-2.0）与musl（MIT），不引入额外二进制下载。密钥由构建Secret生成，仅以拆分数组写入私有头文件，不能视为不可提取的加密存储。LuCI中英文切换使用上游gettext／po2lmo机制；英文为原文，简体中文为随包编译的LMO目录。
