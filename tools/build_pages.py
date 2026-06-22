"""Сборка app/pages_data.json из дескрипторов _probe/desc/*.json.

Дев-инструмент: после реверс-воркфлоу прогоняет каждый дескриптор через
конвертер from_descriptor (валидация), делает базовые проверки и пишет
объединённый, коммитимый app/pages_data.json, который грузит приложение.

Запуск:  python tools/build_pages.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.registry import from_descriptor  # noqa: E402

DESC_DIR = ROOT / "_probe" / "desc"
OUT = ROOT / "app" / "pages_data.json"

# Эти страницы уже описаны вручную в registry.py — из дескрипторов исключаем.
HAND = {"NetworkCfgRpm", "DMZRpm", "FixMapCfgRpm", "AccessCtrlHostsListsRpm"}


def main() -> int:
    if not DESC_DIR.exists():
        print(f"Нет папки {DESC_DIR}")
        return 1
    descriptors = []
    problems = []
    for path in sorted(DESC_DIR.glob("*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{path.name}: невалидный JSON — {e}")
            continue
        pid = d.get("id") or path.stem
        d["id"] = pid
        if pid in HAND:
            continue
        # валидация через конвертер
        try:
            spec = from_descriptor(d)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{pid}: ошибка конвертации — {e}")
            continue
        # санити-проверки
        if spec.kind == "list" and not (spec.list_array and spec.para_array):
            problems.append(f"{pid}: list без list_array/para_array")
        if spec.kind == "form" and not spec.fields:
            problems.append(f"{pid}: form без полей")
        descriptors.append(d)

    descriptors.sort(key=lambda x: x["id"])
    OUT.write_text(json.dumps(descriptors, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Записано {len(descriptors)} дескрипторов -> {OUT.relative_to(ROOT)}")
    by_kind: dict[str, int] = {}
    for d in descriptors:
        by_kind[d.get("kind", "?")] = by_kind.get(d.get("kind", "?"), 0) + 1
    print("По типам:", by_kind)
    if problems:
        print("\nПРОБЛЕМЫ (", len(problems), "):", sep="")
        for p in problems:
            print("  -", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
