# v1.10.2 当前验证

- root密码模式自动互斥：指定ADMIN_PASSWORD则不生成密码组件和密钥头文件；留空则自动启用IMEI初始化，初始化前root密码登录锁定。
- 菜单取消独立组件开关；管理输入值优先于ROUTER_ADMIN_PASSWORD Secret。两者均为空才使用IMEI。遗留INCLUDE_DEVICE_INIT变量不能覆盖密码模式。
- LAN仍为192.168.1.1；WiFi密码菜单默认1234567890，可在编译时指定。
- 本轮151项本机测试及GitHub语法检查通过；结果见validation/release-v1.10.2.json。尚未进行整版固件交叉编译或真机验收；下方旧版行为已被本版替代。

---

# v1.10.1 历史验证

- 默认LAN为192.168.1.1，管理／WiFi初始密码为password；本次输入优先，其次仓库Secret，缺省才使用内置值。
- 内置用户指定的共享密钥，BOARD_STATE_KEY非空时覆盖它。源码包仅适合私有仓库；组件不勾选时不生成密钥头文件和运行程序。
- IMEI组件启用时也生成初始管理密码哈希，成功核验后切换为派生密码；仍保留仅初始化一次及保留配置升级规则。
- 本轮150项本机测试、17项AArch64 QEMU测试及GitHub语法检查通过；结果见validation/release-v1.10.1.json。未进行整版固件交叉编译或真机验收。下面v1.10.0记录仅代表该版验证，不能视为本版重复全量编译。

---

# v1.10.0 历史验证

- 148项本机测试通过，包含初始化意图与WiFi配置完成顺序、保留升级配置、两种密码模式、旧标记显式修复、root哈希核验、静态ELF入口检查。
- 固定v25.12.2源码下，SSR Plus+／OpenClash／初始化组件共8种选装组合均通过defconfig及必选项保留检查；包数量见`validation/release-v1.10.0.json`。
- 中英文LuCI页面实际渲染测试通过；po2lmo真实编译中文翻译文件通过。KMS新安装配置为enabled=1、auto_activate=1、internet_access=0。
- AArch64 musl静态编译及QEMU下15项初始化／构建集成测试通过；测试使用公开测试密钥，不修改主机或设备root密码。
- GitHub check.yml语法步骤按ShellCheck 0.9执行通过。当时该版未内置真实主密钥，头文件仅由构建Secret生成（v1.10.1已按用户要求变更）。
- 本轮未进行完整固件编译、真实KMS客户端激活或AW1000启动验收。固件内置组件成功后只能移除可写层入口，只读镜像不会擦除。

先前散列验证记录集中保存在`validation/history-through-v1.9.0.tar.xz`；其内部还保留更早历史归档。下文旧版记录中的路径请从该归档查阅。

---

# v1.9.0 验证

- 工程版本更新至 1.9.0，源码 ZIP 顶层目录带版本号：aw1000-immortalwrt-v1.9.0。
- 交付目录 output 的固件在原文件名扩展名前增加 -YYYYMMDD，日期按收集成功时的 Asia/Shanghai 时区生成；源 bin 目录文件不修改，文件内容不改变。
- verification.json 记录交付文件名、原文件名、日期和时区；sha256sums 根据交付文件名生成，中文摘要自动显示新文件名和日期。
- 回归检查日期跨 UTC 零点时使用北京时间、实际复制的日期文件名、源镜像保留和所有输出校验值一致。具体结果见 validation/release-v1.9.0.json；尚未完成全量固件交叉编译或实机验证。

以下是历史记录：

# v1.8.5 验证

本次仅更新工程版本与显示名称：LuCI、说明文字和构建摘要将“仅有线”改为“仅有线网络”；wired_only 内部标识、默认模式、选路与双线在线逻辑不变。沿用 v1.8.3 的完整 127 项测试结果，本次复验摘要测试与完整语法步骤，不重复全量测试或固件编译。当前记录：validation/release-v1.8.5.json。

以下是历史记录：

# v1.8.3 验证

- 单线模式恢复两路 IPv4 默认路由，保留拨号、原生 mwan3 IPv4 探测及 IPv6 客户端；只使用 aw_failover 的单成员策略限制普通默认出口，保持 last_resort=unreachable。
- --tick 自动修复 v1.8.2 关闭的 defaultroute，不改变用户选择的单线路策略；重复同步不重复写配置／重载。
- 单元回归覆盖仅有线／仅蜂窝时两路 route=1、策略仍只有一个成员、恢复双线、后出现的蜂窝接口，以及旧配置修复后拨号和 tracker enabled 均保留。
- 已检查锁定 mwan3 源码的单成员与 last_resort 处理；没有在主机上操作路由／nft 内核或测试真实线路故障。具体结果见 validation/release-v1.8.3.json，仍需全量编译及设备验收。

