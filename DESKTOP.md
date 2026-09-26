# Windows 桌面版

双击 `AppleStoreMonitor.exe`，选择电脑已安装的 Edge 或 Chrome，以及目标取货日期，点击“启动监控”。程序会在默认浏览器打开本机监控页面，并启动所选浏览器的专用窗口执行检查。无需 Docker、Firefox 或 Python。

首次运行需联网下载匹配的 WebDriver 驱动（不是浏览器）。程序不接管日常浏览器窗口，也不复制日常浏览器的密码或 Cookie；请通过页面“打开账号页”在专用窗口登录 Apple。登录资料保存在 `%LOCALAPPDATA%\AppleStoreMonitor\profile-Edge` 或 `profile-Chrome`。

请保持 EXE 和专用浏览器运行。关闭 EXE 会停止监控并关闭它启动的浏览器。浏览器通知需要在页面中点击开启并授权。电脑休眠或关机后不会继续监控。

点击有货选项后仍需确认具体商品、门店和金额。程序复核库存并准备购物袋，最终订单提交和付款由你在 Apple 官网完成。不保证锁定库存。

原项目仅针对 2026/09/18；桌面版允许选择今天或未来的目标日期，每个日期使用独立数据库。商品目录沿用现有 40 个配置，实际官网有效性和实时库存需要联网运行核验。

启动失败时查看窗口“打开数据与日志目录”中的 `desktop.log`。EXE 未做代码签名。

开发者在 Windows 执行 `powershell -ExecutionPolicy Bypass -File build-desktop.ps1`，产物位于 `dist/AppleStoreMonitor.exe`。
