"""Клиент к веб-интерфейсу TP-Link WR740N (прошивка 3.13.2, аппарат v4).

Прошивка отдаёт данные в виде JS-массивов внутри .htm-страниц, например:

    var statusPara = new Array( 1, 1, 22, ... "WR740N v4 ...", 0,0 );

Здесь мы:
  * ходим на /userRpm/*.htm с HTTP Basic-авторизацией (admin:admin по умолчанию);
  * подставляем заголовки Referer/Host под адрес роутера (прошивка их проверяет);
  * парсим JS-массивы в Python-структуры;
  * формируем GET-запросы на сохранение настроек ровно так же, как это делает
    штатная веб-морда (read-modify-write: читаем все текущие поля формы и
    переопределяем только нужные — чтобы случайно ничего не сбросить).

Все операции роутера выполняются методом GET — это особенность прошивки.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import settings

log = logging.getLogger("routerdebugger.router")


class RouterError(Exception):
    """Ошибка обращения к роутеру (недоступен, неверная авторизация и т.п.)."""


# ---------------------------------------------------------------------------
# Парсер JS-массивов
# ---------------------------------------------------------------------------
def _read_array_body(text: str, name: str) -> str | None:
    """Возвращает сырое содержимое `new Array( ... )` для переменной `name`.

    Корректно учитывает вложенные скобки и строки в кавычках.
    """
    m = re.search(r"var\s+" + re.escape(name) + r"\s*=\s*new\s+Array\s*\(", text)
    if not m:
        return None
    i = m.end()
    depth = 1
    in_str = False
    esc = False
    out: list[str] = []
    while i < len(text) and depth > 0:
        c = text[i]
        if in_str:
            if esc:
                out.append(c)
                esc = False
            elif c == "\\":
                out.append(c)
                esc = True
            elif c == '"':
                out.append(c)
                in_str = False
            else:
                out.append(c)
        else:
            if c == '"':
                out.append(c)
                in_str = True
            elif c == "(":
                depth += 1
                out.append(c)
            elif c == ")":
                depth -= 1
                if depth > 0:
                    out.append(c)
            else:
                out.append(c)
        i += 1
    return "".join(out)


def _coerce(tok: str) -> Any:
    tok = tok.strip()
    if not tok:
        return ""
    if tok.startswith('"') and tok.endswith('"'):
        s = tok[1:-1]
        s = s.replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")
        return s
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_js_array(text: str, name: str) -> list[Any] | None:
    """Парсит `var name = new Array(...)` в список Python-значений.

    Хвостовые элементы-«заглушки» (обычно 0,0) остаются в списке — потребители
    читают данные по известному количеству/шагу и лишние элементы игнорируют.
    """
    body = _read_array_body(text, name)
    if body is None:
        return None
    tokens: list[str] = []
    cur: list[str] = []
    in_str = False
    esc = False
    depth = 0
    for c in body:
        if in_str:
            cur.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                cur.append(c)
                in_str = True
            elif c == "(":
                depth += 1
                cur.append(c)
            elif c == ")":
                depth -= 1
                cur.append(c)
            elif c == "," and depth == 0:
                tokens.append("".join(cur))
                cur = []
            else:
                cur.append(c)
    if "".join(cur).strip():
        tokens.append("".join(cur))
    return [_coerce(t) for t in tokens]


def _s(v: Any) -> str:
    return "" if v is None else str(v)


# Текстовые карты (повторяют массивы из прошивки) --------------------------
WAN_TYPE = {
    1: "Динамический IP", 2: "Статический IP", 3: "PPPoE",
    4: "802.1X + Dynamic IP", 5: "802.1X + Static IP", 6: "BigPond Cable",
    7: "L2TP", 8: "PPTP",
}
WLAN_MODE = {1: "11b only", 2: "11g only", 3: "11n only", 4: "11bg mixed", 5: "11bgn mixed"}
STA_STATUS = [
    "STA-AUTH", "STA-ASSOC", "WPA", "WPA-Personal", "WPA2", "WPA2-Personal",
    "802.1X", "STA-JOINED", "AP-UP", "AP-DOWN", "Отключён",
]
PROTO = {1: "ALL", 2: "TCP", 3: "UDP"}


def normalize_port_expression(value: Any, *, allow_list: bool = False,
                              allow_range: bool = True) -> str:
    """Проверяет и нормализует порт, диапазон или список диапазонов.

    Роутер принимает диапазоны как ``1000-1010``, а Port Triggering также
    допускает список через запятую.  Проверяем это до отправки запроса, чтобы
    не полагаться на неочевидные ошибки старой прошивки.
    """
    expression = str(value).strip().replace(" ", "").replace("–", "-").replace("—", "-")
    if not expression:
        raise ValueError("порт не указан")

    parts = expression.split(",")
    if not allow_list and len(parts) != 1:
        raise ValueError("допустим только один порт или один диапазон")

    normalized: list[str] = []
    for part in parts:
        match = re.fullmatch(r"(\d{1,5})(?:-(\d{1,5}))?", part)
        if not match:
            raise ValueError(f"некорректный порт или диапазон: {part or expression}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if not 1 <= start <= 65535 or not 1 <= end <= 65535:
            raise ValueError("порт должен быть от 1 до 65535")
        if end < start:
            raise ValueError("конец диапазона не может быть меньше начала")
        if end != start and not allow_range:
            raise ValueError("для этого поля допустим только один порт")
        normalized.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(normalized)
SEC_TYPE = {0: "Без защиты", 1: "WEP", 2: "WPA/WPA2-Enterprise", 3: "WPA/WPA2-Personal"}


# ---------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------
class RouterClient:
    def __init__(self) -> None:
        host = urlsplit(settings.router_url).netloc
        self._client = httpx.AsyncClient(
            base_url=settings.router_url,
            auth=(settings.router_username, settings.router_password),
            timeout=settings.router_timeout,
            headers={
                "Referer": f"{settings.router_url}/userRpm/MenuRpm.htm",
                "Host": host,
                "User-Agent": "Mozilla/5.0 (compatible; RouterDebugger/1.0)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> str:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise RouterError(f"Роутер недоступен: {e}") from e
        if resp.status_code == 401:
            raise RouterError("Роутер отверг авторизацию (проверь ROUTER_USERNAME/ROUTER_PASSWORD).")
        if resp.status_code >= 400:
            raise RouterError(f"Роутер вернул HTTP {resp.status_code}")
        return resp.text

    # --- Чтение -----------------------------------------------------------
    async def get_status(self) -> dict[str, Any]:
        t = await self._get("/userRpm/StatusRpm.htm")
        st = parse_js_array(t, "statusPara") or []
        lan = parse_js_array(t, "lanPara") or []
        wl = parse_js_array(t, "wlanPara") or []
        stat = parse_js_array(t, "statistList") or []
        wan = parse_js_array(t, "wanPara") or []

        def g(arr: list, i: int, d: Any = "") -> Any:
            return arr[i] if 0 <= i < len(arr) else d

        wan_type = g(wan, 3, 0)
        uptime = int(g(st, 4, 0) or 0)
        return {
            "firmware": _s(g(st, 5)).strip(),
            "hardware": _s(g(st, 6)).strip(),
            "uptime_seconds": uptime,
            "uptime_human": _fmt_uptime(uptime),
            "lan": {"mac": g(lan, 0), "ip": g(lan, 1), "mask": g(lan, 2)},
            "wlan": {
                "enabled": bool(g(wl, 0, 0)),
                "ssid": g(wl, 1),
                "channel": ("Авто" if g(wl, 2) == 15 else g(wl, 2)),
                "current_channel": g(wl, 9),
                "mode": WLAN_MODE.get(g(wl, 3), "—"),
                "mac": g(wl, 4),
            },
            "wan": {
                "type": WAN_TYPE.get(wan_type, "—"),
                "ip": g(wan, 2),
                "mask": g(wan, 4),
                "gateway": g(wan, 7),
                "dns": g(wan, 11),
                "online_time": g(wan, 12),
                "connected": _s(g(wan, 2)) not in ("", "0.0.0.0"),
            },
            "traffic": {
                "bytes_recv": int(g(stat, 0, 0) or 0),
                "bytes_sent": int(g(stat, 1, 0) or 0),
                "pkts_recv": int(g(stat, 2, 0) or 0),
                "pkts_sent": int(g(stat, 3, 0) or 0),
            },
        }

    async def get_dhcp_clients(self) -> list[dict[str, Any]]:
        t = await self._get("/userRpm/AssignedIpAddrListRpm.htm")
        para = parse_js_array(t, "DHCPDynPara") or [0, 4]
        lst = parse_js_array(t, "DHCPDynList") or []
        count = int(para[0]) if para else 0
        stride = int(para[1]) if len(para) > 1 else 4
        out = []
        for i in range(count):
            r = i * stride
            if r + 3 >= len(lst):
                break
            out.append({
                "name": lst[r], "mac": lst[r + 1],
                "ip": lst[r + 2], "lease": lst[r + 3],
            })
        return out

    async def get_wlan_stations(self) -> list[dict[str, Any]]:
        t = await self._get("/userRpm/WlanStationRpm.htm")
        hp = parse_js_array(t, "wlanHostPara") or []
        hl = parse_js_array(t, "hostList") or []
        count = int(hp[0]) if hp else 0
        stride = int(hp[4]) if len(hp) > 4 else 4
        out = []
        for i in range(min(count, len(hl) // stride if stride else 0)):
            r = i * stride
            si = hl[r + 1] if r + 1 < len(hl) else 0
            out.append({
                "mac": hl[r],
                "status": STA_STATUS[si] if isinstance(si, int) and 0 <= si < len(STA_STATUS) else _s(si),
                "rx": hl[r + 2] if r + 2 < len(hl) else 0,
                "tx": hl[r + 3] if r + 3 < len(hl) else 0,
            })
        return out

    async def get_virtual_servers(self, page: int = 1) -> dict[str, Any]:
        page = max(1, int(page))
        params = {"Page": page} if page > 1 else None
        t = await self._get("/userRpm/VirtualServerRpm.htm", params)
        para = parse_js_array(t, "virServerPara") or []
        lst = parse_js_array(t, "virServerListPara") or []
        count = int(para[2]) if len(para) > 2 else 0
        stride = int(para[3]) if len(para) > 3 else 7
        current_page = int(para[0]) if para else page
        has_more = bool(para[1]) if len(para) > 1 else False
        per_page = int(para[4]) if len(para) > 4 else 8
        out = []
        for i in range(count):
            r = i * stride
            if r + 6 >= len(lst):
                break
            ext_a, ext_b = lst[r], lst[r + 1]
            int_a, int_b = lst[r + 2], lst[r + 3]
            out.append({
                "id": (current_page - 1) * per_page + i,
                "service_port": _s(ext_a) if ext_a == ext_b else f"{ext_a}-{ext_b}",
                "internal_port": _s(int_a) if int_a == int_b else f"{int_a}-{int_b}",
                "ext_port": ext_a,
                "int_port": int_a,
                "ip": lst[r + 4],
                "protocol": PROTO.get(lst[r + 5], _s(lst[r + 5])),
                "protocol_code": lst[r + 5],
                "enabled": lst[r + 6] == 1,
            })
        return {
            "items": out,
            "page": current_page,
            "has_more": has_more,
            "per_page": per_page,
        }

    async def get_dhcp_settings(self) -> dict[str, Any]:
        t = await self._get("/userRpm/LanDhcpServerRpm.htm")
        p = parse_js_array(t, "DHCPPara") or []

        def g(i: int, d: Any = "") -> Any:
            return p[i] if 0 <= i < len(p) else d

        return {
            "enabled": g(0, 0) == 1,
            "start_ip": g(1), "end_ip": g(2), "lease": g(3),
            "gateway": g(4), "domain": g(5),
            "dns1": g(6), "dns2": g(7),
        }

    async def get_wlan_network(self) -> dict[str, Any]:
        """Текущие настройки беспроводной сети + сырой wlanPara (для read-modify-write)."""
        t = await self._get("/userRpm/WlanNetworkRpm.htm")
        wl = parse_js_array(t, "wlanPara") or []

        def g(i: int, d: Any = "") -> Any:
            return wl[i] if 0 <= i < len(wl) else d

        return {
            "_raw": wl,
            "ssid": g(3),
            "region": g(5),
            "mode": g(7),
            "mode_name": WLAN_MODE.get(g(7), "—"),
            "radio_on": bool(g(8, 0)),
            "ssid_broadcast": bool(g(9, 0)),
            "channel": g(10),
            "channel_label": ("Авто" if g(10) == 15 else g(10)),
            "chan_width": g(11),
            "rate": g(12),
            "wds": bool(g(22, 0)),
            "brlssid": g(23),
            "brlbssid": g(24),
            "keytype": g(25),
            "keytext": g(26),
            "wepindex": g(27),
            "authtype": g(32),
        }

    async def get_wlan_security(self) -> dict[str, Any]:
        t = await self._get("/userRpm/WlanSecurityRpm.htm")
        wl = parse_js_array(t, "wlanPara") or []
        wlist = parse_js_array(t, "wlanList") or []

        def g(i: int, d: Any = "") -> Any:
            return wl[i] if 0 <= i < len(wl) else d

        sec_opt = _s(g(3, "333"))
        return {
            "_raw": wl,
            "_raw_list": wlist,
            "sec_type": g(2),
            "sec_type_name": SEC_TYPE.get(g(2), "—"),
            "psk_password": g(9),       # текущий пароль Wi-Fi (WPA-PSK)
            "psk_sec_opt": sec_opt[2] if len(sec_opt) > 2 else "3",
            "psk_cipher": g(14),
            "wpa_cipher": g(13),
            "interval": g(11),
        }

    # --- Запись (действия) ------------------------------------------------
    async def reboot(self) -> None:
        await self._get("/userRpm/SysRebootRpm.htm", {"Reboot": "Reboot"})

    async def vs_add(self, ext_port: str | int, int_port: str | int, ip: str,
                     protocol: int = 1, state: int = 1, page: int = 1) -> None:
        ext_port = normalize_port_expression(ext_port)
        int_port = normalize_port_expression(int_port)
        await self._get("/userRpm/VirtualServerRpm.htm", {
            "ExPort": ext_port, "InPort": int_port, "Ip": ip,
            "Protocol": protocol, "State": state,
            "Commonport": 0, "Changed": 0, "SelIndex": 0,
            "Page": max(1, int(page)), "Save": "Save",
        })

    async def vs_delete(self, entry_id: int, page: int = 1) -> None:
        await self._get("/userRpm/VirtualServerRpm.htm", {
            "Del": entry_id, "Page": max(1, int(page)),
        })

    async def vs_do_all(self, action: str, page: int = 1) -> None:
        # action: EnAll | DisAll | DelAll
        if action not in ("EnAll", "DisAll", "DelAll"):
            raise RouterError("Недопустимое действие для проброса портов")
        await self._get("/userRpm/VirtualServerRpm.htm", {
            "doAll": action, "Page": max(1, int(page)),
        })

    async def dhcp_save(self, enabled: bool, start_ip: str, end_ip: str, lease: str,
                        gateway: str = "", domain: str = "",
                        dns1: str = "", dns2: str = "") -> None:
        await self._get("/userRpm/LanDhcpServerRpm.htm", {
            "dhcpserver": 1 if enabled else 0,
            "ip1": start_ip, "ip2": end_ip, "Lease": lease,
            "gateway": gateway, "domain": domain,
            "dnsserver": dns1, "dnsserver2": dns2,
            "Save": "Save",
        })

    async def wlan_save(self, *, ssid: str | None = None, radio_on: bool | None = None,
                        ssid_broadcast: bool | None = None,
                        channel: int | None = None, mode: int | None = None) -> None:
        """Меняет настройки Wi-Fi сети (read-modify-write: остальные поля сохраняются)."""
        cur = await self.get_wlan_network()
        ssid = cur["ssid"] if ssid is None else ssid
        radio_on = cur["radio_on"] if radio_on is None else radio_on
        ssid_broadcast = cur["ssid_broadcast"] if ssid_broadcast is None else ssid_broadcast
        channel = cur["channel"] if channel is None else channel
        mode = cur["mode"] if mode is None else mode

        params: dict[str, Any] = {
            "ssid1": ssid,
            "region": cur["region"],
            "channel": channel,
            "mode": mode,
            "chanWidth": cur["chan_width"],
            "rate": cur["rate"],
            "brlssid": cur["brlssid"],
            "brlbssid": cur["brlbssid"],
            "keytype": cur["keytype"],
            "wepindex": cur["wepindex"],
            "authtype": cur["authtype"],
            "keytext": cur["keytext"],
            "Save": "Save",
        }
        # чекбоксы отправляются только если включены (как в браузере)
        if radio_on:
            params["ap"] = 1
        if ssid_broadcast:
            params["broadcast"] = 2
        if cur["wds"]:
            params["wdsbrl"] = 2
        await self._get("/userRpm/WlanNetworkRpm.htm", params)

    async def wlan_set_password(self, new_password: str) -> None:
        """Меняет пароль Wi-Fi (режим WPA/WPA2-Personal), сохраняя остальные параметры."""
        if not (8 <= len(new_password) <= 63):
            raise RouterError("Пароль Wi-Fi должен быть длиной 8–63 символа.")
        cur = await self.get_wlan_security()
        wl = cur["_raw"]
        wlist = cur["_raw_list"]

        def g(i: int, d: Any = "") -> Any:
            return wl[i] if 0 <= i < len(wl) else d

        def gl(i: int, d: Any = "") -> Any:
            return wlist[i] if 0 <= i < len(wlist) else d

        sec_opt = _s(g(3, "333"))
        params: dict[str, Any] = {
            "secType": 3,                       # WPA/WPA2-Personal
            "wepSecOpt": sec_opt[0] if len(sec_opt) > 0 else "1",
            "keytype": g(4, 1),
            "keynum": g(10, 1),
            "key1": gl(0), "length1": gl(1, 0),
            "key2": gl(2), "length2": gl(3, 0),
            "key3": gl(4), "length3": gl(5, 0),
            "key4": gl(6), "length4": gl(7, 0),
            "wpaSecOpt": sec_opt[1] if len(sec_opt) > 1 else "3",
            "wpaCipher": g(13, 1),
            "radiusIp": g(6, ""),
            "radiusPort": g(7, 1812),
            "radiusSecret": g(8, ""),
            "intervalWpa": g(15, 0),
            "pskSecOpt": sec_opt[2] if len(sec_opt) > 2 else "3",
            "pskCipher": g(14, 3),
            "pskSecret": new_password,
            "interval": g(11, 0),
            "Save": "Save",
        }
        await self._get("/userRpm/WlanSecurityRpm.htm", params)

    # --- Продвинутый «сырой» доступ --------------------------------------
    async def raw_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Сырой GET к /userRpm/*. Полный доступ к панели для экспертных задач."""
        path = path.strip()
        if not path.startswith("/"):
            path = "/" + path
        # ограничиваем песочницу путями веб-морды роутера
        if not re.match(r"^/(userRpm|dynaform|help)/", path):
            raise RouterError("Разрешены только пути /userRpm/, /dynaform/, /help/")
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as e:
            raise RouterError(f"Роутер недоступен: {e}") from e
        return {"status": resp.status_code, "body": resp.text}

    # --- Особые операции -------------------------------------------------
    async def get_page_arrays(self, htm: str, arrays: list[str]) -> dict[str, list]:
        """Считывает указанные JS-массивы со страницы (для рендера особых страниц)."""
        t = await self._get(f"/userRpm/{htm}")
        return {a: (parse_js_array(t, a) or []) for a in arrays}

    async def factory_reset(self) -> None:
        await self._get("/userRpm/RestoreDefaultCfgRpm.htm", {"Restorefactory": "Restore"})

    async def change_login(self, new_user: str, new_pass: str,
                           old_user: str | None = None, old_pass: str | None = None) -> None:
        """Меняет логин/пароль роутера и СРАЗУ обновляет креды клиента в памяти."""
        old_user = old_user or settings.router_username
        old_pass = old_pass or settings.router_password
        if not (1 <= len(new_user) <= 15 and 1 <= len(new_pass) <= 15):
            raise RouterError("Логин и пароль роутера: 1–15 символов, без пробелов.")
        await self._get("/userRpm/ChangeLoginPwdRpm.htm", {
            "oldname": old_user, "oldpassword": old_pass,
            "newname": new_user, "newpassword": new_pass, "newpassword2": new_pass,
            "Save": "Save",
        })
        # после смены — обновляем авторизацию, иначе следующий запрос упадёт 401
        self._client.auth = httpx.BasicAuth(new_user, new_pass)
        settings.router_username = new_user
        settings.router_password = new_pass
        log.warning("Креды роутера изменены в памяти. Обнови ROUTER_USERNAME/ROUTER_PASSWORD "
                    "в env, иначе после рестарта панель потеряет доступ.")

    async def backup_config(self) -> tuple[bytes, str]:
        """Скачивает бинарный бэкап конфигурации (/userRpm/config.bin)."""
        try:
            resp = await self._client.get(
                "/userRpm/config.bin",
                headers={"Referer": f"{settings.router_url}/userRpm/BakNRestoreRpm.htm"},
            )
        except httpx.HTTPError as e:
            raise RouterError(f"Роутер недоступен: {e}") from e
        if resp.status_code >= 400:
            raise RouterError(f"Не удалось скачать бэкап: HTTP {resp.status_code}")
        return resp.content, "config.bin"

    async def restore_config(self, file_bytes: bytes, filename: str) -> None:
        await self._post_file(
            "/incoming/RouterBakCfgUpload.cfg", "BakNRestoreRpm.htm",
            field="filename", file_bytes=file_bytes, filename=filename or "config.bin",
            data={"Restore": "Restore"}, timeout=120.0,
        )

    async def upgrade_firmware(self, file_bytes: bytes, filename: str) -> None:
        if not filename.lower().endswith(".bin"):
            raise RouterError("Файл прошивки должен иметь расширение .bin")
        await self._post_file(
            "/incoming/Firmware.htm", "SoftwareUpgradeRpm.htm",
            field="Filename", file_bytes=file_bytes, filename=filename,
            data={"Upgrade": "Upgrade"}, timeout=300.0,
        )

    async def _post_file(self, path: str, referer_page: str, *, field: str,
                         file_bytes: bytes, filename: str, data: dict, timeout: float) -> None:
        files = {field: (filename, file_bytes, "application/octet-stream")}
        try:
            resp = await self._client.post(
                path, files=files, data=data, timeout=timeout,
                headers={"Referer": f"{settings.router_url}/userRpm/{referer_page}"},
            )
        except httpx.HTTPError as e:
            raise RouterError(f"Роутер недоступен или оборвал связь: {e}") from e
        if resp.status_code >= 400:
            raise RouterError(f"Роутер вернул HTTP {resp.status_code}")

    async def run_diagnostic(self, ping_addr: str, do_type: str = "ping",
                             send_num: int = 4, p_size: int = 64,
                             over_time: int = 800, tr_hops: int = 20) -> str:
        """Ping/traceroute через /userRpm/PingIframeRpm.htm (параметр адреса — ping_addr)."""
        if do_type not in ("ping", "tracert"):
            raise RouterError("doType должен быть ping или tracert")
        params = {
            "ping_addr": ping_addr, "doType": do_type, "isNew": "new",
            "sendNum": send_num, "pSize": p_size, "overTime": over_time, "trHops": tr_hops,
        }
        try:
            resp = await self._client.get("/userRpm/PingIframeRpm.htm", params=params, timeout=60.0)
        except httpx.HTTPError as e:
            raise RouterError(f"Роутер недоступен: {e}") from e
        return _format_ping(resp.text, ping_addr)

    async def wps_action(self, params: dict[str, Any]) -> None:
        await self._get("/userRpm/WpsCfgRpm.htm", params)


def _format_ping(html: str, addr: str) -> str:
    """Превращает сырой ответ PingIframeRpm в читаемые строки.

    ping_verbos_result: записи по 6 значений [seq, ip, bytes, ttl, time_ms, ok].
    Если разобрать не удалось — возвращаем очищенный от HTML текст.
    """
    res = parse_js_array(html, "ping_verbos_result")
    lines = [f"Диагностика: {addr}"]
    parsed = False
    if res:
        i = 0
        while i + 5 < len(res):
            seq, ip, by, ttl, tm, ok = res[i:i + 6]
            if ip and ip != 0:
                parsed = True
                if ok:
                    lines.append(f"  Ответ от {ip}: байт={by}, TTL={ttl}, время={tm} мс")
                else:
                    lines.append(f"  Превышен интервал ожидания (от {ip})")
            i += 6
    if parsed:
        return "\n".join(lines)
    # фолбэк: вырезаем HTML-обвязку, оставляя содержимое
    text = re.sub(r"(?is)<script.*?</script>", "", html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+\n", "\n", text).strip()
    return text or "Нет данных от роутера."


def _fmt_uptime(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d} дн {h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"
