"""GWM 中国区账号认证与远控客户端(短信登录 / token 自动刷新 / BeanTech 车控)。

协议来自开源项目 ha-gwm-ev(moryoav)对官方 APP 的逆向成果,
登录链路为三个独立服务的串联:
  1. G-App    (gapp-api.gwmapp-h.com)      手机号+短信验证码 → gToken/ssoToken/beanId
  2. BeanTech (gw-app-gateway.gwmapp-h.com) SSO 登录 → accessToken(车况查询+车控)
  3. AutoAI   (gapp-api.gwmapp-h.com 代理)  登录 → tokenId(车况查询头)

token 到期(约 7 天)后用 gRefreshToken 调 /v5/token/refresh 自动续期,
彻底摆脱"手动抓包 7 天一换"的旧方案。

v3.0 车况接口(gw-app-gateway)与 v2.0(apgdm)响应 key 大小写不一致,
统一在解析前把所有 key 递归转小写处理。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import quote, unquote

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

# ============================================================
# 常量(均来自 ha-gwm-ev 对官方 APP 的逆向)
# ============================================================
DEFAULT_NOTE_ID = "145765423214576567716671"
BEAN_TECH_APP_KEY = "7863128529"
AUTO_AI_CKEY = "ea49a50f914b8d38af1c84809d302683"
_SOURCE_APP_VERSION = "2.1.5"
_SOURCE_APP_CODE = "2150"
_OFFICIAL_USER_AGENT = "okhttp/4.2.2"
_G_APP_APP_ID = "GWM-APP-ANDROID-1100018"

_G_APP_BASE = "https://gapp-api.gwmapp-h.com/"
_BEAN_TECH_BASE = "https://gw-app-gateway.gwmapp-h.com/"
_SMS_REQUEST_URL = _G_APP_BASE + "api-guser/v5/user/login-sms/send"
_SMS_LOGIN_URL = _G_APP_BASE + "api-guser/v5/user/sms-login"
_REFRESH_URL = _G_APP_BASE + "api-guser/v5/token/refresh"
_DISCOVERY_URL = _G_APP_BASE + "gcar/v1/app/android/vehicle/query-vehicle-list"
_AUTO_AI_LOGIN_URL = _G_APP_BASE + "tsp/v1/proxy/navinfo/GW.M.APP_LOGIN"

_BEAN_TECH_LOGIN_PATH = "/app-api/api/v1.0/userAuth/loginSSOAccount"
_BEAN_TECH_LOGIN_URL = _BEAN_TECH_BASE.rstrip("/") + _BEAN_TECH_LOGIN_PATH
_BEAN_TECH_STATUS_PATH = "/app-api/api/v3.0/vehicle/getLastStatus"
_BEAN_TECH_STATUS_URL = _BEAN_TECH_BASE.rstrip("/") + _BEAN_TECH_STATUS_PATH
_BEAN_TECH_SEND_PATH = "/app-api/api/v1.0/vehicle/T5/sendCmd"
_BEAN_TECH_SEND_URL = _BEAN_TECH_BASE.rstrip("/") + _BEAN_TECH_SEND_PATH
# 鸣笛/闪灯类"即时"命令走 v3.0 remote-ctrl/timely(不带 cmdBody/isSaveConfig),
# 其余命令走 T5/sendCmd —— 均为 ha-gwm-ev 对官方 APP 抓包确认的行为
_BEAN_TECH_TIMELY_PATH = "/app-api/api/v3.0/vehicle/remote-ctrl/timely"
_BEAN_TECH_TIMELY_URL = _BEAN_TECH_BASE.rstrip("/") + _BEAN_TECH_TIMELY_PATH
# AutoAI 直连通道(navinfo/gtsp 平台远控走这里)
_AUTO_AI_DIRECT = "https://ti.gwm.com.cn:8443/tsp/ead"
_BEAN_TECH_RESULT_PATH = "/app-api/api/v1.0/vehicle/getRemoteCtrlResultT5"
_BEAN_TECH_RESULT_URL = _BEAN_TECH_BASE.rstrip("/") + _BEAN_TECH_RESULT_PATH

# 签名密钥(逆向自官方 APP)
_DEFAULT_SECRET_32 = "E3*138%pb=GcflmhmsaA4WU^J-f&0Ofe"
_DEFAULT_SECRET_36 = "t8X_MybKFjp-Kg^mt99ALe-ArGzJE5mpCOra"
_BEAN_TECH_SECRET = "21382b32fea1d5fa03813d806d2dd64f"
_AUTO_AI_PRIVATE_KEY = "dad377585f566b548c961a418dcec41a"
_G_APP_PASSWORDS = {
    1: "Qin.1^0123456789abcdef0123456789abcdef0123456789abcdef012345cdef",
    2: "Gwn*9$0123456789abcdef0123456789abcdef0189abcdef0123456789abcdef",
}
_G_APP_PREFIX = b"Salted__"
_AES_BLOCK_BYTES = algorithms.AES.block_size // 8


# 鸣笛/闪灯类"即时"命令(走 remote-ctrl/timely,不带 cmdBody/isSaveConfig)
_TIMELY_COMMANDS = frozenset({"WHISTLE", "FLASH", "WHISTLE_FLASH"})

# AutoAI 通用指令通道(GW.M.SEND_COMMON_COMMAND)的 cmdCode 映射。
# 适用于 navinfo / gtsp 平台(2026 款坦克300 = gtsp,实测 T5/sendCmd 返回 550002)。
_AUTO_AI_CMD_CODES = {
    "VEHICLE_UNLOCK": 1,
    "VEHICLE_LOCK": 2,
    "WINDOW_CLOSE": 3,
    "WHISTLE_FLASH": 5,
    "ENGINE_START": 15,
    "ENGINE_STOP": 16,
    "WHISTLE": 19,
    "FLASH": 20,
    "SKYLIGNT_CLOSE": 28,  # 官方 APP 拼写即如此
}
# 远程启动用单独的 function(官方 APP 行为)
_AUTO_AI_OPEN_COMMAND = "GW.M.SET_AND_OPEN_COMMAND"
_AUTO_AI_SEND_COMMAND = "GW.M.SEND_COMMON_COMMAND"


# ============================================================
# 异常
# ============================================================
class GWMCNAuthError(Exception):
    """认证失败(token 无效 / 验证码错误)。"""


class GWMCNConnectionError(Exception):
    """网络或 HTTP 层失败。"""


class GWMCNRiskControlError(Exception):
    """风控挑战(code 1013):需要在官方 APP 里完成验证后再试。"""


class GWMCNSchemaError(Exception):
    """响应结构异常。"""


# ============================================================
# 基础工具
# ============================================================
def sha256_hex(value: str) -> str:
    """UTF-8 文本的 SHA-256 小写十六进制。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _norm_keys(obj: Any) -> Any:
    """递归把所有 dict key 转小写(v2.0/v3.0 接口大小写不一致)。"""
    if isinstance(obj, dict):
        return {k.lower(): _norm_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm_keys(i) for i in obj]
    return obj


