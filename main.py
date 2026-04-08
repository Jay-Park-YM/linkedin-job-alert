"""
LinkedIn L'Oréal South Korea Job Alert → Slack
"""

import json
import os
import re
import logging
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEARCH_BASE_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?keywords=l%27oreal&location=South%20Korea&position=1&pageNum=0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

SEEN_JOBS_PATH = Path(__file__).parent / "seen_jobs.json"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def load_seen_jobs() -> set:
    if SEEN_JOBS_PATH.exists():
        data = json.loads(SEEN_JOBS_PATH.read_text())
        return set(data)
    return set()


def save_seen_jobs(seen: set):
    # Keep only last 500 to prevent file from growing forever
    recent = sorted(seen)[-500:]
    SEEN_JOBS_PATH.write_text(json.dumps(recent, ensure_ascii=False, indent=2))


def parse_cards(html: str) -> list[dict]:
    """Parse job cards from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for card in soup.select("div.base-card"):
        job_id = card.get("data-entity-urn", "")
        match = re.search(r"(\d+)", job_id)
        if not match:
            continue

        jid = match.group(1)

        title_el = card.select_one("h3.base-search-card__title")
        company_el = card.select_one("h4.base-search-card__subtitle a")
        location_el = card.select_one("span.job-search-card__location")
        link_el = card.select_one("a.base-card__full-link")
        date_el = card.select_one("time")

        title = title_el.get_text(strip=True) if title_el else "N/A"
        company = company_el.get_text(strip=True) if company_el else "N/A"
        location = location_el.get_text(strip=True) if location_el else "N/A"
        link = link_el["href"].split("?")[0] if link_el else ""
        posted = date_el.get_text(strip=True) if date_el else ""

        jobs.append({
            "id": jid,
            "title": title,
            "company": company,
            "location": location,
            "link": link,
            "posted": posted,
        })

    return jobs


def fetch_jobs() -> list[dict]:
    """Fetch all public LinkedIn job listings with pagination."""
    log.info("Fetching LinkedIn jobs...")
    all_jobs = []
    seen_ids = set()
    start = 0

    while True:
        url = f"{SEARCH_BASE_URL}&start={start}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()

        page_jobs = parse_cards(resp.text)
        if not page_jobs:
            break

        new_on_page = 0
        for job in page_jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                all_jobs.append(job)
                new_on_page += 1

        log.info("Page start=%d: %d job(s)", start, new_on_page)

        if new_on_page == 0:
            break

        start += 25

    log.info("Found %d job(s) total", len(all_jobs))
    return all_jobs


def filter_new_jobs(jobs: list[dict], seen: set) -> list[dict]:
    return [j for j in jobs if j["id"] not in seen]


def send_slack_notification(new_jobs: list[dict]):
    if not SLACK_WEBHOOK_URL:
        log.error("SLACK_WEBHOOK_URL not set")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"L'Oréal Korea 새 채용공고 ({today})",
            },
        },
        {"type": "divider"},
    ]

    for job in new_jobs:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*<{job['link']}|{job['title']}>*\n"
                    f"Company: {job['company']}\n"
                    f"Location: {job['location']}\n"
                    f"Posted: {job['posted']}"
                ),
            },
        })

    payload = {"blocks": blocks}
    resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
    log.info("Slack notification sent for %d job(s)", len(new_jobs))


def main():
    seen = load_seen_jobs()
    jobs = fetch_jobs()

    if not jobs:
        log.info("No jobs found in search results")
        return

    new_jobs = filter_new_jobs(jobs, seen)

    if not new_jobs:
        log.info("No new jobs since last check")
        return

    log.info("Found %d new job(s)", len(new_jobs))
    send_slack_notification(new_jobs)

    # Update seen jobs
    for j in new_jobs:
        seen.add(j["id"])
    save_seen_jobs(seen)


if __name__ == "__main__":
    main()
