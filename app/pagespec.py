"""Декларативное описание страниц роутера (PageSpec) и дерево меню.

Идея: большинство страниц WR740N однотипны — данные в JS-массивах сверху,
форма с именованными полями, сохранение GET-ом. Поэтому каждую обычную
страницу описываем декларативно (PageSpec), а универсальный движок
(`engine.py`) читает/пишет её без бд ручного кода на страницу.

Особые страницы (прошивка, бэкап, сброс, смена пароля роутера, WPS,
диагностика, журнал) помечаются kind="special" и обслуживаются отдельными
обработчиками в `specials.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Field:
    """Одно поле формы."""
    name: str                       # имя GET-параметра (атрибут name в форме роутера)
    label: str                      # подпись в UI (RU)
    type: str = "text"              # text|password|number|select|checkbox|radio|hidden|static|ip|mac|textarea
    src: tuple | None = None        # (имя_массива, индекс) — откуда взять текущее значение
    options: list = field(default_factory=list)   # [(value, label)] для select/radio
    checked_value: str = "1"        # что слать, когда чекбокс включён (у TP-Link бывает "2")
    show_if: tuple | None = None     # (имя_поля, значение) — условный показ
    help: str = ""
    optional: bool = False
    readonly: bool = False


@dataclass
class Column:
    """Колонка таблицы для list-страниц."""
    label: str
    index: int
    kind: str = "text"              # text|status|mac|ip|proto|bool


@dataclass
class PageSpec:
    id: str
    title: str
    section: str
    kind: str = "form"              # form|list|readonly|special
    htm: str = ""                   # по умолчанию <id>.htm
    danger: str = ""                # предупреждение (показывается в UI)
    icon: str = ""

    # --- form ---
    fields: list = field(default_factory=list)
    save_extra: dict = field(default_factory=dict)   # доп. GET-параметры сохранения, напр. {"Save": "Save"}
    reboot_note: bool = False       # изменения требуют перезагрузки роутера

    # --- list ---
    list_array: str = ""            # имя массива со списком записей
    para_array: str = ""            # имя массива с метаданными (счётчики/пагинация)
    count_index: int = -1           # индекс количества записей в para_array
    stride_index: int = -1          # индекс шага записи в para_array
    perpage_index: int = -1         # индекс «записей на страницу» в para_array
    page_index: int = 0             # индекс текущей страницы в para_array
    hasmore_index: int = 1          # индекс флага «есть ещё страница» в para_array
    columns: list = field(default_factory=list)
    add_fields: list = field(default_factory=list)   # поля формы добавления/редактирования
    do_all: list = field(default_factory=list)       # доступные массовые операции: EnAll/DisAll/DelAll

    # --- readonly/special ---
    arrays: list = field(default_factory=list)        # имена массивов, отдать как есть (для readonly/special)
    handler: str = ""                                 # имя обработчика особой страницы
    extra: dict = field(default_factory=dict)         # доп. данные дескриптора (special_actions, readonly_display, …)

    def __post_init__(self):
        if not self.htm:
            self.htm = f"{self.id}.htm"


# ---------------------------------------------------------------------------
# Дерево меню (порядок и группировка как в родной морде роутера)
# Заполняется id страниц; сами PageSpec — в registry.py
# ---------------------------------------------------------------------------
MENU: list[tuple[str, list[str]]] = [
    ("Состояние", ["StatusRpm"]),
    ("Быстрая настройка", ["WzdStartRpm"]),
    ("WPS", ["WpsCfgRpm"]),
    ("Сеть", ["WanCfgRpm", "MacCloneCfgRpm", "NetworkCfgRpm", "LanBrModeRpm"]),
    ("Беспроводной режим", [
        "WlanNetworkRpm", "WlanSecurityRpm", "WlanMacFilterRpm", "WlanAdvRpm", "WlanStationRpm",
    ]),
    ("DHCP", ["LanDhcpServerRpm", "AssignedIpAddrListRpm", "FixMapCfgRpm"]),
    ("Переадресация", ["VirtualServerRpm", "SpecialAppRpm", "DMZRpm", "UpnpCfgRpm"]),
    ("Защита", ["BasicSecurityRpm", "AdvScrRpm", "LocalManageControlRpm", "ManageControlRpm"]),
    ("Родительский контроль", ["ParentCtrlRpm"]),
    ("Контроль доступа", [
        "AccessCtrlAccessRulesRpm", "AccessCtrlHostsListsRpm",
        "AccessCtrlAccessTargetsRpm", "AccessCtrlTimeSchedRpm",
    ]),
    ("Маршрутизация", ["StaticRouteTableRpm", "SysRouteTableRpm"]),
    ("Контроль пропускной способности", ["QoSCfgRpm", "QoSRuleListRpm"]),
    ("Привязка IP и MAC", ["LanArpBindingRpm", "LanArpBindingListRpm"]),
    ("Динамический DNS", ["DdnsAddRpm"]),
    ("Системные инструменты", [
        "DateTimeCfgRpm", "DiagnosticRpm", "SoftwareUpgradeRpm", "RestoreDefaultCfgRpm",
        "BakNRestoreRpm", "SysRebootRpm", "ChangeLoginPwdRpm", "SystemLogRpm", "SystemStatisticRpm",
    ]),
]
