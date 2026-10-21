# AW1000 ImmortalWrt · v1.10.2

适用于 **Arcadyan AW1000 / IPQ8072A / Quectel RG500Q-EA**。源码包目录 `aw1000-immortalwrt-v1.10.2/`，不含预编译整版固件，文件数控制在100以内。

## 功能

- **网络**：QModem、短信转发、mwan3-nft；仅有线网络（默认）、仅蜂窝、有线优先、蜂窝优先；DHCP／PPPoE。备用线路保持拨号和检测，仅用IPv4判断切换，保留双路IPv6。“上网方式”支持简体中文／英文，跟随LuCI系统语言。
- **无线**：WiFi按键切换、WPS配对；重启默认启用无线，状态灯常亮／熄灭。国家码固定CN，5GHz信道149。
- **代理**：SSR Plus+、OpenClash独立选装，含相应核心和依赖，启停互斥；MosDNS默认停用。
- **共享**：RJ11电话及中文页面、USB打印、Samba、FTP、文件管理、WireGuard、ZeroTier、国内源。xupnpd默认停用。
- **KMS**：vlmcsd默认开机启用，允许客户端自动发现，不允许互联网连接。
- **CoreMark**：下次北京时间05:00运行一次，成功后删除自身计划。

## GitHub编译

1. 将工程目录内全部文件上传仓库根目录，包含`.github`；`vendor`及历史验证归档保持原样。
2. 默认已内置与你内部GUI一致的主密钥，无需另设Secret。可在仓库 **Settings → Secrets and variables → Actions** 设置`BOARD_STATE_KEY`覆盖；`ROUTER_ADMIN_PASSWORD`用于指定root密码（自动取消组件）；`WIFI_PASSWORD`在WiFi输入框为空时生效。**本包包含真实共享密钥，仅放私有仓库。**
3. **Actions → Build AW1000 → Run workflow**：SSR Plus+、OpenClash分别勾选。root密码留空且未设`ROUTER_ADMIN_PASSWORD`时，自动加入IMEI密码组件；填写管理密码或设置该Secret时，不打包密码生成组件。WiFi密码在编译菜单填写，默认`1234567890`；清空输入后可使用`WIFI_PASSWORD` Secret，否则回退默认值。WiFi国家码固定CN，不再提供选择菜单。
4. 在Artifacts下载固件与校验记录。固件文件名带北京时间日期，构建摘要使用简体中文并标明组件选择及管理密码来源，不显示密码或密钥。

默认IP `192.168.1.1`，账号`root`，root初始密码默认由IMEI派生，WiFi默认密码`1234567890`，SSID `AW1000_2.4G`／`AW1000_5G`。`latest`选择正式版；复现验证环境可选`v25.12.2`。`check.yml`自动检查源码，完整编译需手动运行。

## 初始化组件

- 中性命名：包／服务`aw1000-board-state`，程序`/usr/libexec/aw1000-board/stage1`，状态`/etc/aw1000-board-state/ready`。
- 只在全新刷入／恢复出厂后初始化；IMEI模式初始化前root密码登录锁定，成功并核验后设置8位小写字母数字密码；指定密码模式直接设置指定密码，固件不包含该组件、密钥头文件或组件启动入口。保留配置升级不覆盖原密码。
- 成功后核对root密码哈希，再停用并删除可写层入口；失败保留组件和记录。**固件只读层及固件文件仍包含程序和嵌入密钥，不能等同于彻底卸载或安全擦除。** 名称调整也不构成密钥保护。
- C程序静态链接，不需另装OpenSSL或编译器。`tom_modem`、`uci`、`passwd`、`coreutils-timeout`由固件选入和校验；构建时在私有目录生成密钥头文件；源码中的内置密钥及固件仍可被提取。
- 所有设备共用主密钥，一台被提取可能影响全体。不要公开主密钥；只读固件中的嵌入密钥仍可能被分析提取。

本版默认值及兼容性验证见[VALIDATION.md](VALIDATION.md)；**未完成整版固件交叉编译和真机验收**。
