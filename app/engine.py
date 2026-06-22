"""Универсальный движок чтения/записи страниц по их PageSpec.

Парсинг (offline-тестируемый, работает на любом HTML-тексте) отделён от
сетевых операций (требуют живого роутера через RouterClient).
"""
from __future__ import annotations

from typing import Any

from .pagespec import Column, Field, PageSpec
from .router_client import RouterClient, RouterError, parse_js_array


# ---------------------------------------------------------------------------
# Чистый парсинг (без сети) — удобно тестировать на сохранённом HTML
# ---------------------------------------------------------------------------
def _arr_get(arr: list | None, idx: int, default: Any = "") -> Any:
    if arr is None or idx < 0 or idx >= len(arr):
        return default
    return arr[idx]


def parse_form(spec: PageSpec, html: str) -> dict[str, Any]:
    """Возвращает текущие значения полей формы + метаданные для рендера."""
    cache: dict[str, list] = {}

    def arr(name: str) -> list:
        if name not in cache:
            cache[name] = parse_js_array(html, name) or []
        return cache[name]

    out_fields = []
    for f in spec.fields:
        value: Any = ""
        if f.src:
            value = _arr_get(arr(f.src[0]), f.src[1])
        out_fields.append({
            "name": f.name,
            "label": f.label,
            "type": f.type,
            "value": value,
            "options": [{"value": str(v), "label": l} for v, l in f.options],
            "checked_value": f.checked_value,
            "show_if": list(f.show_if) if f.show_if else None,
            "help": f.help,
            "optional": f.optional,
            "readonly": f.readonly,
        })
    return {"id": spec.id, "title": spec.title, "kind": "form",
            "danger": spec.danger, "reboot_note": spec.reboot_note, "fields": out_fields}


def build_save_params(spec: PageSpec, values: dict[str, Any]) -> dict[str, Any]:
    """Строит GET-параметры сохранения формы по присланным значениям."""
    params: dict[str, Any] = {}
    for f in spec.fields:
        if f.type in ("button", "static") or f.readonly:
            continue
        if f.type == "checkbox":
            if values.get(f.name):
                params[f.name] = f.checked_value
            # выключенный чекбокс не отправляется (как в браузере)
        else:
            if f.name in values:
                params[f.name] = values[f.name]
            elif f.src is not None:
                # значение не прислали — отправим пустую строку, чтобы поле не потерялось
                params[f.name] = ""
    params.update(spec.save_extra)
    return params


def parse_list(spec: PageSpec, html: str, page: int = 1) -> dict[str, Any]:
    """Возвращает строки списка + состояние пагинации."""
    lst = parse_js_array(html, spec.list_array) or []
    para = parse_js_array(html, spec.para_array) or []
    count = int(_arr_get(para, spec.count_index, 0) or 0) if spec.count_index >= 0 else 0
    stride = int(_arr_get(para, spec.stride_index, len(spec.columns)) or len(spec.columns)) \
        if spec.stride_index >= 0 else len(spec.columns)
    perpage = int(_arr_get(para, spec.perpage_index, 8) or 8) if spec.perpage_index >= 0 else 8
    cur_page = int(_arr_get(para, spec.page_index, page) or page) if spec.page_index >= 0 else page
    has_more = bool(_arr_get(para, spec.hasmore_index, 0)) if spec.hasmore_index >= 0 else False

    rows = []
    for i in range(count):
        base = i * stride
        cells = []
        for c in spec.columns:
            cells.append({"value": _arr_get(lst, base + c.index), "kind": c.kind})
        rows.append({"id": (cur_page - 1) * perpage + i, "cells": cells})

    return {
        "id": spec.id, "title": spec.title, "kind": "list", "danger": spec.danger,
        "columns": [{"label": c.label, "kind": c.kind} for c in spec.columns],
        "rows": rows,
        "page": cur_page, "has_more": has_more,
        "do_all": spec.do_all,
        "can_add": bool(spec.add_fields),
        "add_fields": [_field_dict(f) for f in spec.add_fields],
        "reboot_note": spec.reboot_note,
    }


def _field_dict(f: Field) -> dict[str, Any]:
    return {
        "name": f.name, "label": f.label, "type": f.type,
        "options": [{"value": str(v), "label": l} for v, l in f.options],
        "checked_value": f.checked_value, "help": f.help, "optional": f.optional,
        "show_if": list(f.show_if) if f.show_if else None,
    }


# ---------------------------------------------------------------------------
# Сетевые операции (через RouterClient)
# ---------------------------------------------------------------------------
async def read_page(client: RouterClient, spec: PageSpec, page: int = 1) -> dict[str, Any]:
    params = {"Page": page} if (spec.kind == "list" and page > 1) else None
    html = await client._get(f"/userRpm/{spec.htm}", params)
    if spec.kind == "list":
        return parse_list(spec, html, page)
    if spec.kind == "readonly":
        if spec.list_array and spec.columns:
            out = parse_list(spec, html, page)
            out["kind"] = "readonly"
            out["readonly"] = True
            return out
        data = {a: parse_js_array(html, a) or [] for a in spec.arrays}
        return {"id": spec.id, "title": spec.title, "kind": "readonly", "arrays": data}
    return parse_form(spec, html)


async def save_page(client: RouterClient, spec: PageSpec, values: dict[str, Any]) -> None:
    params = build_save_params(spec, values)
    await client._get(f"/userRpm/{spec.htm}", params)


async def list_add(client: RouterClient, spec: PageSpec, values: dict[str, Any], page: int = 1) -> None:
    params: dict[str, Any] = {}
    for f in spec.add_fields:
        if f.type in ("button", "static"):
            continue
        if f.type == "checkbox":
            if values.get(f.name):
                params[f.name] = f.checked_value
        else:
            params[f.name] = values.get(f.name, "")
    params["Page"] = page
    params["Save"] = "Save"
    await client._get(f"/userRpm/{spec.htm}", params)


async def list_delete(client: RouterClient, spec: PageSpec, entry_id: int, page: int = 1) -> None:
    await client._get(f"/userRpm/{spec.htm}", {"Del": entry_id, "Page": page})


async def list_do_all(client: RouterClient, spec: PageSpec, action: str, page: int = 1) -> None:
    if action not in ("EnAll", "DisAll", "DelAll"):
        raise RouterError("Недопустимое массовое действие")
    await client._get(f"/userRpm/{spec.htm}", {"doAll": action, "Page": page})
