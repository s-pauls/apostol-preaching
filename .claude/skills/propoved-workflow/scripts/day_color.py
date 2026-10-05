#!/usr/bin/env python3
"""Цвет облачений на заданную дату: берётся из ленты дня на azbyka.ru/days/YYYY-MM-DD.

В тексте страницы цвета нет, он задан стилем ленты <div class="… dayinfo_color" style="background-color: #RRGGBB;">
(см. «Цвет ленты» на https://azbyka.ru/days/p-pojasnenija-k-kalendarju#oblach). fetch_page.py HTML не
показывает, поэтому цвет достаёт этот скрипт.

Пример:
  python day_color.py 2026-10-11
  # 2026-10-11 | Неделя 19-я по Пятидесятнице | #FFBF00 | золотой

Названия по оттенкам ленты (наблюдались на сайте): #FFBF00 золотой, #FFFFFF белый, #34C924 зелёный,
#FF4500 красный, #7FC7FF голубой, #BA55D3 фиолетовый, #808080 серый (чёрные облачения Великого поста).
Если оттенок другой, скрипт пишет «не определён»: сверь по странице «Цвета облачений», по памяти не называй.

Код возврата: 0 — успех; 2 — на странице нет ленты; 3 — ошибка сети. Зависимости: requests, beautifulsoup4.
"""
import re
import sys

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Нужны пакеты: pip install requests beautifulsoup4")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

NAMES = {
    "#FFBF00": "золотой",
    "#FFFFFF": "белый",
    "#34C924": "зелёный",
    "#FF4500": "красный",
    "#7FC7FF": "голубой",
    "#BA55D3": "фиолетовый",
    "#808080": "серый (чёрные облачения Великого поста)",
}


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", sys.argv[1]):
        print(__doc__.strip().split("\n\n")[0] + "\n\nИспользование: day_color.py YYYY-MM-DD", file=sys.stderr)
        return 1
    date = sys.argv[1]
    url = f"https://azbyka.ru/days/{date}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; propoved-workflow/1.0)",
                                       "Accept-Language": "ru"}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Ошибка сети: {e}", file=sys.stderr)
        return 3
    r.encoding = "utf-8"
    ribbon = BeautifulSoup(r.text, "html.parser").select_one(".dayinfo_color")
    m = re.search(r"background-color:\s*(#[0-9A-Fa-f]{6})", ribbon.get("style", "")) if ribbon else None
    if not m:
        print(f"На странице {url} нет ленты с цветом (разметка изменилась?)", file=sys.stderr)
        return 2
    hexcolor = m.group(1).upper()
    title = re.sub(r"\s+", " ", ribbon.get_text(" ").replace("\xa0", " ")).strip()
    title = re.sub(r"\s+([.,])", r"\1", title)
    print(f"{date} | {title} | {hexcolor} | {NAMES.get(hexcolor, 'не определён: сверь по странице «Цвета облачений»')}")
    print(f"Источник: {url} (лента дня), расшифровка: https://azbyka.ru/days/p-pojasnenija-k-kalendarju#oblach")
    return 0


if __name__ == "__main__":
    sys.exit(main())