以下是历史记录（v1.8.2 的路由抑制行为已由本版替代）：

# v1.8.2 验证

- LuCI 上网方式加入 wired_only（仅有线）、cellular_only（仅蜂窝），保留有线／蜂窝优先；新刷机默认仅有线。单线路策略只包含对应成员，last_resort=unreachable，不向另一线路回退。
- 单线路模式抑制另一接口的 IPv4 默认路由；延迟出现的蜂窝逻辑接口由串行 tick 同步。恢复双线模式重新允许两路默认路由。IPv6 客户端不主动停用，LAN IPv6 出口继续跟随已选策略。
- 原有自定义策略拒绝、120 秒确认／回退保留；验证单线路切换回双线不会被误认作自定义策略。
- 简体中文构建摘要包含工程／固件版本、设备、默认管理 IP／地址、账号、管理密码来源（无明文或哈希）、SSR Plus+／OpenClash 选择及固件文件名。保留配置升级沿用原 IP 和密码。
- 验证结果见 validation/release-v1.8.2.json；代理四组合依赖解析沿用 v1.8.1，未改变软件包选项。没有完成本版全量固件交叉编译、路由内核验证或上机测试。

以下为历史记录：

# v1.8.1 验证

- SSR Plus+ 与 OpenClash 是两个独立布尔编译参数，默认均选中；四种组合均通过真实源码 defconfig 和逐项配置保留检查，包数分别为 402 / 388 / 384 / 368。
- 不选 SSR Plus+ 时跳过 Xray 源码／Go 检查；不选 OpenClash 时不安装核心链接；两者都不选时 Mihomo 也不选中。共享依赖由 Kconfig 按需解析。
- 镜像收集按公开的 work/build-features.json 验收，拒绝带入未选中的插件，产物携带选项记录；模拟镜像覆盖不含代理和仅 OpenClash 的组合。
- 保留配置升级时，已卸载 OpenClash 的旧 UCI 配置不会阻止 SSR Plus+；安装了两个插件时仍实行启停互斥。
- 完整单元测试和语法检查结果见 validation/release-v1.8.1.json。没有进行四种组合的完整固件交叉编译或设备联网验收。

以下为历史记录：

# v1.8.0 验证

本次仅更新工程版本和精简 README，运行代码及配置选项保持不变。沿用 v1.7.10 的 117 项测试、401 包依赖解析与 ShellCheck 0.9.0／0.11.0 完整语法检查结果；未重新执行全量测试或固件编译。当前记录：`validation/release-v1.8.0.json`。

以下为历史记录：

# v1.7.10 验证

- Ubuntu ShellCheck 0.9.0 已重现旧版 SC2317；修正后以 0.9.0 和 0.11.0 执行完整 check.yml 语法步骤通过。
- 修改仅限 WiFi 启动脚本的静态检查标注和原因说明。WiFi/WPS 回调运行逻辑保持不变。
- 沿用 401 包依赖解析，完整测试及语法状态见 历史归档中的 `release-v1.7.10.json`；尚未完成固件全量交叉编译或实机验证。

以下是历史记录：

# v1.7.9 验证

117 项测试通过，401 个选中软件包，全部指定配置保留；完整 CI 语法步骤通过（ShellCheck 0.11.0），两个修改后的上游服务脚本 sh -n 通过。

- WiFi 关闭标记在无线查询仍返回 up=true 时也会使 WiFi 灯熄灭，清除后恢复实际 AP 绑定。
- 新增代理互斥测试：两个方向的启动拒绝、停止未运行插件不产生 DNS 清理副作用、停止过程中不能启动另一插件、并发启动仅一个成功、OpenClash enable=1 的开机优先规则；实际锁定源上的 init 锚点适配成功，sh -n 通过。
- xupnpd 首次默认 stop / disable；已启用后的普通重启不重新执行已完成的 uci-defaults。
- 完整测试及语法结果见 `validation/release-v1.7.9.json`。没有完成全量交叉编译、nft 内核转发、代理订阅联网或实体按键验证；模拟测试不能替代设备验收。

以下是历史记录：

# v1.7.8 验证

完整 113 项测试通过；实际源码 defconfig 解析 401 个包，全部指定选项保留；完整 CI 语法步骤通过（ShellCheck 0.11.0、sh -n、Node、Python、Lua）。

