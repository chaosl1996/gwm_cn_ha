"""GWM China API Client (token-based, no signing).

该客户端直接复用从官方 APP 抓包到的 accessToken 与华为云 WAF 会话 Cookie,
不进行 gwm-auth-sign 签名。优点是无需逆向 APP 获取 app_sec,
代价是 token 失效(约 7 天)后需要用户重新抓包更新配置。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .const import BASE_URL, GET_STATUS_PATH

_LOGGER = logging.getLogger(__name__)


def _parse_value_unit(value_str: Any) -> Optional[float]:
    """解析 CN API 的 'value,unit' 格式(如 '273,kPa' / '77,L' / '80,%')。

    返回纯数值部分;解析失败返回 None。
    """
    if value_str is None:
        return None
    if isinstance(value_str, (int, float)):
        return float(value_str)
    if not isinstance(value_str, str):
        return None
    s = value_str.strip()
    if not s or s in ("--", "null", "NULL"):
        return None
    # 取逗号前的数值部分
    head = s.split(",", 1)[0]
    try:
        return float(head)
    except ValueError:
        return None


def _str_to_bool(
    value: Any,
    *,
    true_vals: tuple[str, ...] = ("1",),
    false_vals: tuple[str, ...] = ("0",),
) -> Optional[bool]:
    """把字符串状态值转为 bool。

    多数 CN 字段:'1' = 开/激活,'0' = 关/未激活。
    对于门窗等多态字段(如 WinPosnSts:"1"=关,"3"=开),
    可以通过 true_vals + false_vals 明确指定两态值集合,
    未命中集合的值视为 None(未知,不在 HA 上显示瞎猜结果)。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        s = str(int(value))
    elif isinstance(value, str):
        s = value.strip()
    else:
        return None
    if s in ("--", "null", "NULL", ""):
        return None
    if s in true_vals:
        return True
    if s in false_vals:
        return False
    return None


def _window_pos_to_bool(value: Any, closed_val: str) -> Optional[bool]:
    """车窗/天窗位置多态字段转 bool。

    语义(参考 ha-gwm-ev 项目对同平台 BeanTech 的逆向结论):
      指定的 closed_val = 关(实测:车窗"1"关/天窗"3"关),
      其余任何有效数值 = 开(全开/半开/翘起等中间态),
      null/"--" = None 未知。

    用户实测:开窗后值并不是"3"(旧映射导致显示未知),
    正确逻辑是「非关闭值即开」。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        s = str(int(value))
    elif isinstance(value, str):
        s = value.strip()
    else:
        return None
    if s in ("--", "null", "NULL", ""):
        return None
    if s == closed_val:
        return False
    return True


def _str_to_bool_inverted(value: Any) -> Optional[bool]:
    """反向语义:'0' 视为 True(已锁/已关),'1' 视为 False。

    用于 mainDrveDoorLockSts 这种 '0'=已锁 的字段。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value == 0
    if not isinstance(value, str):
        return None
    s = value.strip()
    if s in ("--", "null", "NULL", ""):
        return None
    if s == "0":
        return True
    if s == "1":
        return False
    return None


