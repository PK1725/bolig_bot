import json
import os
import re
import time
import random
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

print("Current working directory:", os.getcwd())
load_dotenv()

class BoligPortalScraper:
    """Lightweight scraper for BoligPortal (no browser), storing listings in JSON and sending Telegram alerts."""

    LISTINGS_FILE = "listings.json"
    NUM_PAGES = 3
    CYCLE_DELAY = 120

    BASE_URL = "https://www.boligportal.dk"

    def __init__(self):
        # Keep envs compatible with your existing setup
        self.webdriver_path = os.environ.get("WEBDRIVER_PATH", "")  # unused now
        self.bot_token = self._load_env_var("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = self._load_env_var("TELEGRAM_CHAT_ID", "")
        self.areas_json = self._load_env_var("BOLIG_PORTAL_AREAS_JSON", "{}")

        self.max_price = int(self._load_env_var("MAX_PRICE", "999999"))
        self.min_rooms = float(self._load_env_var("MIN_ROOMS", "1"))
        self.min_sqm = int(self._load_env_var("MIN_SQM", "0"))
        self.min_period = int(self._load_env_var("MIN_PERIOD", "0"))

        # Parse areas
        self.areas = json.loads(self.areas_json)

        # Initialize listings store and HTTP session
        self.listings = self._load_listings()
        self.session = self._init_session()

        print(f"Telegram bot token present: {bool(self.bot_token)}")
        print(f"Telegram chat ID: {self.chat_id}")
        print(f"Areas: {self.areas}")
        print(f"Max price: {self.max_price}")
        print(f"Min rooms: {self.min_rooms}")
        print(f"Min sqm: {self.min_sqm}")

    def _load_env_var(self, key: str, default=None) -> str:
        val = os.environ.get(key, default)
        if val is None:
            raise ValueError(f"Missing required environment variable: {key}")
        return val

    def _init_session(self) -> requests.Session:
        s = requests.Session()
        # Realistic desktop headers help avoid trivial blocks
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
        })
        s.timeout = 15  # default per-request timeout via kwarg, see _fetch
        return s

    def _load_listings(self) -> Dict[str, Any]:
        if not os.path.isfile(self.LISTINGS_FILE):
            return {}
        try:
            with open(self.LISTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_listings(self) -> None:
        tmp = self.LISTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.listings, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.LISTINGS_FILE)

    def _send_telegram_notification(self, message: str) -> None:
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials missing; skipping notification.")
            return
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "disable_web_page_preview": True}
            resp = requests.post(url, data=payload, timeout=10)
            if resp.status_code != 200:
                print(f"Telegram notification failed. Status={resp.status_code}, msg={resp.text[:300]}")
        except Exception as e:
            print(f"Telegram notification error: {e}")

    # ---------------------------
    # HTML fetching (no browser)
    # ---------------------------
    def _fetch(self, url: str, retries: int = 3, backoff_base: float = 0.8) -> Optional[str]:
        """Fetch a URL with basic retries/backoff. Returns text or None."""
        last_err = None
        for i in range(retries + 1):
            try:
                resp = self.session.get(url, timeout=15)
                # If site uses basic anti-bot returning 403/429, backoff and retry
                if resp.status_code in (429, 403, 502, 503, 520, 521, 522, 523, 524):
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                # Heuristic: reject suspiciously tiny pages
                if len(resp.text) < 500 and i < retries:
                    raise ValueError("Suspiciously small response, retrying…")
                return resp.text
            except Exception as e:
                last_err = e
                sleep = backoff_base * (2 ** i) + random.uniform(0, 0.4)
                print(f"Fetch fail ({i+1}/{retries+1}) {url}: {e} -> sleeping {sleep:.1f}s")
                time.sleep(sleep)
        print(f"Giving up on {url}: {last_err}")
        return None

    def _scrape_page(self, url: str) -> Optional[BeautifulSoup]:
        html = self._fetch(url)
        if html is None:
            return None
        return BeautifulSoup(html, "html.parser")

    # ---------------------------
    # Parsing helpers
    # ---------------------------
    def _parse_rooms(self, text: str) -> float:
        match = re.search(r"(\d+(?:,\d+)?)", text.replace(" ", ""))
        if not match:
            return 0.0
        return float(match.group(1).replace(",", "."))

    def _parse_sqm(self, text: str) -> int:
        matches = re.findall(r"(\d+(?:[.,]\d+)?)", text.replace(" ", ""))
        if not matches:
            return 0
        sqm_str = matches[-1].replace(",", ".")
        return round(float(sqm_str))

    def _parse_price(self, text: str) -> int:
        digits = re.sub(r"\D", "", text)
        return int(digits) if digits.isdigit() else 0

    def _parse_period(self, text: str) -> int:
        default_value = 99999
        if text == "Ubegrænset":
            return default_value
        elif text == "24+ måneder":
            return 24
        elif text == "12-23 måneder":
            return 12
        elif text == "1-11 måneder":
            return 1
        elif text == "":
            return default_value
        else:
            return default_value

    def _meets_initial_criteria(self, rooms: float, sqm: int, price: int) -> bool:
        return (rooms >= self.min_rooms and sqm >= self.min_sqm and price <= self.max_price)

    def _meets_in_depth_criteria(self, period: int) -> bool:
        return period >= self.min_period

    # ---------------------------
    # Scraping logic
    # ---------------------------
    def _scrape_area(self, area_name: str, base_url: str) -> None:
        found_urls = []

        for page_idx in range(self.NUM_PAGES):
            page_url = base_url
            if page_idx > 0:
                page_url += f"?offset={18 * page_idx}"

            soup = self._scrape_page(page_url)
            
            if soup is None:
                print(f"Page load failed, skipping page: {page_url}")
                continue
            
            # Remove the "Lignende annoncer" (Similar ads) section before parsing
            similar_ads_section = soup.find("h2", string=lambda text: text and "Lignende annoncer" in text)
            if similar_ads_section:
                # Find the parent container and remove it
                parent_container = similar_ads_section.find_parent("div", class_="css-in2ycb")
                if parent_container:
                    parent_container.decompose()


            #cards = soup.find_all("a", {"class": ["AdCardSrp__Link", "css-17x8ssx"]})
            cards = soup.find_all("a", class_=lambda x: x and "AdCardSrp__Link" in x)
            if page_idx == 0 and not cards:
                # If first page has no cards, likely blocked or structure changed
                print(f"No cards found on first page for {area_name}.")
                break

            for card in cards:
                apt_href = card.get("href", "")
                if not apt_href:
                    continue

                if apt_href.startswith("/"):
                    apt_url = self.BASE_URL + apt_href
                elif apt_href.startswith("http"):
                    apt_url = apt_href
                else:
                    apt_url = self.BASE_URL + "/" + apt_href.lstrip("/")

                found_urls.append(apt_url)
                if apt_url in self.listings:
                    continue
                    
                title_el = card.select_one(".css-a76tvl")
                location_el = card.select_one(".css-avmlqd")
                price_el = card.select_one(".css-dlcfcd")

                location_txt = location_el.text.strip() if location_el else ""
                title_txt = title_el.text.strip() if title_el else ""
                desc_txt = title_txt  # reuse title for description parsing
                price_txt = price_el.text.strip() if price_el else ""

                rooms_val = self._parse_rooms(desc_txt)
                sqm_val = self._parse_sqm(desc_txt)
                price_val = self._parse_price(price_txt)


                if not self._meets_initial_criteria(rooms_val, sqm_val, price_val):
                    continue

                # Detail page (still plain requests)
                detail_soup = self._scrape_page(apt_url)
                if detail_soup is None:
                    print(f"Detail page failed, skipping: {apt_url}")
                    continue

                # Extract period (SoupSieve supports :has / :-soup-contains in bs4>=4.7)
                period_el = detail_soup.select_one(
                    ".css-x1kcbm:has(.css-1y5f71p:-soup-contains('Lejeperiode')) .css-14bctuo"
                )
                period_txt = period_el.text.strip() if period_el else ""
                period_val = self._parse_period(period_txt)

                if not self._meets_in_depth_criteria(period_val):
                    print(f"Skipping due to period filter: {apt_url} (Period: {period_txt})")
                    continue

                time_el = detail_soup.select_one(".css-v49nss")
                timestamp_str = time_el.text.strip() if time_el else ""

                self.listings[apt_url] = {
                    "area": area_name,
                    "title": title_txt,
                    "location": location_txt,
                    "description": desc_txt,
                    "price": price_txt,
                    "rooms": rooms_val,
                    "sqm": sqm_val,
                    "timestamp": timestamp_str,
                }

                msg = (
                    f"New apartment in {area_name}!\n"
                    f"Rooms: {rooms_val}, Size: {sqm_val} m², Price: {price_txt}\n"
                    f"{apt_url}"
                )
                self._send_telegram_notification(msg)

            # Be gentle to the server and reduce block risk
            jitter = 0.8 + random.random() * 0.8
            time.sleep(jitter)

        # Remove stale listings for this area
        for known_url in list(self.listings.keys()):
            if self.listings[known_url].get("area") == area_name and known_url not in found_urls:
                del self.listings[known_url]

    def run(self):
        if not self.areas:
            print("No areas configured (BOLIG_PORTAL_AREAS_JSON is empty). Exiting.")
            return

        print("Starting BoligPortal scraper (no browser)... Press Ctrl+C to stop.")
        try:
            while True:
                for area_name, area_url in self.areas.items():
                    print(f"Scraping area: {area_name} ...")
                    self._scrape_area(area_name, area_url)

                self._save_listings()
                sleep_variation = int(self.CYCLE_DELAY * 0.5)
                sleep_time = self.CYCLE_DELAY + random.randint(-sleep_variation, sleep_variation)
                print(f"Scrape cycle complete. Sleeping {sleep_time} seconds...")
                time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("Keyboard interrupt received. Stopping scraper.")
        finally:
            self._save_listings()

def main():
    scraper = BoligPortalScraper()
    scraper.run()

if __name__ == "__main__":
    main()
