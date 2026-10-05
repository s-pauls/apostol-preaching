#!/usr/bin/env python3
"""Скачивает страницу (или читает сохранённый HTML) и выдаёт её текст ДОСЛОВНО.

Зачем: встроенный WebFetch в Claude Code пересказывает страницу моделью, а для богослужебных
текстов (церковнославянский с ударениями, номера зачал) нужна точная копия. Этот скрипт ничего
не пересказывает: он только превращает HTML в текст с сохранением абзацев и переносов строк.

Примеры:
  # вся страница
  python fetch_page.py "https://azbyka.ru/days/2026-10-11"

  # раздел «Чтение Апостола» (от заголовка с id до следующего заголовка с id)
  python fetch_page.py "https://azbyka.ru/worships/?date=2026-10-11&worship=liturgy" \
      --from-id chtenie-apostola --to-id chtenie-evangelija

  # ссылки сохранить как [текст](url)
  python fetch_page.py URL --links

  # посмотреть, какие id есть на странице (если разметка сайта изменилась)
  python fetch_page.py URL --list-ids

  # сохранённый вручную HTML вместо URL
  python fetch_page.py saved_page.html --from-id chtenie-apostola --to-id chtenie-evangelija

Код возврата: 0 — успех; 2 — нет такого id/селектора; 3 — ошибка сети. Зависимости: requests, beautifulsoup4.
"""
import argparse
import re
import sys
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:
    sys.exit("Нужны пакеты: pip install requests beautifulsoup4")

UA = "Mozilla/5.0 (compatible; propoved-workflow/1.0)"
BLOCK = {"p", "div", "section", "article", "li", "ul", "ol", "tr", "table", "blockquote",
         "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "nav", "main", "dd", "dt", "dl"}
SKIP = {"script", "style", "noscript", "template", "svg"}
MARK = "\x00ID:{}\x00"


# Windows: при выводе в канал/файл Python берёт локальную кодовую страницу (cp1251/cp1252),
# и кириллица с ударениями вызывает UnicodeEncodeError. Принудительно используем UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_html(src: str) -> str:
    if re.match(r"^https?://", src):
        try:
            r = requests.get(src, headers={"User-Agent": UA, "Accept-Language": "ru"}, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"Ошибка сети: {e}", file=sys.stderr)
            sys.exit(3)
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
        return r.text
    return Path(src).read_text(encoding="utf-8")


def render(node, out, links):
    if isinstance(node, NavigableString):
        if node.__class__.__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction"):
            return
        out.append(str(node))
        return
    if not isinstance(node, Tag) or node.name in SKIP:
        return
    if node.get("id"):
        out.append(MARK.format(node["id"]))
    if node.name == "br":
        out.append("\n")
        return
    block = node.name in BLOCK
    if block:
        out.append("\n")
    href = node.get("href") if node.name == "a" else None
    if links and href:
        out.append("[")
    for child in node.children:
        render(child, out, links)
    if links and href:
        out.append(f"]({href})")
    if block:
        out.append("\n")


def tidy(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    res, blank = [], 0
    for l in lines:
        if l:
            blank = 0
            res.append(l)
        else:
            blank += 1
            if blank == 1 and res:
                res.append("")
    return "\n".join(res).strip() + "\n"


def to_text(soup_or_tag, links=False):
    out = []
    render(soup_or_tag, out, True if links else False)
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="URL или путь к сохранённому HTML")
    ap.add_argument("--selector", help="CSS-селектор: взять только этот элемент")
    ap.add_argument("--from-id", help="id элемента, с которого начинается нужный раздел")
    ap.add_argument("--to-id", help="id элемента, на котором раздел заканчивается (не включается)")
    ap.add_argument("--links", action="store_true", help="сохранять ссылки как [текст](url)")
    ap.add_argument("--list-ids", action="store_true", help="показать id, найденные на странице")
    ap.add_argument("--out", help="записать в файл вместо stdout")
    args = ap.parse_args()

    soup = BeautifulSoup(load_html(args.source), "html.parser")

    if args.list_ids:
        for t in soup.find_all(id=True):
            label = t.get_text(" ", strip=True)[:60]
            print(f"{t['id']}\t<{t.name}>\t{label}")
        return 0

    root = soup
    if args.selector:
        root = soup.select_one(args.selector)
        if root is None:
            print(f"Селектор не найден: {args.selector}", file=sys.stderr)
            return 2

    raw = to_text(root, args.links)

    if args.from_id:
        start = MARK.format(args.from_id)
        i = raw.find(start)
        if i < 0:
            print(f"id не найден: {args.from_id} (попробуйте --list-ids)", file=sys.stderr)
            return 2
        raw = raw[i:]
        if args.to_id:
            j = raw.find(MARK.format(args.to_id), len(start))
            if j < 0:
                print(f"Внимание: id окончания не найден ({args.to_id}), выведено до конца страницы",
                      file=sys.stderr)
            else:
                raw = raw[:j]

    raw = re.sub(r"\x00ID:[^\x00]*\x00", "", raw)
    text = tidy(raw)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"OK: {args.out} ({len(text)} символов)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
