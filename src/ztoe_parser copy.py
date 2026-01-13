#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Parser for Zhytomyroblenergo (ZTOE) — version 4
# - правильний пошук <tr> для кожної підчерги
# - визначення відключень по RGB

import asyncio
import re
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright
import os

TZ = ZoneInfo("Europe/Kyiv")
URL = "https://www.ztoe.com.ua/unhooking-search.php"
OUTPUT_FILE = "out/Zhytomyroblenergo.json"

LOG_DIR = "logs"
FULL_LOG_FILE = os.path.join(LOG_DIR, "full_log.log")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("out", exist_ok=True)


def log(message: str):
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [ztoe_parser_v4] {message}"
    print(line)
    with open(FULL_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def fetch_html() -> str:
    """Отримує HTML сторінки ZTOE."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        page = await context.new_page()
        try:
            log(f"🌐 Opening {URL}")
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector("table", timeout=30000)

            await asyncio.sleep(2)

            html = await page.content()
            log(f"✅ HTML loaded ({len(html)} bytes)")
            return html
        finally:
            await browser.close()


def is_blackout_color(hex_color: str) -> bool:
    """
    Повертає True, якщо колір схожий на червоний blackout.
    hex_color: рядок без '#', наприклад 'ff3333'
    """
    c = hex_color.lower()
    if len(c) != 6:
        return False

    r = int(c[0:2], 16)
    g = int(c[2:4], 16)
    b = int(c[4:6], 16)

    # Червоний: R високий, G і B низькі
    return (r > 200 and g < 80 and b < 80)


def extract_tr_for_group(table_html: str, subgroup: str) -> str | None:
    """
    Повертає HTML одного <tr>, який містить підчергу (наприклад "1.1").
    Шукаємо серед усіх <tr> у таблиці.
    """
    # Витягуємо всі <tr>...</tr>
    rows = re.findall(r'<tr[^>]*>.*?</tr>', table_html, re.DOTALL | re.IGNORECASE)

    for row in rows:
        # Перевіряємо, чи є у цьому рядку текст підчерги
        # Можливі варіанти:
        #   >1.1<
        #   >1.1</b>
        if f">{subgroup}<" in row or f">{subgroup}</b>" in row:
            return row

    return None


def parse_table(html: str, date_str: str) -> dict:
    """Парсить таблицю для конкретної дати."""
    result: dict[str, dict[str, str]] = {}

    date_pattern = re.escape(date_str)

    # 1. Знаходимо таблицю по даті
    table_match = re.search(
        rf'<b[^>]*>{date_pattern}</b>.*?</table>',
        html,
        re.DOTALL | re.IGNORECASE
    )
    if not table_match:
        log(f"⚠️ No table found for {date_str}")
        return result

    table_html = table_match.group(0)

    # 2. Знаходимо всі підчерги:
    # <a ... pidcherga_id=1 ...><b>1.1</b></a>
    row_pattern = r'pidcherga_id=(\d+)[^>]*><b[^>]*>(\d+\.\d+)</b>'
    rows = re.findall(row_pattern, table_html)

    if not rows:
        log(f"⚠️ No subgroup rows found for {date_str}")
        return result

    for _pid, subgroup in rows:
        group_id = f"GPV{subgroup}"
        result[group_id] = {str(h): "yes" for h in range(1, 25)}

        # 3. Витягуємо конкретний <tr> для цієї підчерги
        tr_html = extract_tr_for_group(table_html, subgroup)
        if not tr_html:
            log(f"⚠️ {group_id}: <tr> not found")
            continue

        # 4. Витягуємо кольори 48 слотів
        cells = re.findall(
            r'background:\s*#([0-9a-fA-F]{6})',
            tr_html,
            re.IGNORECASE
        )

        if len(cells) < 48:
            log(f"⚠️ {group_id}: found {len(cells)} slots, expected 48")
            continue

        # Перетворюємо 48 півгодинних слотів на "yes"/"no"
        half: list[str] = []
        for c in cells:
            if is_blackout_color(c):
                half.append("no")
            else:
                half.append("yes")

        # 5. Перетворюємо 48 півгодин на 24 години
        for hour in range(1, 25):
            idx = (hour - 1) * 2
            a = half[idx]
            b = half[idx + 1]

            if a == "no" and b == "no":
                state = "no"
            elif a == "no":
                state = "first"
            elif b == "no":
                state = "second"
            else:
                state = "yes"

            result[group_id][str(hour)] = state

        log(f"✔️ {group_id}: parsed 48 slots")

    return result


def parse_schedule(html: str):
    """Парсинг графіка на сьогодні і завтра."""
    results: dict[str, dict] = {}

    today = datetime.now(TZ).date()
    tomorrow = today + timedelta(days=1)

    # Час оновлення
    update_match = re.search(
        r'Дата оновлення інформації[^0-9]*(\d{2}):(\d{2})\s*(\d{2})\.(\d{2})\.(\d{4})',
        html
    )
    if update_match:
        hh, mm, dd, mm2, yyyy = update_match.groups()
        update_info = f"{hh}:{mm} {dd}.{mm2}.{yyyy}"
        log(f"🕒 Update time: {update_info}")
    else:
        update_info = datetime.now(TZ).strftime("%H:%M %d.%m.%Y")
        log(f"⚠️ Update time not found, using current: {update_info}")

    # Обробляємо сьогодні + завтра
    for d in (today, tomorrow):
        date_str = d.strftime("%d.%m.%Y")
        ts = int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())

        log(f"📅 Processing {date_str}")
        table = parse_table(html, date_str)

        if table:
            results[str(ts)] = table
            log(f"✅ Added {len(table)} groups for {date_str}")
        else:
            log(f"⚠️ No schedule for {date_str}")

    return results, update_info


async def main():
    log("=" * 60)
    log("🚀 Starting ZTOE parser v4")
    log("=" * 60)

    try:
        html = await fetch_html()
        results, update_info = parse_schedule(html)

        if not results:
            log("❌ No schedules parsed — stopping")
            return False

        # DIFF — чи змінились дані?
        if os.path.exists(OUTPUT_FILE):
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_data = old.get("fact", {}).get("data", {})

            if json.dumps(old_data, sort_keys=True) == json.dumps(results, sort_keys=True):
                log("ℹ️ No changes detected → skipping write")
                return False

        # Сортуємо дати
        sorted_results = dict(sorted(results.items(), key=lambda x: int(x[0])))

        # today timestamp
        today = datetime.now(TZ).date()
        today_ts = int(datetime(today.year, today.month, today.day, tzinfo=TZ).timestamp())

        # Формуємо фінальний JSON
        new_json = {
            "regionId": "Zhytomyr",
            "lastUpdated": datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "fact": {
                "data": sorted_results,
                "update": update_info,
                "today": today_ts,
            },
            "preset": {
                "time_zone": {
                    str(i): [
                        f"{i - 1:02d}-{i:02d}",
                        f"{i - 1:02d}:00",
                        f"{i:02d}:00",
                    ]
                    for i in range(1, 25)
                },
                "time_type": {
                    "yes": "Світло є",
                    "maybe": "Можливе відключення",
                    "no": "Світла немає",
                    "first": "Світла не буде перші 30 хв.",
                    "second": "Світла не буде другі 30 хв.",
                },
            },
        }

        log(f"💾 Writing JSON → {OUTPUT_FILE}")
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(new_json, f, ensure_ascii=False, indent=2)

        log("✅ JSON updated successfully")
        log("=" * 60)
        return True

    except Exception as e:
        log(f"❌ ERROR: {e}")
        import traceback
        log(traceback.format_exc())
        return False


if __name__ == "__main__":
    asyncio.run(main())
