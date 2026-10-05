#!/usr/bin/env python3
"""
Ищет в index.json (файлы проекта «Апостольское чтение») для зачала:
  - перевод архимандрита Ианнуария  -> метка [Ианн]
  - Синодальный перевод             -> метка [СИН]

Запуск:
    python find_translation.py "Гал.5:22-6:2"
    python find_translation.py "2Кор.4:6-15" --prefer "Неделя 15"
    python find_translation.py "Гал.5:22-6:2" --data-dir data --json

Ссылка берётся так, как она записана на azbyka.ru/worships (Гал.5:22-6:2, 2Кор.4:6-15). Скрипт
приводит её к виду книги («гал 5:22–6:2») и сравнивает без учёта регистра, пробелов, точки после
названия книги и вида тире.

index.json ищется в --data-dir, в $PROPOVED_DATA, в <корень проекта>/data, в ./data, в /mnt/project и в текущей папке.
Код возврата: 0 — найден хотя бы один перевод (Ианн или СИН), 1 — зачало в индексе не найдено.
Если у зачала нет перевода Ианнуария, строка [Ианн] просто не выводится.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

MARK = "Перевод архимандрита Ианнуария"


def display_ref(ref: str) -> str:
    """Гал.5:22-6:2 -> гал 5:22–6:2 (нижний регистр, пробел после книги, длинное тире)."""
    s = ref.strip().strip("()").lower().replace("\u00a0", " ").replace("\u2009", " ")
    s = re.sub(r"[\u2010-\u2015\u2212-]", "–", s)
    m = re.match(r"^(\d?\s*[а-яё]+)\.?\s*(\d.*)$", s)
    if m:
        s = f"{m.group(1).replace(' ', '')} {m.group(2).strip()}"
    return re.sub(r"\s*([:,–])\s*", r"\1", s)


def key(ref: str) -> str:
    """Ключ для сравнения: без регистра, пробелов, точек, ударений; любые тире = '-'."""
    s = unicodedata.normalize("NFD", ref)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn" or c == "\u0306")
    s = unicodedata.normalize("NFC", s).lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"[\s\u00a0\u2009]+", "", s).replace(".", "")
    s = s.replace("петр", "пет")  # 1Пет / 1Петр
    return s.strip("()")


def clean_synodal(text: str) -> str:
    """Убирает ссылку на отрывок в конце: ([Гал 5:22–6:2](https://...)) или (3:1–8)."""
    t = re.sub(r"\s*\(\[[^\n]*biblia[^\n]*$", "", text.strip())
    t = re.sub(r"\s*\(\d+[:,][^()\n]*\)\s*$", "", t)
    return t.strip()


def clean_ian(text: str) -> str:
    t = text.strip()
    if t.startswith(MARK):
        t = t[len(MARK):].lstrip(" :")
    return t.strip()


def load_index(data_dir):
    cands = [Path(data_dir)] if data_dir else []
    if os.environ.get("PROPOVED_DATA"):
        cands.append(Path(os.environ["PROPOVED_DATA"]))
    # <корень проекта>/data: скрипт лежит в <корень>/.claude/skills/apostol-chtenie-format/scripts/
    here = Path(__file__).resolve()
    for up in (4, 3):
        if len(here.parents) > up:
            cands.append(here.parents[up] / "data")
    cands += [Path("data"), Path("/mnt/project"), Path(".")]
    for d in cands:
        if (d / "index.json").exists():
            return json.loads((d / "index.json").read_text(encoding="utf-8"))
    sys.exit("Не найден index.json. Он лежит в папке data/ проекта (или укажите --data-dir / переменную PROPOVED_DATA).")


def distinct(texts):
    return len({re.sub(r"[\u0301\s]", "", t) for t in texts if t})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--prefer", help="часть заголовка записи (например «Неделя 15» или «Сергия»), которую выбрать из нескольких")
    ap.add_argument("--data-dir")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    idx = load_index(a.data_dir)
    norm = {key(k): v for k, v in idx.items()}
    hits = norm.get(key(a.ref), [])
    if not hits:
        print(f"Зачало «{display_ref(a.ref)}» в индексе не найдено.", file=sys.stderr)
        sys.exit(1)

    chosen = hits[0]
    if a.prefer:
        p = a.prefer.lower()
        chosen = next((h for h in hits if p in h["heading"].lower()), hits[0])

    ian = clean_ian(chosen["translation"]) if chosen.get("translation") else None
    syn = clean_synodal(chosen["synodal"]) if chosen.get("synodal") else None
    info = {
        "query": display_ref(a.ref), "heading": chosen["heading"], "url": chosen["url"],
        "entries": len(hits), "headings": [h["heading"] for h in hits],
        "distinct_ian_texts": distinct([clean_ian(h["translation"]) for h in hits if h.get("translation")]),
        "distinct_syn_texts": distinct([clean_synodal(h["synodal"]) for h in hits if h.get("synodal")]),
        "ian": ian, "syn": syn,
    }
    if not ian and not syn:
        print(f"Для «{info['query']}» нет ни перевода Ианнуария, ни Синодального текста.", file=sys.stderr)
        sys.exit(1)
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return

    print(f"# запрос: {info['query']} | запись: {info['heading']} | {info['url']}")
    print(f"# записей в книге: {info['entries']}; различающихся текстов (без учёта ударений): "
          f"Ианн — {info['distinct_ian_texts']}, СИН — {info['distinct_syn_texts']}")
    if info["entries"] > 1:
        print("# заголовки: " + "; ".join(info["headings"]))
    print()
    if ian:
        first, _, rest = ian.partition("\n")
        print("[Ианн] " + first + (("\n" + rest) if rest else ""))
        print()
    else:
        print("# перевода Ианнуария нет")
        print()
    if syn:
        first, _, rest = syn.partition("\n")
        print("[СИН] " + first + (("\n" + rest) if rest else ""))


if __name__ == "__main__":
    main()
