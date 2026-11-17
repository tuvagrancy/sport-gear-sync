# Sport Gear Sync — 技术实现参考手册

> 本文档面向 AI agent 实现者。目标：把用户在 Strava 录入的运动装备信息，自动同步到 Garmin Connect、Stryd、Runalyze、Intervals.icu 四个平台。
>
> 参考实现：https://github.com/tuvagrancy/sport-gear-sync

---

## 目录

1. [系统架构](#1-系统架构)
2. [Strava — 装备来源](#2-strava--装备来源)
3. [Garmin Connect — 装备读写](#3-garmin-connect--装备读写)
4. [Stryd — 装备读写](#4-stryd--装备读写)
5. [Runalyze — 装备读写](#5-runalyze--装备读写)
6. [Intervals.icu — 装备读写](#6-intervalsicu--装备读写)
7. [跨平台活动匹配](#7-跨平台活动匹配)
8. [装备名称映射](#8-装备名称映射)
9. [同步流程](#9-同步流程)
10. [历史数据回填](#10-历史数据回填)

---

## 1. 系统架构

```
Strava API (OAuth2)
    │
    ▼
新活动列表 + gear_id
    │
    ├──► Garmin Connect (garminconnect Python 库)
    ├──► Stryd REST API  (Bearer token + Cognito refresh)
    ├──► Runalyze        (会话 Cookie + 表单提交)
    └──► Intervals.icu   (Basic Auth API Key)
```

**核心设计原则**
- Strava 是装备的唯一权威来源（用户手动录入）
- 其他平台按 Garmin 活动 ID 或时间+距离匹配活动
- 默认只读（dry run），加 `--apply` 才写入
- 写入前先读取当前值，相同则跳过（幂等）
- 写入后回读验证，不信服务端返回值

---

## 2. Strava — 装备来源

### 认证

OAuth2 PKCE flow，访问令牌有效期约 6 小时，用 `refresh_token` 自动续期。

**获取 refresh token**（首次）：
1. 引导用户访问授权 URL：
   ```
   https://www.strava.com/oauth/authorize
     ?client_id=<CLIENT_ID>
     &redirect_uri=http://localhost
     &response_type=code
     &scope=read,activity:read_all
     &approval_prompt=force
   ```
2. 用户授权后重定向到 `http://localhost?code=<CODE>`
3. 用 code 换 token：
   ```http
   POST https://www.strava.com/oauth/token
   Content-Type: application/x-www-form-urlencoded

   client_id=<ID>&client_secret=<SECRET>&code=<CODE>&grant_type=authorization_code
   ```
4. 响应含 `access_token`、`refresh_token`、`expires_at`

**续期**：
```http
POST https://www.strava.com/oauth/token

client_id=<ID>&client_secret=<SECRET>&refresh_token=<TOKEN>&grant_type=refresh_token
```

### 读取新活动

```http
GET https://www.strava.com/api/v3/athlete/activities
Authorization: Bearer <access_token>

?after=<unix_epoch>&per_page=100&page=1
```

关键字段：
```json
{
  "id": 12345678,
  "type": "Run",
  "start_date": "2026-11-15T10:30:00Z",
  "distance": 5500.0,
  "gear_id": "g12345678"
}
```

`type` 取值：`Run`, `TrailRun`, `Hike`, `Walk`（跑步类），`Ride`, `Virtual Ride`, `E-Bike Ride`（骑行类）

### 读取装备详情

```http
GET https://www.strava.com/api/v3/gear/<gear_id>
Authorization: Bearer <access_token>
```

响应：
```json
{
  "id": "g12345678",
  "name": "Nike Vomero Plus",
  "brand_name": "Nike",
  "model_name": "Vomero Plus",
  "distance": 1234567
}
```

### 读取所有跑鞋/骑行装备（全量）

```http
GET https://www.strava.com/api/v3/athlete
Authorization: Bearer <access_token>
```

响应含 `shoes` 数组（跑鞋）和 `bikes` 数组（骑行），结构同上。

### 历史全量回填

用 Strava 官方数据导出（Settings → My Account → Download or Delete Your Account → Request Your Archive），解压后有 `activities.csv`。

关键列：`Activity Date`（`Nov 15, 2026, 6:56:47 AM` 格式）、`Activity Type`、`Activity Gear`（装备名称字符串）、`Distance`（有两列，第一列是千米，最后一列是米）。

---

## 3. Garmin Connect — 装备读写

### 认证

使用 Python 库 `garminconnect`（pip install garminconnect），封装了 Garmin SSO 登录和 OAuth2 token 管理。

```python
from garminconnect import Garmin

g = Garmin(email="user@example.com", password="xxx", is_cn=False,
           prompt_mfa=lambda: input("MFA code: "))
g.login(tokenstore="~/.garminconnect")  # 缓存 token，30 天内免登录
```

### 读取装备列表

```python
# 读取所有装备（含已退役），名称字段：活跃装备用 name，部分鞋子用 brand
for status_filter in ({}, {"gear_statuses": ["RETIRED"]}):
    page = g.get_gear_list(start=0, limit=50, **status_filter)
    for item in page:
        name = item.get("name") or item.get("brand")
        uuid = item.get("uuid")
        is_retired = item.get("status") == "RETIRED"
```

注意：Garmin 给跑鞋创建时，名称可能存在 `brand` 字段而非 `name` 字段，需要两个都检查。

### 读取活动列表

```python
acts = g.get_activities_by_date("2026-11-01", "2026-11-30")
# 每条活动：{"activityId": 12345, "startTimeGMT": "2026-11-15 10:30:00", "distance": 5500.0, ...}
```

### 读取活动当前装备

```python
result = g.get_activity_gear(activity_id=12345)
# 递归遍历结果，收集所有 uuid/gearuuid 字段值
```

### 创建装备

```python
g.create_gear(
    gear_type="SHOES",         # 或 "BIKE"
    brand="Nike",
    model="Vomero Plus",
    name="Nike Vomero Plus",   # 显示名称
    first_use_date="2025-11-20",
    notes="Imported from Strava",  # 必填，不能为空字符串（服务端校验 1-2000 字符）
    usage_type="DISTANCE",
)
```

**注意**：`notes` 参数必须非空，空字符串会返回 `400 Bad Request`，这是 Garmin API 的一个已知限制。

### 关联装备到活动

```python
g.add_gear_to_activity(gearUUID="uuid-string", activity_id=12345)
```

### 取消关联

```python
g.remove_gear_from_activity(gearUUID="uuid-string", activity_id=12345)
```

### 安全的换装备流程

```
1. 读取活动当前装备 UUID 集合
2. 计算目标 UUID
3. 如果当前 == 目标，跳过
4. add_gear_to_activity(目标 UUID)  ← 先加
5. remove_gear_from_activity(旧 UUID) ← 后删
（先加后删确保活动不会出现无装备状态）
```

---

## 4. Stryd — 装备读写

### 认证

Stryd 使用 AWS Cognito。Token 存在浏览器 Local Storage（`https://www.stryd.com` → `token`），**有效期约 12 小时**，必须用 refresh token 自动刷新。

**首次获取**（浏览器手动）：
打开 stryd.com → DevTools → Application → Local Storage → 复制：
- `token`（access token）
- `refreshToken`
- `refreshClientId`

**自动刷新**：
```http
POST https://auth.stryd.com/oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&client_id=<refreshClientId>&refresh_token=<refreshToken>
```

响应：
```json
{
  "access_token": "eyJ...",
  "expires_in": 3600,
  "refresh_token": "新的refresh_token（Cognito会轮换）"
}
```

**每次调用前检查过期时间（留 5 分钟余量），过期则刷新，刷新后保存新 token。**

### 获取用户 ID

```http
GET https://api.stryd.com/b/api/v1/users/me
Authorization: Bearer <access_token>
```

响应含 `id` 字段（UUID 格式），后续所有接口用此 ID，**不能直接用字符串 "me"**（会被当作 int64 解析报错）。

### 读取跑鞋列表

```http
GET https://api.stryd.com/b/api/v1/users/<user_id>/apparel
Authorization: Bearer <access_token>
```

响应数组，每项关键字段：
```json
{
  "id": 5904582856048640,
  "nickname": "我的鞋子",
  "apparel_model": {"brand": "Nike", "name": "Vomero Plus", "type": "shoe"},
  "retired": false,
  "lifetime_distance": 123456.7,
  "distance_limit": 800000
}
```

### 创建跑鞋

```http
POST https://api.stryd.com/b/api/v1/users/<user_id>/apparel
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "apparel_model": {"brand": "Nike", "name": "Vomero Plus", "type": "shoe"},
  "distance_limit": 800000,
  "nickname": ""
}
```

响应含新建鞋子的完整对象（含 `id`）。

### 读取活动列表

```http
GET https://api.stryd.com/b/api/v1/users/<user_id>/calendar
Authorization: Bearer <access_token>

?from=<unix_epoch>&to=<unix_epoch>&include_deleted=false
```

响应是以日期字符串为 key 的对象，value 是活动数组。每条活动含 `id`、`timestamp`（unix epoch）、`distance`（米）、`apparel_ids`（数组，当前关联的鞋子 ID）。

### 关联鞋子到活动

```http
PUT https://api.stryd.com/b/api/v1/activities/<activity_id>
Authorization: Bearer <access_token>
Content-Type: application/json

{"apparel_ids": [5904582856048640]}
```

**注意**：
- `apparel_ids` 必须是 `array<int64>`，不能用字符串数字
- 清除关联用 `[]`
- 传不存在的 ID 会静默存入（返回 200 但数据无效），必须在写入前校验鞋子存在
- PUT 是部分更新语义，只传 `apparel_ids` 不影响其他字段
- 重复 PUT 相同值不会导致里程累加（里程是按活动-鞋子关联重算的）

---

## 5. Runalyze — 装备读写

### 认证

Runalyze 没有公开 API，使用网页会话（Symfony 表单登录）。

**登录流程**：
```
1. GET https://runalyze.com/login
   → 提取页面中的 _csrf_token：
     <input name="_csrf_token" value="xxx">

2. POST https://runalyze.com/login
   Content-Type: application/x-www-form-urlencoded
   Referer: https://runalyze.com/login

   _username=<email>&_password=<pass>&_csrf_token=<token>&_remember_me=on&submit=

3. 检查落地 URL 是否还在 /login，仍在则登录失败
4. 保存 session cookie 供后续复用
```

会话探活：`GET /activity/<已知活动ID>/edit`，返回 200 且页面含 `activity[equipment]` 字段则有效。

### 读取装备列表

```http
GET https://runalyze.com/_internal/data/equipment/combined
（需要登录会话）
```

响应：
```json
{
  "categories": [
    {"id": 392391, "name": "Shoes", ...},
    {"id": 392393, "name": "Bike", ...}
  ],
  "equipment": [
    {"id": 12345, "name": "Nike Vomero Plus", "category": 392391, "dateEnd": null}
  ]
}
```

**退役判断**：`dateEnd` 非空则已退役。

### 读取活动列表（全量）

```http
GET https://runalyze.com/_internal/data/activities/all
（需要登录会话）
```

返回 CSV 格式，关键列：
- `externalId`：Garmin 活动 ID（跨平台关联的关键键）
- `id`：Runalyze 内部活动 ID
- `time`：unix 时间戳，**数值上是 CST（UTC+8）墙钟时间**，换算日期时不要再加 8 小时

### 写入装备（表单提交）

Runalyze 没有 JSON 写入 API，必须模拟浏览器提交编辑表单：

```
1. GET https://runalyze.com/activity/<runalyze_activity_id>/edit
   → 解析 HTML 表单，收集所有 input/select/textarea 的 name 和当前值

2. 修改目标字段（装备选择）：
   - 跑鞋字段名：activity[equipment][392391]  （category id）
   - 骑行字段名：activity[equipment][392393]

3. POST https://runalyze.com/activity/<id>/edit
   Content-Type: application/x-www-form-urlencoded
   Referer: https://runalyze.com/activity/<id>/edit

   （所有字段原样回传，只修改装备字段，加上提交按钮 activity[saveAndClose]=1）

4. 回读验证：再次 GET 编辑页，检查装备字段当前值 == 目标值
```

**关键细节**：
- **必须原样回传所有字段**，只改装备字段。不完整的回传会导致活动其他属性被清空
- POST 返回 200 ≠ 写入成功，必须回读验证
- 徒步/步行活动的编辑页没有装备字段（`no_field`），这是 Runalyze 平台限制，只能跳过
- 复合运动（multisport）活动写入后服务端会忽略，不要反复重试
- 装备名称可能含零宽字符（`‌`、`﻿`），比较前先清洗

### 表单解析要点

```python
from html.parser import HTMLParser

# 收集规则：
# input[type=text/hidden/email/...]: name -> value
# input[type=checkbox]: 只有 checked 时才加入，value 或 "1"
# input[type=radio]: 只有 checked 时才加入
# select: name -> 选中的 option value
# textarea: name -> 文本内容（注意嵌套 HTML，建议用正则提取）
# input[type=submit]: 不加入 body，单独处理（优先 activity[saveAndClose]）
```

---

## 6. Intervals.icu — 装备读写

### 认证

HTTP Basic Auth，用户名固定为 `API_KEY`，密码为用户的 API Key。

```python
import base64
token = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}
```

API Key 在 [intervals.icu/settings](https://intervals.icu/settings) → Developer 区域获取，**永久有效，不需要刷新**。

### 读取装备列表

```http
GET https://intervals.icu/api/v1/athlete/<athlete_id>/gear
Authorization: Basic <token>
```

响应数组：
```json
[
  {"id": "g12345678", "name": "Nike Vomero Plus", "type": "Shoes", ...}
]
```

### 读取活动列表

```http
GET https://intervals.icu/api/v1/athlete/<athlete_id>/activities
Authorization: Basic <token>

?oldest=2026-01-01&newest=2026-12-31&limit=100&page=1
```

**注意**：一次性拉取全量会导致连接中途断开（`ChunkedEncodingError`），必须按年分页拉取，每页不超过 100 条。

每条活动关键字段：
```json
{
  "id": "12345678",
  "external_id": "garmin:9876543210",
  "start_date_local": "2026-11-15T18:30:00",
  "gear_id": "g12345678"
}
```

**活动匹配关键**：`external_id` 格式为 `garmin:<garmin_activity_id>`（也可能直接是数字字符串）。用这个字段做跨平台关联，比时间+距离匹配可靠得多。

### 读取单条活动

```http
GET https://intervals.icu/api/v1/athlete/<athlete_id>/activities/<activity_id>
Authorization: Basic <token>
```

### 写入装备

```http
PUT https://intervals.icu/api/v1/athlete/<athlete_id>/activities/<activity_id>
Authorization: Basic <token>
Content-Type: application/json

{"gear_id": "g12345678"}
```

PUT 是部分更新，只需传要改的字段。返回 200 或 204 表示成功。写入后回读验证 `gear_id` 字段。

---

## 7. 跨平台活动匹配

### 方式一：Garmin Activity ID（首选）

Runalyze 和 Intervals.icu 都存储了 Garmin 活动 ID：
- Runalyze CSV：`externalId` 列
- Intervals.icu：`external_id` 字段（格式 `garmin:12345678`）

流程：
```
Strava 活动 ──(时间+距离)──► Garmin 活动 ──(Garmin ID)──► Runalyze/Intervals 活动
```

### 方式二：时间 + 距离匹配（Garmin 和 Stryd 用）

```python
TIME_TOLERANCE = timedelta(minutes=3)
DISTANCE_TOLERANCE = 0.03  # 3%

def matches(strava_start_utc, strava_km, platform_start_utc, platform_km):
    if abs(platform_start_utc - strava_start_utc) > TIME_TOLERANCE:
        return False
    if strava_km and platform_km:
        if abs(platform_km - strava_km) / strava_km > DISTANCE_TOLERANCE:
            return False
    return True
```

**时区注意**：
- Strava `start_date` 是 UTC
- Garmin `startTimeGMT` 是 UTC（格式：`2026-11-15 10:30:00`）
- Stryd `timestamp` 是 unix epoch（UTC）
- Intervals.icu `start_date_local` 是本地时间，匹配时要尝试多种偏移（原始值 和 +8h）

### 匹配结果处理

- **唯一匹配（1 条）**：使用这条
- **无匹配（0 条）**：记录日志，跳过（不报错）
- **多匹配（2+ 条）**：记录日志，跳过（不猜测）

---

## 8. 装备名称映射

各平台显示的装备名称可能不同，需要维护映射表。

### Strava → Garmin/Intervals.icu/Runalyze

跑鞋：从 Strava 名称（含昵称）映射到"品牌 型号"格式：

```python
SHOE_MAP = {
    "HOKA Clifton 9 后备箱里的黑户": ("HOKA", "Clifton 9"),
    "Saucony Triumph20":             ("Saucony", "Triumph 20"),  # 合并拼写错误
    "Saucony Trumph 21SE":           ("Saucony", "Triumph 21SE"),
    # ...
}
# Garmin 装备显示名 = f"{brand} {model}"
```

骑行装备（名称可能不同）：

```python
BIKE_NAME_MAP = {
    "Scultura 4000": "Daredevil",  # Strava名 -> Garmin显示名
}
```

### Strava → Stryd

Stryd 使用数字 ID（账号专属），需要先在 Stryd 创建装备，再建立映射：

```python
SHOE_ID_MAP = {
    "HOKA Clifton 9 后备箱里的黑户": 4741199595208704,  # Stryd shoe id
    # ...
}
```

### 名称清洗

Garmin 装备名称可能含零宽字符：

```python
name = name.replace('‌', '').replace('﻿', '').strip()
```

---

## 9. 同步流程

### 增量同步（日常）

```
1. 读取 last_sync_epoch（上次同步时间）
2. 拉取 Strava 新活动（after=last_sync_epoch）
3. 筛选有 gear_id 的活动，解析装备名
4. 拉取各平台同一时间窗口的活动
5. 对每条 Strava 活动：
   a. 匹配 Garmin 活动（时间+距离）
   b. 同步 Garmin 装备
   c. 匹配 Stryd 活动（时间+距离，仅跑步）
   d. 同步 Stryd 装备
   e. 用 Garmin ID 查 Runalyze 活动，同步装备
   f. 用 Garmin ID 查 Intervals.icu 活动，同步装备
6. 更新 last_sync_epoch = now()
```

### 写入前的通用检查

```python
# 1. 目标装备存在且未退役
if gear_name not in gear_map:
    return "no-gear"
if is_retired(gear_name):
    return "retired-skip"

# 2. 读取当前值
current = read_current_gear(activity_id)

# 3. 已经正确，跳过
if current == target:
    return "already-correct"

# 4. 写入
write_gear(activity_id, target_gear)

# 5. 回读验证
verify = read_current_gear(activity_id)
if verify != target:
    return "verify-failed"
return "linked"
```

---

## 10. 历史数据回填

### 使用 Strava 数据导出（全量）

从 Strava 导出的 `activities.csv` 包含所有历史活动。

CSV 日期格式：`Nov 15, 2026, 6:56:47 AM`（UTC）

距离列：有两个 `Distance` 列，第一列是千米（粗略），最后一列是米（精确）。**始终用最后一列÷1000 换算为千米**。

### Garmin 历史回填

通过 `get_activities_by_date` 分段拉取 Garmin 活动，然后用 CSV 活动按时间+距离匹配，批量设置装备。

### Intervals.icu 历史回填

1. **按年分页拉取**所有活动（每年一批，每批 ≤100 条），建立 `garmin_id -> intervals_activity` 映射
2. 对每条 Strava CSV 活动，从映射里找对应的 Intervals.icu 活动
3. 比较当前装备，不同则 PUT 更新

注意：Intervals.icu 的 `start_date_local` 是本地时间，匹配时需要同时尝试 CSV 的 UTC 时间和 UTC+8，取差值最小的那条（容差 5 分钟）。

### Runalyze 历史回填

1. `GET /_internal/data/activities/all` 拉取全量 CSV，建立 `garmin_id -> runalyze_id` 映射
2. 通过 Garmin ID 直接定位 Runalyze 活动（无需时间距离匹配）
3. 对每条活动提交编辑表单

节奏控制：每条活动写入后等待 0.4 秒，避免触发限流。Runalyze 对频繁请求敏感。

---

## 附录：已知限制

| 平台 | 限制 |
|------|------|
| Garmin | 创建装备时 `notes` 字段必须非空（1-2000 字符），否则 400 |
| Stryd | access token 有效期约 12 小时，必须实现 Cognito 自动刷新 |
| Stryd | 一条活动只能关联一双鞋（apparel_ids 传多个时行为未验证）|
| Runalyze | 徒步/步行活动没有装备字段（平台限制），无法绑装备 |
| Runalyze | 复合运动（multisport）活动写入后服务端静默忽略装备值 |
| Runalyze | POST 返回 200 不等于写入成功，必须回读验证 |
| Runalyze | 偶尔被 Cloudflare 人机验证拦截（全新登录时），复用会话缓存通常不受影响 |
| Intervals.icu | 一次性全量拉取活动会 ChunkedEncodingError，必须按年分页 |

---

## 附录：状态文件结构

建议用 JSON 文件持久化以下状态：

```json
{
  "strava_client_id": "...",
  "strava_client_secret": "...",
  "strava_access_token": "...",
  "strava_refresh_token": "...",
  "strava_expires_at": 1700000000,

  "stryd_token": "eyJ...",
  "stryd_refresh_token": "...",
  "stryd_refresh_client_id": "...",
  "stryd_expires_at": 1700000000,
  "stryd_user_id": "uuid-string",

  "intervals_athlete_id": "i116077",
  "intervals_api_key": "...",

  "runalyze_shoe_cat": 392391,
  "runalyze_bike_cat": 392393,

  "last_sync_epoch": 1700000000
}
```

Runalyze 凭证（用户名/密码）单独存在 `~/.runalyze/credentials.json`（chmod 0600）。
Garmin 会话 token 由 garminconnect 库自动管理，存在 `~/.garminconnect/`。
