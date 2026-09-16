# Apple 上海直营店首发取货监控

Docker Compose 在本机持续检查 Apple 中国官网，只把 **2026 年 9 月 18 日** 上海直营店可取货视为命中。监控 iPhone 18 Pro、iPhone 18 Pro Max、iPhone Duo 的全部 40 个颜色与容量配置。

监控由容器 Firefox 执行，不依赖 Codex 定时任务，不消耗 Codex token。管理页面监听 `127.0.0.1:8765`，Firefox 的可视 noVNC 页面监听 `127.0.0.1:7900`，两个端口都不会暴露到局域网。

## 启动

前提：安装并启动 Docker Desktop。

```powershell
docker compose up -d --build
```

也可在 Windows 双击 `启动监控.cmd`，脚本会构建并启动容器，然后打开：

- 监控页面：<http://127.0.0.1:8765>
- 容器 Firefox：<http://127.0.0.1:7900/?autoconnect=true&resize=scale>

停止时运行 `docker compose down`，或双击 `停止监控.cmd`。SQLite 数据保存在 Docker 卷 `monitor_data` 中，普通停止不会删除。

## 使用

1. 页面显示“执行器在线”后，点击“开启到货通知”。脚本每轮检查 8 个配置，约 5 分钟覆盖全部 40 个配置。
2. 点击 Apple 账号按钮，在 noVNC 的 Firefox 页面登录。账号、密码、验证码和付款资料不会进入监控页面或镜像。
3. 出现 9 月 18 日可取货库存后，页面列出商品、价格、门店和取货日期并发送通知。
4. 点击“下单”，核对商品、门店、日期、数量 1 和最高金额，再点击“确认并准备购物袋”。容器会重新核验库存、选择门店并加入 Apple 购物袋。
5. 在容器 Firefox 中核对购物袋，手动完成最终订单提交和付款。脚本不会添加 AppleCare+，也不会自动付款。

监控门店：香港广场、南京东路、上海环贸 iapm、浦东、静安、环球港、五角场、七宝。Apple 没有显示门店取货入口时会记录为首发日暂无供应；入口恢复后会逐店读取实际日期。非 9 月 18 日库存不会触发通知或下单按钮。

## Docker Hub

预构建应用镜像可通过环境变量使用：

```powershell
$env:MONITOR_IMAGE='DOCKERHUB_USER/apple-store-launch-monitor:latest'
docker compose pull app
docker compose up -d
```

Firefox 使用 Selenium 官方镜像 `selenium/standalone-firefox`。应用镜像只包含页面、目录、监控逻辑和 Python 依赖，不包含任何 Apple 账号数据。

## 发布

GitHub Actions 工作流 `.github/workflows/docker-publish.yml` 会在推送 `v*` 标签或手动触发时构建 `linux/amd64` 与 `linux/arm64` 镜像。仓库需要设置：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

## 验证

```powershell
python -m py_compile web_server.py container_worker.py container_main.py sync_catalog.py
docker compose config
docker compose build app
```
