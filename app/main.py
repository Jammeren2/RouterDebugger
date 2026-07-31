"""FastAPI-приложение: вход в панель + JSON-API к роутеру + статика дашборда."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import engine
from .config import settings
from .pagespec import MENU
from .registry import REGISTRY
from .registry import get as get_spec
from .router_client import RouterClient, RouterError, normalize_port_expression
from .security import (
    client_key,
    get_csrf,
    login_session,
    logout_session,
    rate_limiter,
    require_csrf,
    require_user,
    verify_credentials,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("routerdebugger")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    for problem in settings.validate():
        log.warning(problem)
    app.state.router = RouterClient()
    log.info("RouterDebugger запущен. Роутер: %s", settings.router_url)
    try:
        yield
    finally:
        await app.state.router.aclose()


app = FastAPI(title="RouterDebugger", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=settings.session_max_age,
    same_site="strict",
    https_only=settings.cookie_secure,
    session_cookie="rd_session",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_router(request: Request) -> RouterClient:
    return request.app.state.router


# ---------------------------------------------------------------------------
# Аутентификация / страницы
# ---------------------------------------------------------------------------
@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(request: Request, username: str = Form(""), password: str = Form("")):
    key = client_key(request)
    wait = rate_limiter.check(key)
    if wait:
        return TEMPLATES.TemplateResponse(
            request, "login.html",
            {"error": f"Слишком много попыток. Подожди {wait} сек."},
            status_code=429,
        )
    if verify_credentials(username, password):
        rate_limiter.reset(key)
        login_session(request, username)
        return RedirectResponse("/", status_code=302)
    rate_limiter.register_failure(key)
    log.warning("Неудачный вход с %s (user=%r)", key, username)
    return TEMPLATES.TemplateResponse(
        request, "login.html", {"error": "Неверный логин или пароль."}, status_code=401,
    )


@app.post("/logout", include_in_schema=False)
async def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/login", status_code=302)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(
        request, "index.html",
        {
            "csrf": get_csrf(request),
            "user": request.session.get("user"),
            "router_url": settings.router_url,
            "raw_enabled": settings.enable_raw_console,
        },
    )


# ---------------------------------------------------------------------------
# JSON-API (всё под авторизацией)
# ---------------------------------------------------------------------------
def _err(e: RouterError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/status")
async def api_status(_: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    try:
        return {"ok": True, "data": await r.get_status()}
    except RouterError as e:
        return _err(e)


@app.get("/api/devices")
async def api_devices(_: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    try:
        return {
            "ok": True,
            "data": {
                "dhcp_clients": await r.get_dhcp_clients(),
                "wlan_stations": await r.get_wlan_stations(),
            },
        }
    except RouterError as e:
        return _err(e)


@app.get("/api/portforward")
async def api_portforward(page: int = 1, _: str = Depends(require_user),
                          r: RouterClient = Depends(get_router)):
    try:
        return {"ok": True, "data": await r.get_virtual_servers(max(1, page))}
    except RouterError as e:
        return _err(e)


@app.get("/api/dhcp")
async def api_dhcp(_: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    try:
        return {"ok": True, "data": await r.get_dhcp_settings()}
    except RouterError as e:
        return _err(e)


@app.get("/api/wlan")
async def api_wlan(_: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    try:
        net = await r.get_wlan_network()
        sec = await r.get_wlan_security()
        net.pop("_raw", None)
        sec = {k: v for k, v in sec.items() if not k.startswith("_raw")}
        return {"ok": True, "data": {"network": net, "security": sec}}
    except RouterError as e:
        return _err(e)


# --- Действия (POST + CSRF) -------------------------------------------------
@app.post("/api/reboot")
async def api_reboot(request: Request, _: str = Depends(require_user),
                     r: RouterClient = Depends(get_router)):
    require_csrf(request)
    try:
        await r.reboot()
        log.info("Перезагрузка роутера инициирована пользователем %s", request.session.get("user"))
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/portforward/add")
async def api_pf_add(request: Request, _: str = Depends(require_user),
                     r: RouterClient = Depends(get_router)):
    require_csrf(request)
    body = await request.json()
    try:
        ext_port = normalize_port_expression(body["ext_port"])
        int_port = normalize_port_expression(body.get("int_port") or ext_port)
        await r.vs_add(
            ext_port=ext_port,
            int_port=int_port,
            ip=str(body["ip"]).strip(),
            protocol=int(body.get("protocol", 1)),
            state=1 if body.get("enabled", True) else 0,
            page=max(1, int(body.get("page", 1))),
        )
        return {"ok": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Некорректные данные: {e}")
    except RouterError as e:
        return _err(e)


@app.post("/api/portforward/delete")
async def api_pf_delete(request: Request, _: str = Depends(require_user),
                        r: RouterClient = Depends(get_router)):
    require_csrf(request)
    body = await request.json()
    try:
        await r.vs_delete(int(body["id"]), max(1, int(body.get("page", 1))))
        return {"ok": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Некорректные данные: {e}")
    except RouterError as e:
        return _err(e)


@app.post("/api/portforward/all")
async def api_pf_all(request: Request, _: str = Depends(require_user),
                     r: RouterClient = Depends(get_router)):
    require_csrf(request)
    body = await request.json()
    action = body.get("action", "")
    try:
        await r.vs_do_all(action, max(1, int(body.get("page", 1))))
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Некорректные данные: {e}")
    except RouterError as e:
        return _err(e)


@app.post("/api/dhcp/save")
async def api_dhcp_save(request: Request, _: str = Depends(require_user),
                        r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    try:
        await r.dhcp_save(
            enabled=bool(b.get("enabled", True)),
            start_ip=str(b.get("start_ip", "")).strip(),
            end_ip=str(b.get("end_ip", "")).strip(),
            lease=str(b.get("lease", "120")).strip(),
            gateway=str(b.get("gateway", "")).strip(),
            domain=str(b.get("domain", "")).strip(),
            dns1=str(b.get("dns1", "")).strip(),
            dns2=str(b.get("dns2", "")).strip(),
        )
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/wlan/save")
async def api_wlan_save(request: Request, _: str = Depends(require_user),
                        r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    try:
        kwargs = {}
        if "ssid" in b:
            kwargs["ssid"] = str(b["ssid"])
        if "radio_on" in b:
            kwargs["radio_on"] = bool(b["radio_on"])
        if "ssid_broadcast" in b:
            kwargs["ssid_broadcast"] = bool(b["ssid_broadcast"])
        if "channel" in b and b["channel"] != "":
            kwargs["channel"] = int(b["channel"])
        if "mode" in b and b["mode"] != "":
            kwargs["mode"] = int(b["mode"])
        await r.wlan_save(**kwargs)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Некорректные данные: {e}")
    except RouterError as e:
        return _err(e)


@app.post("/api/wlan/password")
async def api_wlan_password(request: Request, _: str = Depends(require_user),
                            r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    try:
        await r.wlan_set_password(str(b.get("password", "")))
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/raw")
async def api_raw(request: Request, _: str = Depends(require_user),
                  r: RouterClient = Depends(get_router)):
    require_csrf(request)
    if not settings.enable_raw_console:
        raise HTTPException(status_code=403, detail="Сырой доступ отключён (ENABLE_RAW_CONSOLE=false)")
    b = await request.json()
    path = str(b.get("path", "")).strip()
    params = b.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params должен быть объектом")
    try:
        return {"ok": True, "data": await r.raw_get(path, params)}
    except RouterError as e:
        return _err(e)


# ===========================================================================
# Единое меню + универсальные страницы (spec-driven движок) + особые страницы
# ===========================================================================
# Страницы с «родными» красивыми компонентами фронтенда (рендерятся отдельно).
CUSTOM_PAGES: dict[str, tuple[str, str]] = {
    "StatusRpm": ("Состояние", "overview"),
    "WlanNetworkRpm": ("Беспроводной режим", "wifi"),
    "WlanSecurityRpm": ("Защита Wi-Fi (пароль)", "wifi"),
    "WlanStationRpm": ("Статистика Wi-Fi", "stations"),
    "AssignedIpAddrListRpm": ("Список клиентов DHCP", "dhcp-clients"),
    "LanDhcpServerRpm": ("Настройки DHCP", "dhcp"),
    "VirtualServerRpm": ("Виртуальные серверы", "portforward"),
    "SysRebootRpm": ("Перезагрузка", "reboot"),
}


def _page_meta(page_id: str) -> dict | None:
    if page_id in CUSTOM_PAGES:
        title, handler = CUSTOM_PAGES[page_id]
        return {"id": page_id, "title": title, "kind": "custom", "handler": handler, "danger": ""}
    spec = REGISTRY.get(page_id)
    if spec:
        return {"id": page_id, "title": spec.title, "kind": spec.kind,
                "handler": spec.handler, "danger": spec.danger}
    return None


@app.get("/api/menu")
async def api_menu(_: str = Depends(require_user)):
    sections = []
    for name, ids in MENU:
        pages = [m for pid in ids if (m := _page_meta(pid))]
        if pages:
            sections.append({"section": name, "pages": pages})
    return {"ok": True, "data": sections}


@app.get("/api/page/{page_id}")
async def api_page(page_id: str, page: int = 1, _: str = Depends(require_user),
                   r: RouterClient = Depends(get_router)):
    spec = get_spec(page_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Неизвестная страница")
    try:
        if spec.kind == "special":
            state = await r.get_page_arrays(spec.htm, spec.arrays) if spec.arrays else {}
            return {"ok": True, "data": {
                "id": spec.id, "title": spec.title, "kind": "special", "handler": spec.handler,
                "danger": spec.danger, "reboot_note": spec.reboot_note,
                "fields": engine.parse_form(spec, "")["fields"] if spec.fields else [],
                "state": state, "extra": spec.extra,
            }}
        return {"ok": True, "data": await engine.read_page(r, spec, page)}
    except RouterError as e:
        return _err(e)


@app.post("/api/page/{page_id}/save")
async def api_page_save(page_id: str, request: Request, _: str = Depends(require_user),
                        r: RouterClient = Depends(get_router)):
    require_csrf(request)
    spec = get_spec(page_id)
    if not spec or spec.kind != "form":
        raise HTTPException(status_code=400, detail="Страница не является формой")
    values = await request.json()
    try:
        await engine.save_page(r, spec, values)
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/page/{page_id}/list/{op}")
async def api_page_list(page_id: str, op: str, request: Request, _: str = Depends(require_user),
                        r: RouterClient = Depends(get_router)):
    require_csrf(request)
    spec = get_spec(page_id)
    if not spec or spec.kind != "list":
        raise HTTPException(status_code=400, detail="Страница не является списком")
    try:
        body = await request.json()
        page = max(1, int(body.get("page", 1)))
        if op == "add":
            values = dict(body.get("values", {}))
            if page_id == "SpecialAppRpm":
                values["trPort"] = normalize_port_expression(
                    values.get("trPort", ""), allow_range=False,
                )
                values["inPort"] = normalize_port_expression(
                    values.get("inPort", ""), allow_list=True,
                )
            await engine.list_add(r, spec, values, page)
        elif op == "delete":
            await engine.list_delete(r, spec, int(body["id"]), page)
        elif op == "doall":
            await engine.list_do_all(r, spec, body.get("action", ""), page)
        else:
            raise HTTPException(status_code=400, detail="Неизвестная операция")
        return {"ok": True}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Некорректные данные: {e}")
    except RouterError as e:
        return _err(e)


# --- Особые операции --------------------------------------------------------
@app.post("/api/special/factory-reset")
async def api_factory_reset(request: Request, _: str = Depends(require_user),
                            r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    if b.get("confirm") != "RESET":
        raise HTTPException(status_code=400, detail="Нужно подтверждение")
    try:
        await r.factory_reset()
        log.warning("ЗАВОДСКОЙ СБРОС инициирован пользователем %s", request.session.get("user"))
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/special/password-change")
async def api_password_change(request: Request, _: str = Depends(require_user),
                              r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    try:
        await r.change_login(
            new_user=str(b.get("new_user", "")).strip(),
            new_pass=str(b.get("new_pass", "")),
            old_user=str(b.get("old_user", "")).strip() or None,
            old_pass=str(b.get("old_pass", "")) or None,
        )
        return {"ok": True, "note": "Креды роутера обновлены в памяти. Не забудь обновить "
                "ROUTER_USERNAME/ROUTER_PASSWORD в env для сохранения после рестарта."}
    except RouterError as e:
        return _err(e)


@app.post("/api/special/diagnostic")
async def api_diagnostic(request: Request, _: str = Depends(require_user),
                         r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    try:
        out = await r.run_diagnostic(
            ping_addr=str(b.get("pingAddr") or b.get("ping_addr", "")).strip(),
            do_type=str(b.get("doType", "ping")),
            send_num=int(b.get("sendNum", 4) or 4), p_size=int(b.get("pSize", 64) or 64),
            over_time=int(b.get("overTime", 800) or 800), tr_hops=int(b.get("trHops", 20) or 20),
        )
        return {"ok": True, "output": out}
    except (ValueError, RouterError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.post("/api/special/wps")
async def api_wps(request: Request, _: str = Depends(require_user),
                  r: RouterClient = Depends(get_router)):
    require_csrf(request)
    b = await request.json()
    params = b.get("params") or {}
    if not isinstance(params, dict) or not params:
        raise HTTPException(status_code=400, detail="Нет параметров действия")
    try:
        await r.wps_action(params)
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.get("/api/special/backup")
async def api_backup(_: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    try:
        data, name = await r.backup_config()
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    except RouterError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/special/restore")
async def api_restore(request: Request, file: UploadFile = File(...),
                      _: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    require_csrf(request)
    try:
        await r.restore_config(await file.read(), file.filename or "config.bin")
        return {"ok": True}
    except RouterError as e:
        return _err(e)


@app.post("/api/special/firmware")
async def api_firmware(request: Request, file: UploadFile = File(...),
                       _: str = Depends(require_user), r: RouterClient = Depends(get_router)):
    require_csrf(request)
    try:
        await r.upgrade_firmware(await file.read(), file.filename or "")
        return {"ok": True}
    except RouterError as e:
        return _err(e)