class GWMChinaClient:
    """GWM 中国网关车辆状态查询客户端(token + WAF cookie)。"""

    def __init__(
        self,
        access_token: str,
        hw_waf_ses_id: str,
        hw_waf_ses_time: str,
        verify_ssl: bool = True,
    ) -> None:
        """初始化客户端。

        :param access_token: JWT accessToken(从抓包工具复制)
        :param hw_waf_ses_id: 华为云 WAF HWWAFSESID cookie 值
        :param hw_waf_ses_time: 华为云 WAF HWWAFSESTIME cookie 值
        :param verify_ssl: 是否校验 SSL 证书(生产保持 True,仅在调试时关闭)
        """
        self.session = requests.Session()
        self.access_token = access_token
        self.hw_waf_ses_id = hw_waf_ses_id
        self.hw_waf_ses_time = hw_waf_ses_time
        self.verify_ssl = verify_ssl
        self.last_error_code: Optional[str] = None
        self.last_error_description: Optional[str] = None
        self.last_http_status: Optional[int] = None

    def _build_headers(self) -> Dict[str, str]:
        """构造请求 header(基于多次 HAR 抓包确认的最小集合)。"""
        return {
            "Host": "apgdm.gwmcloudcn.com",
            "sourceApp": "GWM",
            "language": "zh-cn",
            "User-Agent": (
                "GWmSuperCarWidgetExtension/75 "
                "CFNetwork/3860.700.1 Darwin/25.6.0"
            ),
            "Cookie": (
                f"HWWAFSESID={self.hw_waf_ses_id}; "
                f"HWWAFSESTIME={self.hw_waf_ses_time}"
            ),
            "brand": "10",
            "channel": "APP",
            "cVer": "2.1.5",
            "accessToken": self.access_token,
            "rs": "2",
            "terminal": "GW_APP_GWM",
            "securityToken": "",
            "Connection": "keep-alive",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "Accept": "*/*",
            "Content-Type": "application/json",
            "enterpriseId": "CC01",
            "Accept-Encoding": "gzip, deflate, br",
        }

    def get_vehicle_status(self, vin: str) -> Optional[Dict[str, Any]]:
        """查询车辆最新状态。

        :param vin: 17 位车架号
        :return: 成功时返回响应 data 字段(扁平 JSON);失败返回 None
        """
        url = f"{BASE_URL}{GET_STATUS_PATH}"
        params = {"vin": vin}
        headers = self._build_headers()

        try:
            response = self.session.get(
                url, headers=headers, params=params, timeout=30,
                verify=self.verify_ssl,
            )
            self.last_http_status = response.status_code

            if response.status_code != 200:
                body = response.text[:500] if response.text else "Empty body"
                self.last_error_code = "http_error"
                self.last_error_description = f"HTTP {response.status_code}: {body}"
                _LOGGER.error(
                    "GWM CN API HTTP %s: %s",
                    response.status_code,
                    body,
                )
                return None

            result = response.json() if response.text else {}

        except requests.exceptions.RequestException as exc:
            self.last_error_code = "request_error"
            self.last_error_description = str(exc)
            _LOGGER.exception("GWM CN API request failed: %s", exc)
            return None
        except ValueError as exc:
            self.last_error_code = "json_error"
            self.last_error_description = str(exc)
            _LOGGER.exception("GWM CN API JSON decode failed: %s", exc)
            return None

        code = result.get("code")
        if code != "000000":
            self.last_error_code = str(code)
            self.last_error_description = result.get(
                "description", "Unknown error"
            )
            _LOGGER.warning(
                "GWM CN API error code=%s description=%s",
                code,
                self.last_error_description,
            )
            return None

        # 成功
        self.last_error_code = None
        self.last_error_description = None
        return result.get("data")


def _norm_keys(obj: Any) -> Any:
    """递归把所有 dict key 转小写。

    v2.0 网关(apgdm)返回 camelCase,v3.0 网关(gw-app-gateway)
    返回的 key 大小写不一致,统一转小写后用小写 key 解析,两版通吃。
    """
    if isinstance(obj, dict):
        return {k.lower(): _norm_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_keys(i) for i in obj]
    return obj


def _first(*values: Any) -> Any:
    """返回第一个非 None 的值(新旧网关字段名/层级兜底)。"""
    for v in values:
        if v is not None:
            return v
    return None