- 新增 xupnpd 自定义 LuCI 包生成、依赖和 JavaScript 语法检查；页面 ACL 只授权固定配置、固定播放列表和该服务脚本。
- CoreMark 回归：成功后删除自身 05:00 计划，保留其他计划；失败后计划仍在。使用 crontab 通知重载，避免从任务内部重启 cron。
- 测试、依赖解析及语法结果见 `validation/release-v1.7.8.json`。未完成本版固件全量交叉编译或 xupnpd/DLNA 设备播放验证。

以下是历史记录：

# v1.7.7 验证

- 完整测试 111 项通过；新增交付收集阶段拒绝缺失 OpenClash 核心链接，按下／松开事件、关闭／恢复序列、无 UCI 写入、重启开启、重复 CoreMark 与 feed 计划移除检查。
- 实际锁定源码 defconfig 通过，399 个软件包选中，全部指定配置保留。MosDNS 唯一来源 helloworld；OpenClash 来自既有锁定 LuCI feed。
- 完整 wpad-openssl 的 hostapd-full.config 含 CONFIG_WPS=y；DTS WLAN/WPS 与 gpio-button-hotplug 事件一致，wifi-scripts 提供 WPS handler。
- 新生成脚本通过 sh -n、ShellCheck；原 1.7.6 procd、上传提示、蜂窝状态和 LED 修复保留。
- 本版没有完成固件交叉编译或上机测试；不能将单元模拟和依赖解析当作实体按键、WPS 配对、MosDNS DoH 或 OpenClash/mwan3 转发验证。
- 待设备验收：连续两次 WiFi 短按、关闭后重启、关闭期间有线与蜂窝仍可用、双频 WPS、05:00 次日任务仍保留、代理单独开启及主备切换、DNS 无回环。

以下是历史验证记录：

# 当前版本验证：v1.7.6

- 合入 procd 请求隔离修复。新增回归先复现旧嵌套流程的空 set，再检查单次完整 set、单实例 add、停用清理不破坏外层请求、健康实例保留与失效实例重新启动。测试使用 procd／ubus 边界替身，不冒充真实 procd 进程验收。
- 新增完整 multipart 提交回归：模拟文件回调完成后 ulfile 普通 formvalue 为空，确认保留“File saved to”结果；空提交仍给出未选择文件提示。
- 用户已确认页面连接红点修复和上传提示修复成功；设备输出证明逻辑接口 4_1、物理设备 wwan0、IPv4 地址与默认路由有效，LED 诊断 cellular_led / signal_led 均为 true。电话服务先前显示 running / module / patch / enabled 为 true，但完整呼叫音频和新固件刷机仍未验收。
- 本轮发布记录见 validation/release-v1.7.6.json。以下保留 v1.7.5 的功能覆盖说明，完整固件编译结果以发布记录为准。

