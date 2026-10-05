#!/usr/bin/env python3
"""Проверка окружения проекта «Проповедь по Апостолу». Запуск из корня проекта:

    python3 check_setup.py        (на Windows: python check_setup.py)

Проверяет: версию Python, зависимости, файлы данных, поиск перевода, сборку EPUB,
строку поиска для Радио Вера и доступность сайтов (необязательно, по сети).
"""
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SK = ROOT / ".claude" / "skills"
ok_all = True


def report(ok, text, hint=""):
    global ok_all
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {text}" + (f"\n       → {hint}" if (hint and not ok) else ""))
    ok_all = ok_all and ok


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", cwd=ROOT)


# 1. Python
report(sys.version_info >= (3, 9), f"Python {sys.version.split()[0]}", "нужен Python 3.9 или новее")

# 2. зависимости
for mod, pkg in (("requests", "requests"), ("bs4", "beautifulsoup4")):
    try:
        __import__(mod)
        report(True, f"пакет {pkg}")
    except ImportError:
        report(False, f"пакет {pkg}", "pip install -r requirements.txt")

# 3. файлы данных
for name in ("index.json", "apostol.md"):
    f = ROOT / "data" / name
    report(f.exists() and f.stat().st_size > 100_000, f"data/{name}", "файл отсутствует или повреждён")
try:
    idx = json.loads((ROOT / "data" / "index.json").read_text(encoding="utf-8"))
    report(True, f"index.json читается ({len(idx) if hasattr(idx, '__len__') else '?'} записей верхнего уровня)")
except Exception as e:  # noqa: BLE001
    report(False, "index.json читается", str(e))

# 4. навыки
for s in ("apostol-zachalo", "apostol-chtenie-format", "radiovera-apostol", "propoved-workflow"):
    report((SK / s / "SKILL.md").exists(), f"навык {s}", f"нет .claude/skills/{s}/SKILL.md")

# 5. поиск перевода
r = run(SK / "apostol-chtenie-format" / "scripts" / "find_translation.py", "Гал.5:22-6:2")
report(r.returncode == 0 and "[СИН]" in r.stdout, "поиск перевода зачала (Гал.5:22-6:2)", r.stderr.strip()[:200])

# 6. сборка EPUB
sample = ROOT / "examples" / "sample_kindle.md"
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "t.epub"
    r = run(SK / "propoved-workflow" / "scripts" / "build_kindle_doc.py", sample, "--out", out)
    good = r.returncode == 0 and out.exists() and zipfile.is_zipfile(out)
    report(good, "сборка EPUB из examples/sample_kindle.md", r.stderr.strip()[:200])

# 7. строка поиска для Радио Вера (без сети)
r = run(SK / "radiovera-apostol" / "scripts" / "radiovera_search.py", "Гал. 5:22–6:2, зач. 213", "--dry-run")
report(r.returncode == 0 and "213 зач." in r.stdout, "радио Вера: построение строки поиска (--dry-run)", r.stderr.strip()[:200])

# 8. сеть (необязательно)
print("\nПроверка сети (необязательная):")
try:
    import requests
    for url in ("https://azbyka.ru", "https://radiovera.ru", "https://posledovania.ru"):
        try:
            code = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}).status_code
            print(f"  {url}: HTTP {code}")
        except requests.RequestException as e:
            print(f"  {url}: недоступен ({type(e).__name__})")
except ImportError:
    print("  пропущено (нет requests)")

print("\n" + ("Всё готово к работе." if ok_all else "Есть проблемы: исправьте пункты FAIL выше."))
sys.exit(0 if ok_all else 1)
