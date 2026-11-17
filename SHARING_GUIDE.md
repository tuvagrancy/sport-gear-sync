# Strava Gear Auto Sync — 分享版使用说明

把你在 Strava 录入的运动装备自动同步到 Garmin Connect 和 Stryd，无需手动操作。

---

## 目录

- [Windows 直接运行（exe 版）](#windows-直接运行exe-版)
- [Python 脚本版](#python-脚本版)
- [NAS Docker 部署版](#nas-docker-部署版)
- [首次配置](#首次配置)
- [日常使用](#日常使用)
- [添加新鞋](#添加新鞋)
- [常见问题](#常见问题)

---

## Windows 直接运行（exe 版）

无需安装 Python，双击或命令行运行。

**下载**：从分享包里取 `gear-sync.exe`，放到任意目录，例如 `C:\gear-sync\`。

```powershell
# 首次配置
.\gear-sync.exe --setup

# 预览（不写入）
.\gear-sync.exe

# 正式同步
.\gear-sync.exe --apply
```

---

## Python 脚本版

**系统要求**：Python 3.10 或以上

**安装依赖**：

```powershell
pip install -r requirements.txt
```

**运行**：

```powershell
# 首次配置
python -I auto_sync.py --setup

# 预览（不写入）
python -I auto_sync.py

# 正式同步
python -I auto_sync.py --apply
```

---

## NAS Docker 部署版

适合群晖 NAS 或任何支持 Docker 的 Linux 机器，构建一次后长期运行，可通过定时任务每小时自动同步。

### 1. 把文件传到 NAS

```bash
# 在 NAS 上建目录
ssh tuvagrancy@<NAS-IP> "mkdir -p /volume3/docker/gear-sync/data /volume3/docker/gear-sync/garminconnect"

# 传文件（群晖需要 -O 参数）
scp -O Dockerfile docker-compose.yml auto_sync.py sync_gear.py sync_stryd.py requirements.txt \
    tuvagrancy@<NAS-IP>:/volume3/docker/gear-sync/
```

### 2. 构建镜像

```bash
ssh tuvagrancy@<NAS-IP>
cd /volume3/docker/gear-sync
sudo /usr/local/bin/docker build -t gear-sync:latest .
```

### 3. 首次配置（交互式）

```bash
sudo /usr/local/bin/docker run -it --rm \
  -v /volume3/docker/gear-sync/data:/app/data \
  -v /volume3/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --setup
```

### 4. 预览

```bash
sudo /usr/local/bin/docker run -it --rm \
  -v /volume3/docker/gear-sync/data:/app/data \
  -v /volume3/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py
```

### 5. 正式同步

```bash
sudo /usr/local/bin/docker run --rm \
  -v /volume3/docker/gear-sync/data:/app/data \
  -v /volume3/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --apply
```

### 6. 设置定时任务（每小时自动同步）

在群晖 DSM 里：**控制面板 → 任务计划 → 新增 → 计划的任务 → 用户定义的脚本**

- 用户：`root`
- 计划：每小时
- 脚本：

```bash
/usr/local/bin/docker run --rm \
  -v /volume3/docker/gear-sync/data:/app/data \
  -v /volume3/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --apply
```

---

## 首次配置

无论使用哪种方式，第一次运行都需要 `--setup`，向导会引导你完成：

### 1. Strava OAuth

1. 进入 [strava.com/settings/api](https://www.strava.com/settings/api)，创建一个应用
2. `Authorization Callback Domain` 填 `localhost`
3. 把 **Client ID** 和 **Client Secret** 粘进来
4. 向导会给你一个授权链接，在浏览器里打开并授权
5. 授权后浏览器会跳转到 `localhost`（页面打不开是正常的），把地址栏的完整 URL 粘回来

### 2. Stryd Token

1. 在浏览器打开 [stryd.com](https://www.stryd.com) 并登录
2. 按 F12 → Application → Local Storage → `https://www.stryd.com` → 找到 `token` 条目
3. 复制 Value（`eyJ...` 开头的一长串）粘进来

### 3. Runalyze

在向导里输入 runalyze.com 的用户名和密码，脚本会自动完成登录验证并识别装备类别 ID（鞋类、骑行类）。凭证存在 `~/.runalyze/credentials.json`，只有当前系统用户可读。

如果自动识别类别失败，向导会提示手动输入类别 ID。在浏览器里打开 `https://runalyze.com/_internal/data/equipment/combined`，在返回的 JSON 里找 `categories` 数组，记下鞋类和骑行类的 `id` 字段值即可。

### 4. Garmin

首次运行时脚本会在终端里提示输入邮箱和密码，以及邮件验证码（如果 Garmin 要求）。登录成功后凭证缓存 30 天，期间不再需要输入。

---

## 日常使用

在 Strava 录入装备后，运行：

```
gear-sync.exe --apply           # Windows exe
python -I auto_sync.py --apply  # Python 脚本
```

脚本只处理上次同步以来的新活动，重复运行安全。

### 补同步漏掉的活动

```
gear-sync.exe --reset-since 30 --apply
python -I auto_sync.py --reset-since 30 --apply
```

`30` 表示重新处理最近 30 天，可以换成其他天数。

---

## 添加新鞋

在 Strava 新增一双鞋后，需要：

1. 在 **Garmin Connect** 网页手动添加这双鞋（装备页面）
2. 在 **Stryd** 网页手动添加这双鞋（My Shoes 页面），或运行 `create_stryd_shoes.py` 批量创建
3. 更新 `auto_sync.py` 开头的两个映射表：

**`SHOE_MAP`**（Garmin 用）：

```python
"Strava 里的鞋名": ("品牌", "型号"),
```

**`SHOE_ID_MAP`**（Stryd 用）：

```python
"Strava 里的鞋名": 1234567890123456,  # Stryd 鞋子 ID
```

Stryd 鞋子 ID 可以运行以下命令查看：

```
python -I sync_stryd.py --list-shoes
```

如果使用 exe 版，需要修改源码后重新打包：

```powershell
python -m PyInstaller --onefile --name gear-sync --clean --noconfirm auto_sync.py
```

打好的 exe 在 `dist\gear-sync.exe`。

---

## Stryd Token 过期处理

Stryd token 有效期约 30 天，过期后脚本会打印：

```
Stryd token expired — skipping.
```

Garmin 仍然正常同步。要恢复 Stryd 同步，重新在浏览器里复制 token 后运行 `--setup`，其他选项直接按回车跳过。

---

## 常见问题

**Q: 活动录了装备但没有同步**
确认 Strava 里已保存装备，然后用 `--reset-since 3` 重新处理最近 3 天。

**Q: 某条活动在 Garmin 或 Stryd 里找不到匹配**
脚本按开始时间（相差 ≤3 分钟）和距离（相差 ≤3%）匹配。Stryd 只记录带足垫的跑步，没带足垫的活动不会出现在 Stryd 里，这是正常现象。

**Q: Garmin 提示需要验证码**
在终端里输入邮件收到的验证码即可，之后 30 天内不再需要。

**Q: Docker 容器里 Garmin 要求验证码，但容器不是交互式的**
用 `-it` 参数运行一次完成登录：
```bash
sudo /usr/local/bin/docker run -it --rm \
  -v /volume3/docker/gear-sync/data:/app/data \
  -v /volume3/docker/gear-sync/garminconnect:/app/garminconnect \
  -e STATE_FILE=/app/data/auto_sync_state.json \
  -e GARMIN_TOKENS=/app/garminconnect \
  gear-sync:latest \
  python -I auto_sync.py --apply
```
登录成功后凭证缓存在 volume 里，后续定时任务不再需要交互。