def parse_vehicle_status(data: Dict[str, Any]) -> Dict[str, Any]:
    """解析 CN 版车辆状态数据(扁平 JSON 结构)。

    字段语义统一约定:
      - bool 字段:True=激活/打开,False=未激活/关闭,None=未知
      - doors_locked:True=已锁,False=未锁
      - engine_state:"0"=熄火,"1"=启动中,"2"=运行
      - 灯光/能耗/电池电压等字段熄火时为 null/"--",启动后才有值(None 时实体显示未知)
      - v2.0/v3.0 网关 key 大小写不一致:先统一转小写再解析
      - gtsp 平台(2026 款坦克300 实测)若干字段改名/挪层,用 _first 兜底:
          车厢温度 cbnTemp→inCarTemperature,档位 hcuGearSts→gearSts,
          油表/GPS开关/TBOX 从顶层挪入 vehicleStatusInfo,胎压报警
          *TirePressIndcrSts→*TirePressSts,方向盘加热 steerWheelHeat→steerWheelHeatDst
    """
    info: Dict[str, Any] = {}

    if not data:
        return info

    data = _norm_keys(data)

    vs = data.get("vehiclestatusinfo") or {}

    # ===== 顶级字段(v3.0/gtsp 把油表/GPS开关/TBOX 挪进了 vehicleStatusInfo) =====
    info["acquisition_time"] = _first(data.get("acquisitiontime"), vs.get("acquisitiontime"))
    info["gps_switch_on"] = _first(vs.get("gpsswitchon"), data.get("gpsswitchon"))
    info["tbox_status"] = _first(data.get("tboxstatus"), vs.get("tboxstate"), vs.get("tboxstatus"))
    info["fuel_gauge"] = _first(vs.get("oilqty"), data.get("oilqty"))  # 0-8 格油表

    # ===== 油电 =====
    # remainOil: 剩余油量(L)
    info["fuel_volume"] = _parse_value_unit(vs.get("remainoil"))
    # preMileage(顶级):综合续航(油+电)km,与 APP 截图「续航里程」一致
    info["fuel_range"] = _parse_value_unit(vs.get("premileage"))
    # remainElectricPercent: PHEV 电池电量 %(不是油表油量!)
    info["battery_percent"] = _parse_value_unit(vs.get("remainelectricpercent"))
    # mileage:行驶总里程 km(与 APP 截图一致)
    info["mileage"] = _parse_value_unit(vs.get("mileage"))
    # charge.evContnsDistance:纯电续航 km(仅 PHEV 有,HEV 此值 null)
    charge = vs.get("charge") or {}
    info["ev_range"] = _parse_value_unit(charge.get("evcontnsdistance"))
    # 额外实用:平均油耗(如有)
    info["avg_fuel_consumption"] = _parse_value_unit(vs.get("avgfuelconse"))
    # 平均能耗 avrgEgyCns(混动的电耗,行驶后有值;熄火停车时为 null)
    info["avg_energy_consumption"] = _parse_value_unit(vs.get("avrgegycns"))
    # 动力电池电压 bmsPackVolt(上电/充电后有值;熄火停车时为 null)
    info["battery_voltage"] = _parse_value_unit(vs.get("bmspackvolt"))

    # ===== 温度(gtsp 改名 inCarTemperature) =====
    info["cabin_temp"] = _parse_value_unit(_first(vs.get("cbntemp"), vs.get("incartemperature")))

    # ===== 引擎 / 档位(gtsp 档位改名 gearSts) =====
    info["engine_state"] = vs.get("enginests")
    info["power_state"] = vs.get("power")
    info["gear"] = _first(vs.get("hcugearsts"), vs.get("gearsts"))

    # ===== 门锁 =====
    # esclLocksts 或 mainDrveDoorLockSts:"0"=已锁
    info["doors_locked"] = _str_to_bool_inverted(vs.get("escllocksts"))
    if info["doors_locked"] is None:
        door = vs.get("door") or {}
        info["doors_locked"] = _str_to_bool_inverted(
            door.get("maindrvedoorlocksts")
        )

    # ===== 车门("1"=开,"0"=关) =====
    door = vs.get("door") or {}
    info["door_front_left"] = _str_to_bool(door.get("maindrvedoorsts"))
    info["door_front_right"] = _str_to_bool(door.get("vicedoorsts"))
    info["door_rear_left"] = _str_to_bool(door.get("lbdoorsts"))
    info["door_rear_right"] = _str_to_bool(door.get("rbdoorsts"))
    info["door_trunk"] = _str_to_bool(_first(door.get("backdoorsts"), door.get("tailgateopenupsts")))
    info["hood"] = _str_to_bool(vs.get("enginedoorsts"))

    # ===== 车窗 / 天窗 =====
    # *WinPosnSts: "1"=关,其余值=开(实测关窗为"1";开窗的值非"3"故旧映射显示未知,
    #   参考同平台逆向项目 ha-gwm-ev: 1=关,其余≥0=开)
    # skyLightSts: "3"=关(锁车熄火实测),其余值=开
    windows = vs.get("windows") or {}
    info["window_front_left"] = _window_pos_to_bool(
        windows.get("lfwinposnsts"), closed_val="1"
    )
    info["window_front_right"] = _window_pos_to_bool(
        windows.get("rfwinposnsts"), closed_val="1"
    )
    info["window_rear_left"] = _window_pos_to_bool(
        windows.get("lbwinposnsts"), closed_val="1"
    )
    info["window_rear_right"] = _window_pos_to_bool(
        windows.get("rbwinposnsts"), closed_val="1"
    )
    info["sunroof"] = _window_pos_to_bool(
        windows.get("skylightsts"), closed_val="3"
    )
    # 前挡风玻璃加热(区别于 frontFrost 前除霜)
    info["windshield_heat"] = _str_to_bool(windows.get("fwinheatsts"))

    # ===== 空调 / 除霜 / 自动模式(gtsp 自动模式改名 acAutoModeSts) =====
    info["air_conditioner"] = _str_to_bool(vs.get("airconditionsts"))
    info["ac_auto_mode"] = _str_to_bool(
        _first(vs.get("airconditionautomodenasts"), vs.get("acautomodests"))
    )
    info["front_defroster"] = _str_to_bool(vs.get("frontfrost"))
    info["rear_defroster"] = _str_to_bool(vs.get("backfrost"))

    # ===== 灯光(熄火时返回 "--" → None 未知;启动后 "0"=关,"1"=开) =====
    lighting = vs.get("lighting") or {}
    info["low_beam"] = _str_to_bool(lighting.get("nearbeamsts"))
    info["high_beam"] = _str_to_bool(lighting.get("farbeamsts"))

    # ===== 座椅 / 方向盘(gtsp 方向盘加热改名 steerWheelHeatDst) =====
    info["steer_wheel_heat"] = _str_to_bool(
        _first(vs.get("steerwheelheat"), vs.get("steerwheelheatdsts"))
    )
    seat = vs.get("seat") or {}
    info["seat_heat_driver"] = _str_to_bool(seat.get("maindriverseatheatsts"))
    info["seat_vent_driver"] = _str_to_bool(seat.get("maindriverseatventsts"))
    info["seat_heat_passenger"] = _str_to_bool(seat.get("viceseatheatsts"))
    info["seat_vent_passenger"] = _str_to_bool(seat.get("viceseatventsts"))
    info["seat_heat_rear_left"] = _str_to_bool(seat.get("lbseatheatsts"))
    info["seat_heat_rear_right"] = _str_to_bool(seat.get("rbseatheatsts"))

    # ===== 胎压(lf=前左, rf=前右, lb=后左, rb=后右) =====
    tire_press = vs.get("tirepress") or {}
    pos_map = [("fl", "lf"), ("fr", "rf"), ("rl", "lb"), ("rr", "rb")]
    for pos, prefix in pos_map:
        info[f"tire_pressure_{pos}"] = _parse_value_unit(
            tire_press.get(f"{prefix}tirepressval")
        )
        # gtsp 把报警字段改名为 *TirePressSts(旧名 *TirePressIndcrSts)
        info[f"tire_pressure_alarm_{pos}"] = _str_to_bool(
            _first(
                tire_press.get(f"{prefix}tirepressindcrsts"),
                tire_press.get(f"{prefix}tirepresssts"),
            )
        )

    # ===== 胎温 =====
    tire_temp = vs.get("tiretemp") or {}
    for pos, prefix in pos_map:
        info[f"tire_temp_{pos}"] = _parse_value_unit(
            tire_temp.get(f"{prefix}tiretempval")
        )

    # ===== 安全 =====
    info["antitheft"] = _str_to_bool(vs.get("vehicleantitheftstatus"))
    info["oil_alarm"] = _str_to_bool(vs.get("oilalarmsts"))

    return info