def _ci_prop(mapping: Dict[str, Any], name: str) -> Any:
    """大小写不敏感取属性(返回原始值)。"""
    if not isinstance(mapping, dict):
        return None
    folded = name.casefold()
    for key, child in mapping.items():
        if isinstance(key, str) and key.casefold() == folded:
            return child
    return None


# ============================================================
# .NET 风格紧凑 JSON(官方服务端对序列化格式敏感)
# ============================================================
def encode_dotnet_json(value: Any) -> str:
    """按 System.Text.Json 默认风格序列化(无空格,\\uXXXX 转义)。"""
    return _dotnet_encode(value, 0)


def _dotnet_encode(value: Any, depth: int) -> str:
    if depth > 32:
        raise ValueError("json_depth_invalid")
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _dotnet_encode_string(value)
    if isinstance(value, dict):
        parts = []
        for key, child in value.items():
            parts.append(_dotnet_encode_string(str(key)) + ":" + _dotnet_encode(child, depth + 1))
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_dotnet_encode(child, depth + 1) for child in value) + "]"
    raise ValueError("json_value_invalid")


_HTML_SENSITIVE = frozenset({'"', "&", "'", "+", "<", ">", "`"})
_SHORT_ESCAPES = {"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _dotnet_encode_string(value: str) -> str:
    encoded = ['"']
    for ch in value:
        short = _SHORT_ESCAPES.get(ch)
        if short is not None:
            encoded.append(short)
            continue
        code = ord(ch)
        if ch == "\\":
            encoded.append("\\\\")
        elif 0x20 <= code <= 0x7E and ch not in _HTML_SENSITIVE:
            encoded.append(ch)
        elif code <= 0xFFFF:
            encoded.append(f"\\u{code:04X}")
        else:
            scalar = code - 0x10000
            high = 0xD800 + (scalar >> 10)
            low = 0xDC00 + (scalar & 0x3FF)
            encoded.append(f"\\u{high:04X}\\u{low:04X}")
    encoded.append('"')
    return "".join(encoded)


# ============================================================
# G_A 信封加密(OpenSSL 兼容的 AES-CBC)
# ============================================================
def _derive_openssl_key(password: str, salt: bytes) -> tuple[bytes, bytes]:
    password_bytes = password.encode("utf-8")
    derived = b""
    previous = b""
    while len(derived) < 48:
        previous = hashlib.md5(previous + password_bytes + salt).digest()
        derived += previous
    return derived[:32], derived[32:48]


def encrypt_g_app(plaintext: str, key_id: int = 1) -> str:
    """把 UTF-8 文本包成 G-App 的 G_A(...) AES 信封。"""
    password = _G_APP_PASSWORDS[key_id]
    salt = secrets.token_bytes(8)
    key, iv = _derive_openssl_key(password, salt)
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    payload = base64.b64encode(_G_APP_PREFIX + salt + ciphertext).decode("ascii")
    return f"G_A({payload},{key_id})"


def decrypt_g_app(wrapped: str) -> str:
    """解开 G_A(...) 信封;普通文本原样返回。"""
    if not isinstance(wrapped, str) or not wrapped.startswith("G_A(") or not wrapped.endswith(")"):
        return wrapped
    separator = wrapped.rfind(",")
    if separator <= 4:
        raise GWMCNSchemaError("G_A 信封格式错误")
    key_id = int(wrapped[separator + 1 : -1])
    password = _G_APP_PASSWORDS[key_id]
    encrypted = base64.b64decode("".join(wrapped[4:separator].split()))
    if len(encrypted) < 32 or len(encrypted) % _AES_BLOCK_BYTES != 0 or encrypted[:8] != _G_APP_PREFIX:
        raise GWMCNSchemaError("G_A 载荷格式错误")
    salt = encrypted[8:16]
    ciphertext = encrypted[16:]
    key, iv = _derive_openssl_key(password, salt)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return plaintext.decode("utf-8")


# ============================================================
# 签名算法
# ============================================================
def _default_derived_secret(timestamp_text: str, device_id: str, authorization: str) -> str:
    """G-App Sign 的派生密钥(时间戳奇偶决定矩阵转置)。"""
    try:
        timestamp = int(timestamp_text.strip())
    except (AttributeError, ValueError):
        timestamp = 0
    index = timestamp % 100_000 % 32
    source = _DEFAULT_SECRET_36
    if timestamp & 1 == 1:
        # 奇数时间戳:6x6 矩阵转置
        source = "".join(
            _DEFAULT_SECRET_36[(row * 6) + column]
            for column in range(6)
            for row in range(6)
        )
    repeated = source + source
    secret_selection = repeated[index : index + 6]
    device_offset = timestamp % 8
    device_selection = (
        device_id[device_offset : device_offset + 6] if len(device_id) >= device_offset + 6 else ""
    )
    trimmed = authorization.strip()
    auth_selection = trimmed[3:9] if len(trimmed) > 9 else ""
    return _DEFAULT_SECRET_32 + secret_selection + device_selection + auth_selection


def default_sign(method: str, signing_url: str, raw_body: Optional[str], headers: Dict[str, str]) -> str:
    """G-App 服务的 SHA-256 签名(Sign 头)。"""
    timestamp = headers.get("Timestamp", "")
    authorization = headers.get("Authorization", "")
    device_id = headers.get("DeviceId", "")
    canonical = method.upper() + signing_url
    for name in (
        "AppId", "Authorization", "DeviceId", "NoteId",
        "SourceApp", "SourceAppVer", "SourceType", "Timestamp",
    ):
        canonical += f"{name.lower()}:{headers.get(name, '')}"
    if method.upper() != "GET":
        canonical += "json=" + (raw_body or "")
    canonical += _default_derived_secret(timestamp, device_id, authorization)
    return sha256_hex(canonical)


def _java_url_encode(value: str) -> str:
    """Java 风格 URL 编码(空格→+,保留 -_.* )。"""
    result = []
    for octet in value.encode("utf-8"):
        ch = chr(octet)
        if ch.isascii() and (ch.isalnum() or ch in "-_.*"):
            result.append(ch)
        elif ch == " ":
            result.append("+")
        else:
            result.append(f"%{octet:02X}")
    return "".join(result)


def bean_tech_sign(method: str, path: str, nonce: str, timestamp: str, parameter: str) -> str:
    """BeanTech 网关的 SHA-256 签名(bt-auth-sign 头)。"""
    decoded_path = "/" + "/".join(unquote(part) for part in path.split("/") if part)
    authorization = (
        f"bt-auth-appkey:{BEAN_TECH_APP_KEY}"
        f"bt-auth-nonce:{nonce}"
        f"bt-auth-timestamp:{timestamp}"
    )
    encoded = _java_url_encode(
        method.upper() + decoded_path + authorization + parameter + _BEAN_TECH_SECRET
    )
    for whitespace in ("+", "%20", "%0A", "%09", "%0D"):
        encoded = encoded.replace(whitespace, "")
    return sha256_hex(encoded)


def auto_ai_sign(timestamp: str) -> str:
    """AutoAI 的 HMAC-SHA1 签名(sign 头)。"""
    key = f"C_KEY={AUTO_AI_CKEY}&API_KEY={_AUTO_AI_PRIVATE_KEY}".encode()
    message = f"SIGN_BODY=[]&SIGN_TIME={timestamp}".encode()
    return base64.b64encode(hmac.digest(key, message, "sha1")).decode("ascii")


def _china_timestamp() -> str:
    """AutoAI 本地时间戳:yyyyMMddHHmmssfff(UTC+8)。"""
    local = time.gmtime(time.time() + 8 * 3600)
    return time.strftime("%Y%m%d%H%M%S", local) + f"{int(time.time() * 1000) % 1000:03d}"


def _random_nonce() -> str:
    return sha256_hex(secrets.token_bytes(16).hex().upper())[:16]


def _random_sequence() -> str:
    return secrets.token_hex(16) + str(secrets.randbelow(9000) + 1000)


# ============================================================
# 认证客户端
# ============================================================
class GWMChinaAuthClient:
    """GWM 中国区短信登录 + token 刷新 + BeanTech 车况/车控客户端。

    用法:
        client = GWMChinaAuthClient("13800138000")
        client.request_sms_code()               # 收到短信后
        vehicles = client.login_sms("123456")   # 完成三段式登录,返回车辆列表
        state = client.state_dict()             # 持久化(HA config entry)
        status = client.get_status(vin)         # 车况
    """

    def __init__(
        self,
        phone: str,
        state: Optional[Dict[str, Any]] = None,
        verify_ssl: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self.phone = (phone or "").strip()
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        state = state or {}
        self.device_id: str = state.get("device_id") or secrets.token_hex(16).upper()
        self.g_token: Optional[str] = state.get("g_token")
        self.g_refresh_token: Optional[str] = state.get("g_refresh_token")
        self.sso_token: Optional[str] = state.get("sso_token")
        self.pt_token: Optional[str] = state.get("pt_token")
        self.user_id: Optional[str] = state.get("user_id")
        self.bean_id: Optional[str] = state.get("bean_id")
        self.bt_access_token: Optional[str] = state.get("bt_access_token")
        self.bt_refresh_token: Optional[str] = state.get("bt_refresh_token")
        self.bt_bean_id: Optional[str] = state.get("bt_bean_id")
        self.auto_token_id: Optional[str] = state.get("auto_token_id")
        self.auto_user_id: Optional[str] = state.get("auto_user_id")
        # 车辆平台缓存: {vin: belongPlatform},按需从车辆列表拉取
        # 已知取值: "beantech"(T5/sendCmd) / "navinfo"(AutoAI) / "gtsp"(长城新 TSP,实测 2026 款坦克300)
        self._platform_cache: Dict[str, str] = {}
        for v in state.get("platforms") or []:
            if isinstance(v, dict) and v.get("vin"):
                self._platform_cache[str(v["vin"]).upper()] = str(v.get("platform") or "")
        # AutoAI/BeanTech 的 token 是短命会话(-101 缓存失效),过期需重新初始化;
        # 加锁防止并发请求同时触发 refresh 造成 refreshToken 单次轮换互相顶掉
        self._relogin_lock = threading.Lock()

    # ---------- 状态 ----------
    @property
    def has_g_app(self) -> bool:
        return all((self.g_token, self.g_refresh_token, self.user_id))

    @property
    def logged_in(self) -> bool:
        return all(
            (
                self.has_g_app,
                self.bean_id,
                self.bt_access_token,
                self.auto_token_id,
                self.auto_user_id,
            )
        )

    def state_dict(self) -> Dict[str, Any]:
        """导出可持久化状态(HA config entry data)。"""
        return {
            "device_id": self.device_id,
            "g_token": self.g_token,
            "g_refresh_token": self.g_refresh_token,
            "sso_token": self.sso_token,
            "pt_token": self.pt_token,
            "user_id": self.user_id,
            "bean_id": self.bean_id,
            "bt_access_token": self.bt_access_token,
            "bt_refresh_token": self.bt_refresh_token,
            "bt_bean_id": self.bt_bean_id,
            "auto_token_id": self.auto_token_id,
            "auto_user_id": self.auto_user_id,
            "platforms": [
                {"vin": vin, "platform": plat}
                for vin, plat in sorted(self._platform_cache.items())
            ],
        }

    # ============================================================
    # 平台路由(gtsp/navinfo → AutoAI 通道;beantech → T5/sendCmd)
    # ============================================================
    def get_platform(self, vin: str) -> str:
        """查询车辆平台;缓存未命中时拉一次车辆列表。"""
        vin = (vin or "").strip().upper()
        if vin not in self._platform_cache:
            for v in self.get_vehicles():
                self._platform_cache[v["vin"]] = (v.get("platform") or "").strip().lower()
        return self._platform_cache.get(vin, "")

    # ============================================================
    # 登录链路(对外)
    # ============================================================
    def request_sms_code(self) -> None:
        """请求短信验证码(flag=LOGIN)。"""
        self._g_app_post(
            "request_verification",
            _SMS_REQUEST_URL,
            {"phone": self.phone, "flag": "LOGIN"},
            encrypt_body=True,
        )

    def login_sms(self, code: str) -> list[Dict[str, Any]]:
        """用短信验证码完成三段式登录,返回车辆列表。"""
        code = (code or "").strip()
        if not code:
            raise GWMCNAuthError("验证码不能为空")
        data = self._g_app_post(
            "login",
            _SMS_LOGIN_URL,
            {"code": code, "phone": self.phone, "deviceToken": ""},
            encrypt_body=True,
        )
        self._apply_g_app_data(data)
        self._initialize_services()
        return self.get_vehicles()

    def refresh(self) -> None:
        """gToken 自动刷新 + 重新初始化两个下游服务。"""
        if not self.has_g_app:
            raise GWMCNAuthError("没有可刷新的登录状态,请重新短信登录")
        data = self._g_app_post(
            "refresh_token",
            _REFRESH_URL,
            {"token": self.g_token, "refreshToken": self.g_refresh_token},
            encrypt_body=True,
        )
        self._apply_g_app_data(data)
        self._initialize_services()

    def get_vehicles(self) -> list[Dict[str, Any]]:
        """发现账号下的车辆列表(query-vehicle-list),并缓存平台归属。"""
        data = self._g_app_post(
            "acquire_vehicles", _DISCOVERY_URL, {"vehicleVersion": 13}, encrypt_body=False
        )
        value = _ci_prop(data, "acquireVehiclesList") or data
        if not isinstance(value, list):
            raise GWMCNSchemaError("车辆列表格式异常")
        vehicles = []
        for item in value:
            if not isinstance(item, dict):
                continue
            vin = _ci_prop(item, "vin")
            if not vin:
                continue
            platform = str(_ci_prop(item, "belongPlatform") or "").strip()
            vehicles.append(
                {
                    "vin": str(vin).upper(),
                    "model_name": _ci_prop(item, "modelName"),
                    "series_name": _ci_prop(item, "appShowSeriesName"),
                    "nickname": _ci_prop(item, "vehicleNick"),
                    "brand_name": _ci_prop(item, "brandName"),
                    "platform": platform,
                }
            )
            self._platform_cache[str(vin).upper()] = platform.lower()
        return vehicles

    # ============================================================
    # 车况 / 车控(对外)
    # ============================================================
    def get_status(self, vin: str) -> Dict[str, Any]:
        """查询车况(v3.0 getLastStatus,BeanTech 签名)。返回 data 对象。"""
        vin = (vin or "").strip().upper()
        if not self.logged_in:
            raise GWMCNAuthError("未完成登录")
        timestamp = str(int(time.time() * 1000))
        nonce = _random_nonce()
        parameter = "vin=" + vin
        headers = self._bean_tech_headers(
            method="GET", path=_BEAN_TECH_STATUS_PATH, nonce=nonce,
            timestamp=timestamp, parameter=parameter, vin=vin,
        )
        resp = self.session.get(
            _BEAN_TECH_STATUS_URL + "?vin=" + vin,
            headers=headers, timeout=self.timeout, verify=self.verify_ssl,
        )
        data = self._decode_g_app_response(resp, "get_last_status")
        return _norm_keys(data) if isinstance(data, dict) else {}

    def send_command(
        self,
        vin: str,
        control_type: str,
        cmd_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        """发送远控命令,返回命令标识(seqNo 或 transactionId)。

        会话过期(-101 缓存失效)时自动重新登录并重试一次。
        """
        try:
            return self._send_command_inner(vin, control_type, cmd_body)
        except GWMCNAuthError as exc:
            msg = str(exc)
            if not ("会话" in msg or "-101" in msg or "认证失败" in msg):
                raise
            # 只对"会话失效"类错误重登重试,避免验证码错误等被误重试
            _LOGGER.info("远控时会话失效(%s),自动恢复后重试", exc)
            self.recover_session()
            return self._send_command_inner(vin, control_type, cmd_body)

    def recover_session(self) -> None:
        """会话失效时按代价从低到高恢复:重初始化短命会话 → refreshToken 刷新。

        全部失败抛 GWMCNAuthError,由上层触发 reauth 流程(需要重新短信验证)。
        """
        with self._relogin_lock:
            try:
                self._initialize_services()
                return
            except GWMCNAuthError as exc:
                _LOGGER.info("短命会话重初始化失败(%s),尝试 refreshToken", exc)
            except GWMCNSchemaError as exc:
                _LOGGER.info("短命会话重初始化失败(%s),尝试 refreshToken", exc)
            self.refresh()

    def _send_command_inner(
        self,
        vin: str,
        control_type: str,
        cmd_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        """按平台路由发送命令。"""
        vin = (vin or "").strip().upper()
        if not self.logged_in:
            raise GWMCNAuthError("未完成登录")

        platform = ""
        try:
            platform = self.get_platform(vin)
        except Exception:  # noqa: BLE001 - 平台探测失败时按未知处理,仍走 AutoAI
            _LOGGER.warning("查询车辆平台失败,远控按 AutoAI 通道尝试")

        if platform == "beantech":
            return self._send_bean_tech_command(vin, control_type, cmd_body)

        cmd_code = _AUTO_AI_CMD_CODES.get(control_type)
        if cmd_code is None:
            raise GWMCNAuthError(
                f"平台 {platform or '未知'} 暂不支持 {control_type}:"
                "gtsp 平台目前仅支持 锁车/解锁/关全车窗/鸣笛/闪灯/鸣笛闪灯/远程启动/熄火/关天窗"
            )
        function = (
            _AUTO_AI_OPEN_COMMAND if control_type == "ENGINE_START" else _AUTO_AI_SEND_COMMAND
        )
        return self._send_auto_ai_command(vin, cmd_code, function)

    def _send_bean_tech_command(
        self,
        vin: str,
        control_type: str,
        cmd_body: Optional[Dict[str, Any]],
    ) -> str:
        """BeanTech 平台:T5/sendCmd(controlType 字符串)+ timely(鸣笛闪灯)。"""
        seq_no = _random_sequence()
        timely = control_type in _TIMELY_COMMANDS
        if timely:
            command_entry: Dict[str, Any] = {"controlType": control_type}
        else:
            command_entry = {"controlType": control_type, "cmdBody": cmd_body}
        payload: Dict[str, Any] = {
            "vin": vin,
            "seqNo": seq_no,
            "sendType": 0,
            "commands": [command_entry],
        }
        if not timely:
            payload["isSaveConfig"] = None
        body = encode_dotnet_json(payload)
        url = _BEAN_TECH_TIMELY_URL if timely else _BEAN_TECH_SEND_URL
        path = _BEAN_TECH_TIMELY_PATH if timely else _BEAN_TECH_SEND_PATH
        headers = self._bean_tech_headers(
            method="POST", path=path, nonce=_random_nonce(),
            timestamp=str(int(time.time() * 1000)),
            parameter="json=" + body, vin=vin,
        )
        headers["Content-Type"] = "application/json; charset=UTF-8"
        resp = self.session.post(
            url, headers=headers, data=body.encode("utf-8"),
            timeout=self.timeout, verify=self.verify_ssl,
        )
        self._decode_g_app_response(resp, "send_cmd")
        return seq_no

    def _send_auto_ai_command(self, vin: str, cmd_code: int, function: str) -> str:
        """AutoAI 通用指令通道(navinfo/gtsp),返回 transactionId。"""
        ts_ms = str(int(time.time() * 1000))
        body = {
            "flag": 1,
            "signStr": hashlib.md5(
                (vin + (self.auto_token_id or "")).encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest(),
            "userId": self.auto_user_id,
            "userType": "0",
            "vin": vin,
            "cmdCode": cmd_code,
        }
        wrapper = {
            "body": body,
            "header": {
                "brandType": "gwm",
                "cVer": _SOURCE_APP_VERSION,
                "fn": function,
                "fv": "0202",
                "mobileId": self.device_id,
                "osType": "Android",
                "osVer": "",
                "rs": "2",
                "ts": _china_timestamp(),
                "tk": self.auto_token_id,
                "v": "1.0",
            },
        }
        payload = encode_dotnet_json(wrapper)
        url = _AUTO_AI_DIRECT + "?p=" + quote(payload, safe="")
        headers = {
            "v": "1.0",
            "cid": self.device_id,
            "client": "phone",
            "sign": auto_ai_sign(ts_ms),
            "time": ts_ms,
            "ckey": AUTO_AI_CKEY,
            "protocolVer": "2.1.2",
            "token": self.auto_token_id,
            "brandType": "GWM",
            "Accept-Encoding": "gzip",
            "User-Agent": _OFFICIAL_USER_AGENT,
        }
        resp = self.session.get(
            url, headers=headers, timeout=self.timeout, verify=self.verify_ssl,
        )
        result = self._decode_auto_ai_response(resp, "send_cmd")
        transaction_id = _ci_prop(result, "transactionId")
        if not transaction_id:
            raise GWMCNSchemaError("AutoAI 远控响应缺少 transactionId")
        return str(transaction_id)

    def get_command_result(self, vin: str, seq_no: str) -> Any:
        """查询远控命令执行结果(getRemoteCtrlResultT5)。"""
        vin = (vin or "").strip().upper()
        if not self.logged_in:
            raise GWMCNAuthError("未完成登录")
        headers = self._bean_tech_headers(
            method="GET", path=_BEAN_TECH_RESULT_PATH, nonce=_random_nonce(),
            timestamp=str(int(time.time() * 1000)),
            parameter="seqno=" + seq_no, vin=vin,
        )
        resp = self.session.get(
            _BEAN_TECH_RESULT_URL + "?seqNo=" + quote(seq_no, safe=""),
            headers=headers, timeout=self.timeout, verify=self.verify_ssl,
        )
        return self._decode_g_app_response(resp, "get_cmd_result")

    # ============================================================
    # 内部:登录链路各段
    # ============================================================
    def _apply_g_app_data(self, data: Dict[str, Any]) -> None:
        """从 G-App 登录/刷新响应提取会话字段。"""
        if not isinstance(data, dict):
            raise GWMCNSchemaError("G-App 响应格式异常")
        g_token = _ci_prop(data, "gToken") or _ci_prop(data, "token")
        g_refresh = _ci_prop(data, "gRefreshToken") or _ci_prop(data, "refreshToken")
        sso_token = _ci_prop(data, "ssoToken")
        pt_token = _ci_prop(data, "ptToken")
        user_id = _ci_prop(data, "userId")
        bean_id = _ci_prop(data, "beanId")
        if g_token and g_refresh and user_id:
            self.g_token = str(g_token)
            self.g_refresh_token = str(g_refresh)
            self.user_id = str(user_id)
        if sso_token:
            self.sso_token = str(sso_token)
        if pt_token:
            self.pt_token = str(pt_token)
        if bean_id:
            self.bean_id = str(bean_id)
        if not self.has_g_app:
            raise GWMCNSchemaError("G-App 登录响应缺少必需字段(gToken/gRefreshToken/userId)")

    def _initialize_services(self) -> None:
        """BeanTech + AutoAI 两个下游服务登录(顺序执行)。"""
        # ---- BeanTech SSO ----
        if self.sso_token is None and self.pt_token is None:
            raise GWMCNSchemaError("缺少 ssoToken,无法登录 BeanTech")
        body = encode_dotnet_json(
            {
                "appType": 0,
                "deviceId": self.device_id,
                "phone": self.phone,
                "ssoId": self.user_id,
                "ssoToken": self.sso_token or self.pt_token,
            }
        )
        timestamp = str(int(time.time() * 1000))
        nonce = _random_nonce()
        headers = self._bean_tech_base_headers(nonce, timestamp)
        headers["bt-auth-sign"] = bean_tech_sign(
            "POST", _BEAN_TECH_LOGIN_PATH, nonce, timestamp, "json=" + body
        )
        if self.bean_id:
            headers["beanId"] = self.bean_id
        headers["Content-Type"] = "application/json; charset=UTF-8"
        resp = self.session.post(
            _BEAN_TECH_LOGIN_URL, headers=headers, data=body.encode("utf-8"),
            timeout=self.timeout, verify=self.verify_ssl,
        )
        bt_data = self._decode_g_app_response(resp, "initialize_bean_tech")
        if not isinstance(bt_data, dict):
            raise GWMCNSchemaError("BeanTech 登录响应格式异常")
        access_token = _ci_prop(bt_data, "accessToken")
        if not access_token:
            raise GWMCNSchemaError("BeanTech 登录响应缺少 accessToken")
        self.bt_access_token = str(access_token)
        bt_refresh = _ci_prop(bt_data, "refreshToken")
        if bt_refresh:
            self.bt_refresh_token = str(bt_refresh)
        bt_bean_id = _ci_prop(bt_data, "beanId")
        self.bt_bean_id = str(bt_bean_id) if bt_bean_id else self.bean_id

        # ---- AutoAI ----
        if self.sso_token is None:
            raise GWMCNSchemaError("缺少 ssoToken,无法登录 AutoAI")
        ts_ms = str(int(time.time() * 1000))
        wrapper = {
            "body": {
                "appType": 0,
                "phone": self.phone,
                "pushId": "0",
                "pushKey": "0",
                "ssoid": self.user_id,
                "ssoTk": self.sso_token,
            },
            "header": {
                "brandType": "gwm",
                "cVer": _SOURCE_APP_VERSION,
                "fn": "GW.M.APP_LOGIN",
                "fv": "0202",
                "mobileId": self.device_id,
                "osType": "Android",
                "osVer": "",
                "rs": "2",
                "ts": _china_timestamp(),
                "tk": "",
                "v": "1.0",
            },
        }
        payload = encode_dotnet_json(wrapper)
        url = _AUTO_AI_LOGIN_URL + "?p=" + quote(payload, safe="")
        headers = {
            "v": "1.0",
            "cid": self.device_id,
            "client": "phone",
            "sign": auto_ai_sign(ts_ms),
            "time": ts_ms,
            "ckey": AUTO_AI_CKEY,
            "protocolVer": "2.1.2",
            "brandType": "GWM",
            "Accept-Encoding": "gzip",
            "User-Agent": _OFFICIAL_USER_AGENT,
        }
        resp = self.session.get(url, headers=headers, timeout=self.timeout, verify=self.verify_ssl)
        auto_data = self._decode_auto_ai_response(resp, "initialize_auto_ai")
        token_id = _ci_prop(auto_data, "tokenId")
        auto_user_id = _ci_prop(auto_data, "userId")
        if not token_id or not auto_user_id:
            raise GWMCNSchemaError("AutoAI 登录响应缺少 tokenId/userId")
        self.auto_token_id = str(token_id)
        self.auto_user_id = str(auto_user_id)

    # ============================================================
    # 内部:请求头构造
    # ============================================================
    def _g_app_post(
        self,
        operation: str,
        url: str,
        logical_body: Dict[str, Any],
        encrypt_body: bool,
    ) -> Any:
        """G-App 服务 POST(Sign 头 + 可选 G_A 加密 body)。"""
        logical_json = encode_dotnet_json(logical_body)
        raw_body = encrypt_g_app(logical_json) if encrypt_body else logical_json
        timestamp = str(int(time.time() * 1000) // 1000 * 1000)
        signing_headers = {
            "Authorization": self.bt_access_token or "",
            "SourceApp": "GWM",
            "SourceType": "ANDROID",
            "SourceAppVer": _SOURCE_APP_VERSION,
            "Timestamp": timestamp,
            "DeviceId": self.device_id,
            "AppId": _G_APP_APP_ID,
            "NoteId": DEFAULT_NOTE_ID,
        }
        headers: Dict[str, str] = {}
        if self.g_token:
            headers["G-TOKEN"] = self.g_token
        headers["Authorization"] = self.bt_access_token or ""
        if self.user_id:
            headers["ssoId"] = self.user_id
        headers.update(
            {
                "SourceApp": "GWM",
                "SourceType": "ANDROID",
                "SourceAppVer": _SOURCE_APP_VERSION,
                "SourceAppCode": _SOURCE_APP_CODE,
                "Timestamp": timestamp,
                "DeviceId": self.device_id,
                "AppId": _G_APP_APP_ID,
            }
        )
        if self.bean_id:
            headers["beanId"] = self.bean_id
        headers.update(
            {
                "NoteId": DEFAULT_NOTE_ID,
                "Sign": default_sign("POST", url, raw_body, signing_headers),
                "Accept-Encoding": "gzip",
                "User-Agent": _OFFICIAL_USER_AGENT,
                "Content-Type": "application/json; charset=UTF-8",
            }
        )
        resp = self.session.post(
            url, headers=headers, data=raw_body.encode("utf-8"),
            timeout=self.timeout, verify=self.verify_ssl,
        )
        return self._decode_g_app_response(resp, operation)

    def _bean_tech_base_headers(self, nonce: str, timestamp: str) -> Dict[str, str]:
        """BeanTech 公共头(不含签名)。"""
        headers = {
            "bt-auth-appkey": BEAN_TECH_APP_KEY,
            "bt-auth-nonce": nonce,
            "bt-auth-timestamp": timestamp,
            "rs": "2",
            "appId": "097a7099af30d960",
            "brand": "10",
            "terminal": "GW_APP_GWM",
            "enterPriseId": "CC01",
            "cVer": _SOURCE_APP_VERSION,
            "tenantId": "1",
            "operatorRole": "0",
            "Accept-Encoding": "gzip",
            "User-Agent": _OFFICIAL_USER_AGENT,
        }
        return headers

    def _bean_tech_headers(
        self,
        *,
        method: str,
        path: str,
        nonce: str,
        timestamp: str,
        parameter: str,
        vin: str,
    ) -> Dict[str, str]:
        """BeanTech 已认证请求头(带 accessToken + 签名)。"""
        bean_id = self.bt_bean_id or self.bean_id
        if not self.bt_access_token or not bean_id or not self.auto_token_id:
            raise GWMCNAuthError("BeanTech 会话不完整,请重新登录")
        headers = self._bean_tech_base_headers(nonce, timestamp)
        headers.update(
            {
                "bt-auth-sign": bean_tech_sign(method, path, nonce, timestamp, parameter),
                "accessToken": self.bt_access_token,
                "beanId": bean_id,
                "vin": vin,
                "tokenId": self.auto_token_id,
            }
        )
        return headers

    # ============================================================
    # 内部:响应解包
    # ============================================================
    def _decode_g_app_response(self, resp: requests.Response, operation: str) -> Any:
        """G-App/BeanTech 风格响应:{code, data},data 可能是 G_A 信封。"""
        self._check_http(resp, operation)
        try:
            root = resp.json()
        except ValueError as exc:
            raise GWMCNSchemaError(f"{operation}: 响应不是 JSON") from exc
        code = str(_ci_prop(root, "code") or "")
        if code and code not in {"0", "000000", "200"}:
            if code == "1013":
                raise GWMCNRiskControlError("触发风控(1013),请在官方 APP 完成验证后再试")
            raise GWMCNAuthError(f"{operation}: API 返回错误码 {code}")
        data = _ci_prop(root, "data")
        if data is None:
            data = root
        if isinstance(data, str) and data.startswith("G_A("):
            try:
                data = json.loads(decrypt_g_app(data))
            except (ValueError, json.JSONDecodeError) as exc:
                raise GWMCNSchemaError(f"{operation}: G_A 信封解密失败") from exc
        return data

    def _decode_auto_ai_response(self, resp: requests.Response, operation: str) -> Any:
        """AutoAI 风格响应:{header:{c}, body};无 header 时回退 G-App 风格。"""
        self._check_http(resp, operation)
        try:
            root = resp.json()
        except ValueError as exc:
            raise GWMCNSchemaError(f"{operation}: 响应不是 JSON") from exc
        if not isinstance(root, dict) or _ci_prop(root, "header") is None:
            # 无 header → 按 G-App 信封解
            return self._decode_g_app_response(resp, operation)
        header = _ci_prop(root, "header")
        code = str(_ci_prop(header, "c") or "")
        if code and code != "0":
            if code == "1013":
                raise GWMCNRiskControlError("触发风控(1013),请在官方 APP 完成验证后再试")
            raise GWMCNAuthError(f"{operation}: AutoAI 返回错误码 {code}")
        body = _ci_prop(root, "body")
        return root if body is None else body

    @staticmethod
    def _check_http(resp: requests.Response, operation: str) -> None:
        if resp.status_code in (401, 403):
            raise GWMCNAuthError(f"{operation}: 认证失败(HTTP {resp.status_code})")
        if not 200 <= resp.status_code <= 299:
            raise GWMCNConnectionError(f"{operation}: HTTP {resp.status_code}")
