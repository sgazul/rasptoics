"""
Parsing of a lecturer’s schedule from SPbSEU (rasp.unecon.ru)
and generation of a .ics file for import into Google Calendar or any other calendar.

Examples:

python unecon_to_ics.py --prepod 7998 --week 29
python unecon_to_ics.py --url "https://rasp.unecon.ru/raspisanie_prepod.php?p=7998&w=29"
"""

import argparse
import re
import time
from datetime import datetime, date, time as dtime
from urllib.parse import urlparse, parse_qs

import pytz
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

BASE_URL = "https://rasp.unecon.ru"
TIMEZONE = pytz.timezone("Europe/Moscow")

# Common headers to make the request look like a regular browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# A single session for all requests.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch_schedule_html(prepod_id: int, week: int | None) -> str:
    """
    Loads the lecturer’s schedule HTML page.
    If week=None, the current week is used.
    """
    params = {"p": prepod_id}
    if week is not None:
        params["w"] = week

    url = f"{BASE_URL}/raspisanie_prepod.php"
    r = SESSION.get(url, params=params, timeout=15)
    if r.status_code == 403:
        raise RuntimeError(f"403 Forbidden when requesting page {r.url}")
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def fetch_schedule_ajax(page_html: str) -> str:
    """
    The page contains a JS variable:
    var filterUrl = 'raspisanie_filter_ajax.php?method=get_rasp_prepod&...';
    We extract it and request the HTML table.
    """
    m = re.search(r"var\s+filterUrl\s*=\s*'([^']+)'", page_html)
    if not m:
        raise RuntimeError("filterUrl not found in HTML. Has the site format changed?")
    ajax_path = m.group(1)
    url = f"{BASE_URL}/{ajax_path}"
    r = SESSION.get(url, timeout=15)
    if r.status_code == 403:
        raise RuntimeError(f"403 Forbidden when performing AJAX request {r.url}")
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def parse_lessons(ajax_html: str) -> list[dict]:
    """
    Parse the HTML fragment of the lecturer’s schedule.
    Return a list of dictionaries with the following fields:
    date (DD.MM.YYYY), day_of_week, time_start, time_end,
    subject, room, building, group, note.
    """
    soup = BeautifulSoup(f"<table>{ajax_html}</table>", "lxml")

    lessons: list[dict] = []
    current_date = None
    current_day = None

    for tr in soup.find_all("tr"):
        classes = tr.get("class", [])

        if "new_day_border" in classes:
            continue

        if "new_day" in classes:
            date_span = tr.find("span", class_="date")
            day_span = tr.find("span", class_="day")
            if date_span:
                current_date = date_span.get_text(strip=True)
            if day_span:
                current_day = day_span.get_text(strip=True)

        predmet_td = tr.find("td", class_="predmet")
        if not predmet_td:
            continue

        # Time
        time_text = None
        time_td = tr.find(
            "td",
            class_=lambda c: c and "time" in c and "no_480" in c,
        )
        if time_td:
            ts = time_td.find("span", class_="time")
            if ts:
                time_text = ts.get_text(strip=True)
        if not time_text:
            ts = predmet_td.find("span", class_="time")
            if ts:
                time_text = ts.get_text(strip=True)

        if not time_text:
            continue

        m = re.match(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", time_text)
        if not m:
            continue
        time_start, time_end = m.groups()

        # Subject
        subject_span = predmet_td.find("span", class_="predmet")
        subject = subject_span.get_text(strip=True) if subject_span else ""

        # Room and building
        room = ""
        building = ""
        aud_td = tr.find(
            "td",
            class_=lambda c: c and "aud" in c and "no_768" in c,
        )
        if aud_td:
            aud_span = aud_td.find("span", class_="aud")
            if aud_span:
                for a in aud_span.find_all("a"):
                    a.decompose()
                room = aud_span.get_text(strip=True)
            korpus_span = aud_td.find("span", class_="korpus")
            if korpus_span:
                building = korpus_span.get_text(strip=True)

        # Group
        group_span = predmet_td.find("span", class_="group")
        group = group_span.get_text(strip=True) if group_span else ""

        # Notes
        prim_span = predmet_td.find("span", class_="prim")
        note = prim_span.get_text(strip=True) if prim_span else ""

        if current_date and subject:
            lessons.append(
                {
                    "date": current_date,
                    "day_of_week": current_day or "",
                    "time_start": time_start,
                    "time_end": time_end,
                    "subject": subject,
                    "room": room,
                    "building": building,
                    "group": group,
                    "note": note,
                }
            )

    return lessons


def parse_url(url: str) -> tuple[int, int | None]:
    """
    Extract p and w parameters from a URL of the form:
    https://rasp.unecon.ru/raspisanie_prepod.php?p=7998&w=29
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    prepod = int(qs["p"][0])
    week = int(qs["w"][0]) if "w" in qs else None
    return prepod, week


def lesson_to_event(lesson: dict) -> Event:
    """
    Convert a class entry into an iCalendar Event.
    """
    date_obj = datetime.strptime(lesson["date"], "%d.%m.%Y").date()
    start_t = datetime.strptime(lesson["time_start"], "%H:%M").time()
    end_t = datetime.strptime(lesson["time_end"], "%H:%M").time()

    start_local = TIMEZONE.localize(datetime.combine(date_obj, start_t))
    end_local = TIMEZONE.localize(datetime.combine(date_obj, end_t))

    ev = Event()
    # Headers
    summary = lesson["subject"]
    if lesson["group"]:
        summary += f" | {lesson['group']}"
    ev.add("summary", summary)

    # Time
    ev.add("dtstart", start_local)
    ev.add("dtend", end_local)

    # Location
    loc_parts = []
    if lesson["room"]:
        loc_parts.append(lesson["room"])
    if lesson["building"]:
        loc_parts.append(lesson["building"])
    if loc_parts:
        ev.add("location", ", ".join(loc_parts))

    # Description
    desc_lines = []
    if lesson["group"]:
        desc_lines.append(f"Group: {lesson['group']}")
    if lesson["room"]:
        desc_lines.append(f"Room: {lesson['room']}")
    if lesson["building"]:
        desc_lines.append(f"Address: {lesson['building']}")
    if lesson["note"]:
        desc_lines.append(f"Note: {lesson['note']}")
    desc_lines.append("")
    desc_lines.append("Generated automatically from rasp.unecon.ru")
    ev.add("description", "\n".join(desc_lines))

    return ev


def main():
    parser = argparse.ArgumentParser(
        description="Export SPbSEU lecturer's timetable to a .ics file"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", type=str, help="Full URL of the lecturer's timetable")
    group.add_argument("--prepod", type=int, help="Lecturer ID (p=)")
    parser.add_argument(
        "--week",
        type=int,
        default=None,
        help="Week number (w=). Default is the current week.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="unecon_schedule.ics",
        help="Name of the output .ics file",
    )

    args = parser.parse_args()

    # Argument parsing
    if args.url:
        prepod_id, week = parse_url(args.url)
        if args.week is not None:
            week = args.week
    else:
        prepod_id = args.prepod
        week = args.week

    print(f"Loading schedule: prepod={prepod_id}, week={week or 'current'}")

    # A short pause in case you are running multiple executions in a row
    time.sleep(0.5)

    page_html = fetch_schedule_html(prepod_id, week)
    ajax_html = fetch_schedule_ajax(page_html)
    lessons = parse_lessons(ajax_html)

    if not lessons:
        print("No classes found (the lecturer might not have any this week).")
        return

    print(f"Classes found: {len(lessons)}")

    cal = Calendar()
    cal.add("prodid", "-//Unecon Schedule Export//example//RU")
    cal.add("version", "2.0")

    for les in lessons:
        ev = lesson_to_event(les)
        cal.add_component(ev)

    with open(args.output, "wb") as f:
        f.write(cal.to_ical())

    print(f"Done. File saved: {args.output}")


if __name__ == "__main__":
    main()