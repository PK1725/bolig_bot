import json
import os
import re
import time
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

load_dotenv()

class BoligPortalScraper:
    """Scraper for BoligPortal, storing listings in JSON and sending Telegram alerts."""

    LISTINGS_FILE = "listings.json"
    NUM_PAGES = 3
    CYCLE_DELAY = 60

    def __init__(self):
        self.webdriver_path = self._load_env_var("WEBDRIVER_PATH")
        self.bot_token = self._load_env_var("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = self._load_env_var("TELEGRAM_CHAT_ID", "")
        self.areas_json = self._load_env_var("BOLIG_PORTAL_AREAS_JSON", "{}")

        self.max_price = int(self._load_env_var("MAX_PRICE", "999999"))
        self.min_rooms = float(self._load_env_var("MIN_ROOMS", "1"))
        self.min_sqm = int(self._load_env_var("MIN_SQM", "0"))
        self.min_period = int(self._load_env_var("MIN_PERIOD", "0"))

        # Parse the JSON for areas
        self.areas = json.loads(self.areas_json)

        # Initialize listings store and Selenium driver
        self.listings = self._load_listings()
        self.driver = self._init_webdriver()

        print(f"Webdriver path: {self.webdriver_path}")
        print(f"Telegram bot token: {self.bot_token}")
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

    def _init_webdriver(self) -> webdriver.Chrome:
        chrome_opts = Options()
        chrome_opts.add_argument("--headless")
        return webdriver.Chrome(service=Service(self.webdriver_path), options=chrome_opts)

    def _load_listings(self) -> Dict[str, Any]:
        if not os.path.isfile(self.LISTINGS_FILE):
            return {}
        try:
            with open(self.LISTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_listings(self) -> None:
        with open(self.LISTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.listings, f, indent=2, ensure_ascii=False)

    def _send_telegram_notification(self, message: str) -> None:
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials missing; skipping notification.")
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message, "disable_web_page_preview": True}
        resp = requests.post(url, data=payload, timeout=10)
        if resp.status_code != 200:
            print(f"Telegram notification failed. Status={resp.status_code}, msg={resp.text}")

    def _scrape_page(self, url: str) -> BeautifulSoup:
        self.driver.get(url)
        time.sleep(1)
        return BeautifulSoup(self.driver.page_source, "html.parser")

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
    
    def _parse_period(self, text: str) -> str:
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
            #print(f"Unknown period format: {text}, defaulting to {str(default_value)}")
            return default_value

    def _meets_initial_criteria(self, rooms: float, sqm: int, price: int) -> bool:
        return (
            rooms >= self.min_rooms
            and sqm >= self.min_sqm
            and price <= self.max_price
        )
    def _meets_in_depth_criteria(self, period: int) -> bool:
        return (
            period >= self.min_period
        )

    def _scrape_area(self, area_name: str, base_url: str) -> None:
        found_urls = []
        for page_idx in range(self.NUM_PAGES):
            page_url = base_url
            if page_idx > 0:
                page_url += f"?offset={18 * page_idx}"

            soup = self._scrape_page(page_url)
            cards = soup.find_all("a", {"class": ["AdCardSrp__Link", "css-17x8ssx"]})

            if page_idx == 0 and not cards:
                break

            for card in cards:
                #print(card.prettify())

                apt_href = card.get("href", "")
                if not apt_href:
                    continue

                apt_url = "https://www.boligportal.dk" + apt_href
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
                

                # print(f"Found: {title_txt} | Rooms: {rooms_val} | Size: {sqm_val} m² | Price: {price_val} | URL: {apt_url}")

                if not self._meets_initial_criteria(rooms_val, sqm_val, price_val):
                    #print(f"Skipping due to filters: {apt_url}")
                    continue

                detail_soup = self._scrape_page(apt_url)

                # Extract period from detail page
                period_el = detail_soup.select_one(".css-x1kcbm:has(.css-1y5f71p:-soup-contains('Lejeperiode')) .css-14bctuo")
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
                self._send_telegram_notification(msg) # TODO Uncomment to enable notifications

        # Remove stale listings
        for known_url in list(self.listings.keys()):
            if self.listings[known_url].get("area") == area_name and known_url not in found_urls:
                del self.listings[known_url]

    def run(self):
        if not self.areas:
            print("No areas configured (BOLIG_PORTAL_AREAS_JSON is empty). Exiting.")
            return

        print("Starting BoligPortal scraper... Press Ctrl+C to stop.")
        try:
            while True:
                for area_name, area_url in self.areas.items():
                    print(f"Scraping area: {area_name} ...")
                    self._scrape_area(area_name, area_url)

                self._save_listings()
                print(f"Scrape cycle complete. Sleeping {self.CYCLE_DELAY} seconds...")
                time.sleep(self.CYCLE_DELAY)

        except KeyboardInterrupt:
            print("Keyboard interrupt received. Stopping scraper.")
        finally:
            self.driver.quit()
            self._save_listings()

def main():
    scraper = BoligPortalScraper()
    scraper.run()

if __name__ == "__main__":
    main()