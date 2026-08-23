# GWM China (Tank / Haval) Home Assistant 集成

![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)
![Version](https://img.shields.io/github/v/release/chaosl1996/gwm_cn_ha?style=for-the-badge)
![Home Assistant](https://img.shields.io/badge/HA-2024.1%2B-41BDF5?style=for-the-badge)
![License](https://img.shields.io/github/license/chaosl1996/gwm_cn_ha?style=for-the-badge)

> 长城汽车(坦克/哈弗/长城炮)**中国云** Home Assistant 集成。
> 直接读取官方 APP 同一套 API 返回的真实车况数据,与你在「坦克 APP」→「车况数据」里看到的完全一致。

![logo](https://raw.githubusercontent.com/chaosl1996/gwm_cn_ha/main/icon.png)

---

## ✨ 功能

| 功能 | 说明 |
|---|---|
| 🏁 **车况数据全量同步** | 与 APP 车况页面完全对应:续航、油表、里程、温度、胎压胎温 |
| 🗺️ **GPS 定位追踪** | 在 HA 地图里看到车辆位置(顶级 `latitude/longitude`) |
| 🚪 **门/窗/锁/天窗** | 5 门 + 引擎盖 + 4 窗 + 天窗 + 中央门锁全状态 |
| ❄️ **空调 / 除霜 / 风挡加热** | 空调开关、AUTO 模式、前后除霜、前风挡加热丝 |
| 🔥 **座椅 + 方向盘加热** | 主副驾加热 + 通风 + 后排加热 + 方向盘加热 |
| ⚠️ **报警** | 4 轮胎压异常报警、防盗激活、油量低报警 |
| ⛽ **混动(PHEV/HEV)电池** | 高压动力电池电量百分比 + 纯电续航 |
| 🔄 **手动刷新服务** | `gwm_cn.refresh` 一键重拉最新数据 |

**支持车型**(理论上所有 GWM 中国区在售车型均可):
- TANK 300 / 300 HEV / 330 / 400 / 500 / 700
- Haval 枭龙 MAX / H6 / H6 GT / 大狗 / 酷狗
- 长城炮 / 山海炮
- 魏牌(蓝山/高山/摩卡)等,**需实测确认 header 里 brand 值**

---

## 📸 APP 数据对比(以 Tank 300 HEV 为例)

| 官方 APP 车况数据页 | HA 集成显示 |
|---|---|
| 续航里程:739 km | `综合续航`: 739 km |
| 行驶总里程:130 km | `行驶总里程`: 130 km |
| 8 格油表蓝条 + 剩余油量 77 L | `油表格数` 8 + `剩余油量` 77 L |
| 左上 275 kPa / 23 ℃ | `左前胎压` 275 kPa + `左前胎温` 23 ℃ |
| 右上 272 kPa / 24 ℃ | `右前胎压` 272 kPa + `右前胎温` 24 ℃ |
| 左下 273 kPa / 22 ℃ | `左后胎压` 273 kPa + `左后胎温` 22 ℃ |
| 右下 272 kPa / 23 ℃ | `右后胎压` 272 kPa + `右后胎温` 23 ℃ |
| 数据更新于 08-23 22:02:13 | `最后更新`: 2026-08-23 14:02:13 UTC |

---

## 🚀 安装

### 方式 A:HACS(推荐,可自动更新)

1. **打开 HACS → 集成 → ⋮ 右上角菜单 → 自定义存储库**
2. 填入:
   - **存储库 URL**:`https://github.com/chaosl1996/gwm_cn_ha`
   - **类别**: 集成
3. 点击「添加」
4. 在 HACS 里搜索 **GWM China (Tank/Haval)** → 点击「下载」(或「下载最新版本」)
5. **重启 Home Assistant**

### 方式 B:手动拷贝

1. 下载 [Latest Release](https://github.com/chaosl1996/gwm_cn_ha/releases/latest) 的 `gwm_cn.zip`
2. 解压后把 `custom_components/gwm_cn/` 整个目录拷贝到你 HA 的 `custom_components/` 目录(通常在 `/config/custom_components/`)
3. **重启 Home Assistant**

---

## ⚙️ 配置

### 前置:抓一次包获取 4 个参数

> ⚠️ **中国网关使用了华为云 WAF(防爬虫)+ JWT Token。
> 当前版本需要你手动从坦克 APP 抓一次包拿到 4 个值,有效期约 7 天。**
> 未来如拿到登录流程,会升级为账号密码自动登录。

**抓包步骤**(以 Stream / Charles / Thor HTTP Catcher 为例,手机 + PC 同 Wi-Fi):

1. 在手机上设置代理,安装抓包工具的 CA 证书
2. 打开**坦克 APP**(或「长城汽车」)
3. 找到 `apgdm.gwmcloudcn.com` 下的任意 HTTP 请求(通常叫 `getLastStatus`)
4. 从里面复制以下 4 个值:
   - `accessToken`(HTTP Header 里,以 `eyJ` 开头的长 JWT 字符串)
   - `HWWAFSESID`(Cookie 里的值)
   - `HWWAFSESTIME`(Cookie 里的值)
   - `VIN`(URL Query 参数里 `vin=LGWFF...` 的 17 位车架号)

### 添加集成

1. **设置 → 设备与服务 → 添加集成**,搜索「**GWM China**」
2. 依次填入刚才抓的 4 个值:
   | 表单项 | 填什么 |
   |---|---|
   | AccessToken (JWT) | Header 里 `accessToken`,以 `eyJ` 开头 |
   | HWWAFSESID | Cookie 中 `HWWAFSESID=` 的值 |
   | HWWAFSESTIME | Cookie 中 `HWWAFSESTIME=` 的值 |
   | 车辆 VIN (17 位) | 17 位车架号,如 `LGWFF7A56TJ049057` |
   | 车型名称(可选) | 显示用,如 `Tank 300 HEV` |
3. 完成后会出现一个设备,包含约 **49 个实体**(19 sensor + 29 binary_sensor + 1 tracker)

### Token 失效怎么办?

accessToken 是 JWT,当前抓的约 **7 天** 过期。过期后:
1. HA 集成会变成红色不可用,日志里会出现 `HTTP 401/403` 或 `code != 000000`
2. 不用删整个集成!**点击集成卡片 → 重新配置**,填入新的 token 和 cookie 即可

---

## 📋 完整实体清单

### 设备追踪器(1 个)

| 实体 ID | 名称 | 说明 |
|---|---|---|
| `device_tracker.gwm_xxx_vehicle_location` | 车辆位置 | GPS 定位,还带 8 个属性:行驶总里程/剩余油量/综合续航/混动电池/纯电续航/引擎状态/门锁/VIN |

### 传感器(19 个)

| 类型 | 名称 | 单位 | 说明 |
|---|---|---|---|
| 🟢 油电 | **剩余油量** | L | `remainOil` |
| 🟢 油电 | **综合续航** | km | `preMileage`,与 APP「续航里程」一致(油+电总续航) |
| 🟢 油电 | **油表格数** | 格 | `oilQty`,0-8 格蓝条 |
| 🟢 油电 | **行驶总里程** | km | `mileage`,与 APP「行驶总里程」一致 |
| 🔵 诊断 | **混动动力电池** | % | `remainElectricPercent`,高压动力电池(非 12V) |
| 🔵 诊断·默认隐藏 | **纯电续航** | km | `charge.evContnsDistance`,PHEV 有值,HEV 为 null |
| 🔵 诊断·默认隐藏 | **平均油耗** | L/km | `avgFuelConse`,很多时候返回 null |
| 🌡️ 温度 | **车厢温度** | ℃ | `cbnTemp` |
| 🚙 动力 | **引擎状态** | - | off/starting/running |
| 🚙 动力 | **档位** | - | P/R/N/D 等 |
| 🛞 轮胎 | **左/右/前/后胎压** | kPa | 4 个,与 APP 四角卡片对应 |
| 🛞 轮胎 | **左/右/前/后胎温** | ℃ | 4 个,与 APP 四角卡片对应 |
| 🔵 诊断 | **最后更新** | TIMESTAMP | 车辆数据采集时间 |
| 🔵 诊断 | **GPS 开关** | - | on/off/unknown |

### 二元传感器(29 个)

| 分类 | 名称 | device_class | 说明 |
|---|---|---|---|
| 🔒 锁 | **车门未锁** | LOCK(诊断) | on=未锁, off=已锁 |
| 🚪 门(6) | **左/右/前/后门 + 后备箱 + 引擎盖** | DOOR | on=开,off=关 |
| 🪟 门窗(5) | **4 车窗 + 天窗** | WINDOW / OPENING | `WinPosnSts=1` 关,`3` 开;`skyLightSts=3` 开/半开 |
| ❄️ 空调(5) | **空调** | RUNNING | 压缩机开关 |
| ❄️ 空调 | **空调自动模式** | RUNNING(诊断) | `airConditionAutoModEnaSts` |
| ❄️ 空调 | **前除霜** | HEAT | `frontFrost` |
| ❄️ 空调 | **后除霜** | HEAT | `backFrost` |
| ❄️ 空调 | **前风挡加热** | HEAT | `windows.fWinHeatSts` |
| 🔥 座椅(7) | **方向盘加热** + **主/副驾加热/通风** + **后排左/右加热** | HEAT/RUNNING | on=开 |
| ⚠️ 诊断·报警(6) | **4 胎压异常报警** | PROBLEM | on=异常(该轮胎压报警指示灯亮) |
| ⚠️ 诊断·报警(6) | **防盗激活** | PROBLEM | on=触发防盗 |
| ⚠️ 诊断·报警(6) | **油量报警** | PROBLEM | on=油量低 |

---

## 🔄 服务

| 服务名 | 作用 |
|---|---|
| `gwm_cn.refresh` | 立即从 GWM 中国网关拉一次最新车辆状态(忽略 coordinator 的缓存) |

可以在自动化里调用,比如:
- 车门开了 + 你离家 → 立即刷一次
- 每天定时刷(默认 coordinator 是 5 分钟 poll 一次)

---

## 🔧 工作原理

```
        ┌──────────────────────┐
        │   坦克 APP (手机)    │
        │  ↓ (HTTPS, 带签名)   │
        │ apgdm.gwmcloudcn.com │
        └──────────────────────┘
                    ↑
        ┌─────────────┴──────────────┐
        │   华为云 WAF (防爬虫层)    │
        └─────────────┬──────────────┘
                    ↑
        ┌─────────────┴──────────────┐
        │   GWM China HA Integration │
        │  ↓ 携带 accessToken +      │
        │  HWWAFSESID cookie 直查    │
        │  GET /getLastStatus?vin=   │
        └────────────────────────────┘
```

**为什么没有自动登录?**
GWM 中国网关的 `gwm-auth-sign` 签名使用的 HMAC secret 绑定 appkey `7863128529`,需要反编译官方 APP 才能拿到。
当前方案直接复用 APP 的会话(token + WAF cookie),绕开签名计算 — 代价是需要手动抓包。

如果有朋友拿到了登录流程抓包(特别是返回 `app_sec` / `secret` 的字段),欢迎提 Issue / PR,我来加自动登录功能。

---

## 🗺️ 路线图

- [x] v0.1.0:手动 token 模式,状态查询全量实体 ✅
- [ ] v0.2.0:支持自动登录(需登录流程抓包)
- [ ] v0.3.0:支持远程控制(锁门 / 鸣笛 / 预热 / 空调远程开等,需要更多控制接口抓包)
- [ ] v0.4.0:支持多车账号

---

## 🤝 贡献

欢迎 Issue 提问题 / 提 PR。
如果你有 GWM 其他车型(魏牌/长城炮等)的抓包数据,可以一起适配。

---

## ⚖️ 免责声明

本项目仅用于**个人合法获取自己车辆的状态数据**。
所有 API 均为 GWM 官方 APP 公开使用的 HTTPS 接口,没有任何逆向未授权功能。
使用本集成产生的任何后果由使用者自行承担。

---

## 📝 致谢

感谢以下开源项目提供了参考:
- [wad350/gwm_home_assistant](https://github.com/wad350/gwm_home_assistant) (RU 版,签名算法参考)
- [havaleiros/hassio-haval-h6-to-mqtt](https://github.com/havaleiros/hassio-haval-h6-to-mqtt) (BR 版,无签名 token 调用模式参考)
