"""Реестр PageSpec для всех страниц роутера.

Источники:
  * несколько провалидированных вручную деклараций (_SPECS ниже);
  * запечённые дескрипторы из app/pages_data.json (генерируются из _probe/desc
    скриптом tools/build_pages.py после реверс-воркфлоу).

Универсальный движок (engine.py) исполняет эти декларации. Особые страницы
(kind="special") обслуживаются обработчиками в specials.py по полю handler.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .pagespec import Column, Field, PageSpec

log = logging.getLogger("routerdebugger.registry")

# --- Провалидированные вручную страницы (служат и как эталон формата) -------
_SPECS: list[PageSpec] = [
    PageSpec(
        id="NetworkCfgRpm", title="LAN", section="Сеть", kind="form",
        danger="Смена IP-адреса LAN изменит адрес роутера — панель потеряет связь, "
               "пока не обновишь ROUTER_URL. Меняй с осторожностью.",
        fields=[
            Field("lanip", "IP-адрес LAN", "ip", src=("lanPara", 1)),
            Field("lanmask", "Маска подсети", "select", src=("lanPara", 2), options=[
                ("0", "255.0.0.0"), ("1", "255.255.0.0"), ("2", "255.255.255.0"), ("3", "Другая маска"),
            ]),
            Field("inputMask", "Своя маска", "ip", src=("lanPara", 3), show_if=("lanmask", "3"), optional=True),
            Field("_mac", "MAC LAN", "static", src=("lanPara", 0), readonly=True),
        ],
        save_extra={"Save": "Save"}, reboot_note=True,
    ),
    PageSpec(
        id="DMZRpm", title="DMZ", section="Переадресация", kind="form",
        fields=[
            Field("enable", "DMZ", "radio", src=("DMZInf", 0), options=[("1", "Включить"), ("0", "Выключить")]),
            Field("ipAddr", "IP хоста DMZ", "ip", src=("DMZInf", 1)),
        ],
        save_extra={"netmask": "255.255.255.0", "Save": "Save"},
    ),
    PageSpec(
        id="FixMapCfgRpm", title="Резервирование адресов", section="DHCP", kind="list",
        list_array="dhcpList", para_array="DHCPStaticPara",
        count_index=2, stride_index=3, perpage_index=4, page_index=0, hasmore_index=1,
        columns=[Column("MAC", 0, "mac"), Column("Зарезервированный IP", 1, "ip"), Column("Статус", 2, "status")],
        do_all=["EnAll", "DisAll", "DelAll"], reboot_note=True,
    ),
    PageSpec(
        id="AccessCtrlHostsListsRpm", title="Узлы (хосты)", section="Контроль доступа", kind="list",
        list_array="hosts_lists_data_param", para_array="hosts_lists_page_param",
        count_index=2, stride_index=3, page_index=0, hasmore_index=1,
        columns=[Column("Тип", 0), Column("Описание", 1), Column("IP-начало", 2, "ip"),
                 Column("IP-конец", 3, "ip"), Column("MAC", 4, "mac")],
        do_all=["DelAll"],
    ),
]


# --- Конвертация дескриптора (dict) -> PageSpec -----------------------------
def _mk_field(d: dict) -> Field:
    src = d.get("src")
    src_t = (src[0], int(src[1])) if isinstance(src, (list, tuple)) and len(src) == 2 else None
    opts = [(str(v), str(l)) for v, l in (d.get("options") or []) if v is not None]
    show = d.get("show_if")
    show_t = (show[0], str(show[1])) if isinstance(show, (list, tuple)) and len(show) == 2 else None
    return Field(
        name=d["name"], label=d.get("label", d["name"]), type=d.get("type", "text"),
        src=src_t, options=opts, checked_value=str(d.get("checked_value", "1")),
        show_if=show_t, optional=bool(d.get("optional", False)), readonly=bool(d.get("readonly", False)),
    )


def from_descriptor(d: dict) -> PageSpec:
    kind = d.get("kind", "form")
    save_extra = dict(d.get("save_extra") or {})
    # hidden-поля с фиксированными значениями переносим в save_extra
    for f in d.get("fields", []):
        if f.get("type") == "hidden" and f.get("hidden_value") is not None:
            save_extra.setdefault(f["name"], f["hidden_value"])
    fields = [_mk_field(f) for f in d.get("fields", []) if f.get("type") != "hidden" or f.get("hidden_value") is None]
    cols = [Column(c.get("label", ""), int(c.get("index", 0)), c.get("kind", "text")) for c in d.get("columns", [])]
    add_fields = [_mk_field(f) for f in d.get("add_fields", [])]
    return PageSpec(
        id=d["id"], title=d.get("title", d["id"]), section=d.get("section", ""), kind=kind,
        htm=d.get("htm", ""), danger=d.get("danger", ""), reboot_note=bool(d.get("reboot_note", False)),
        fields=fields, save_extra=save_extra,
        list_array=d.get("list_array", ""), para_array=d.get("para_array", ""),
        count_index=int(d.get("count_index", -1)), stride_index=int(d.get("stride_index", -1)),
        perpage_index=int(d.get("perpage_index", -1)), page_index=int(d.get("page_index", 0)),
        hasmore_index=int(d.get("hasmore_index", 1)),
        columns=cols, add_fields=add_fields, do_all=list(d.get("do_all") or []),
        arrays=list(d.get("arrays") or []), handler=d.get("handler", ""),
        extra={k: d[k] for k in ("special_actions", "readonly_display", "special_notes") if k in d},
    )


def _load_baked() -> list[PageSpec]:
    path = Path(__file__).resolve().parent / "pages_data.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось прочитать pages_data.json: %s", e)
        return []
    out = []
    for d in data:
        try:
            out.append(from_descriptor(d))
        except Exception as e:  # noqa: BLE001
            log.warning("Пропущен дескриптор %s: %s", d.get("id"), e)
    return out


REGISTRY: dict[str, PageSpec] = {}
for _s in _SPECS + _load_baked():
    REGISTRY[_s.id] = _s


def get(page_id: str) -> PageSpec | None:
    return REGISTRY.get(page_id)