- 新增上传回调测试：实际执行 filetransfer.lua 的 handler，严格校验 nixio mode 为合法八进制表示，二进制分块与中文文件名正确，拒绝控制字符和符号链接目标；保留 APK/IPK 与 shell 字面文件名回归。
- 新增电话灯迁移测试：旧 v1.6.0 标记存在时仍启用 green:phone，保留 IPv6 原始快照；迁移只运行一次。手机信号灯在数据在线／离线时与 5G 灯同步，旧 RSRP 缓存不影响结果。
- 新增真实配置回归：实例 `4_1` 没有 alias，`usb0` 为 modem-slot，逻辑接口 `4_1` 为 qmodem_runtime、l3_device/device 为 `wwan0`；灯亮。错误 owner 拒绝点亮；无 alias 的模组按实例名登记到 mwan3，保留槽位 alias 与 APN。QModem 页面支持逻辑接口／设备不同名，受控适配共 14 项。
- no_modem 回归：没有 mwan3 登记时唯一 USB RG500Q-EA 自动发现；多个候选关闭并报告歧义；其他型号不误认；显式 SIM 查询绑定指定实例，避免借用其他模组状态。模组发现全过程不写 UCI 或修改路由。
- LED 扩展回归覆盖：明确 SIM 不可用仍关闭；未知＋新鲜在线 tracker 点亮；过期／离线 tracker 关闭；硬性排除抑制未知兜底；SIM 就绪的数据会话不因主备恢复等待而熄灯。保留 CM PID／代次、接口协议／设备／地址／路由和禁拨检查。
- 电话只检查了当前驱动／fxsd 输出接口，不宣称完成插头检测、线路测量或电话灯实机验证。
- 修复 v1.7.0 在 GitHub Ubuntu runner 的 SC2002／SC2015：已用 Ubuntu ShellCheck 0.9.0 重现原版两项失败，并确认修正版通过；0.11.0 也通过。按 check.yml 原文执行完整语法检查步骤。
- 完整单元／边界测试结果见 历史归档中的 `release-v1.7.6.json`。新覆盖：CPIN/CME 明确状态分类、未知不误切换、状态过期与 USB 代次隔离、SIM 排除与恢复、mwan3 原生策略不能重新选中已排除接口、失败动作重试、PPPoE 字面密码、优先级与 VLAN、非法配置／自定义策略拒绝、超时与重启回退、RPC 权限及生成源码语法。
- 保留 ABI 包名、Raw-IP、IPv6 双热备与 NAT66、稳态不反复 reload、电话 PCM 与 109 条简体中文、LED、文件上传等回归检查。
- 对实际锁定的 ImmortalWrt v25.12.2 与 feeds 执行受控兼容适配、原生 mwan3 mark／SSR 不重叠检查、WDA 报文编码宿主检查、defconfig 与所有指定选项保留核对。明确选择 PPPoE、LuCI PPP 协议包，收集阶段检查后端／页面／ACL 是否真正进入 rootfs。
- 对生成的 shell／LuCI JavaScript 运行语法检查与 ShellCheck。CI 同样生成检查，避免内嵌文件绕过检查。
- 本版没有完成固件全量交叉编译，也没有在 AW1000 上测得故障切换秒数；主机不允许操作 netlink，不能把命令模拟测试当作内核转发或 nft 实机验证。
- 待刷机验收：有线物理断开；有线仍有 carrier 但上游不可达；蜂窝主用时拔卡；AT 繁忙不误报无卡；双线同时失败后按不同顺序恢复；DHCP↔PPPoE 应用与未确认回退；两路 IPv6 始终在线且 LAN/DNS 跟随出口；VLAN 实际联网；电话通话与所有 LED 稳态。

以下仅是上一版本的历史验证说明，不作为本版新增功能的设备测试证明：

# 历史版本验证：v1.6.1

- 全部 85 项单元／边界测试通过。覆盖 IPv6 两路保持运行、原生前缀切换、无 PD NAT66、无健康线路阻断、选中线路无 IPv6 不跨线兜底、备用 DHCPv6 与路由器地址的窄范围豁免、USB 重新枚举、过期前缀、外部规则冲突、nft 事务失败、配置恢复、状态稳定时不反复 reload／清连接、LED 双频选择与无效项清理、信号缓存失效、QModem 会话状态判断及升级一次性迁移。继续保留 ABI 包名收集、Raw-IP、电话和文件上传回归测试。
- 新增电话中文覆盖与格式占位符校验、中文资源缺失拒绝交付、蜂窝灯在无 SIM／无会话／进程失效／接口不匹配时熄灭的测试。109 条翻译使用固定 LuCI 的真实 po2lmo 编译，并由同一上游 LMO 运行时逐条加载比对通过。
- 实际 ImmortalWrt v25.12.2 + 锁定 feeds 的 make defconfig 通过，384 个选中包，全部指定项保留；ip-full、kmod-nft-nat、mwan3、电话和打印组件均选中。
- 11 个结构受控适配已在固定上游源码上成功应用。原生 mwan3 标记隔离检查、真实 Quectel WDA 编码宿主测试通过。
- ShellCheck、Shell/Python/Lua/Node 语法检查通过。保留 v1.5.1 对 oniguruma5 的精确 ABI 匹配、共享库存在检查和模拟收集流程测试。
- 用户此前实机验证了 netdev WiFi 灯重启生效、蜂窝 IPv4 双目标零丢包，以及旧版切换至蜂窝后的 IPv6 Ping 零丢包。这些结果不代表新版 IPv6 热备策略已完成实机验收。
- 新版路由、nft NAT66、RA／客户端前缀更新仍需完整交叉编译与设备验证；本地规则测试使用受控命令边界替身，未验证内核实际转发。RJ11 音频与呼叫也仍需实机验收。

历史 v1.6.1 记录包含在 validation/history-through-v1.7.5.tar.xz。validation 中其他版本文件仅为历史，不能作为本版新逻辑的实机证明。

- 本版尚未完成完整固件交叉编译及设备刷机验证。中文已验证编译／加载，蜂窝灯逻辑已通过受控状态测试；实际拔插 SIM 和开机灯效仍需设备验证。
