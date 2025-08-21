[Adapted from https://github.com/aleenprd/boligportal-scraper](https://github.com/kakn/boligportal-scraper)

# BoligPortal Scraper

Scrapes apartment listings from BoligPortal.dk, stores them in a JSON file, and sends Telegram alerts for new listings.

## Features

- Headless scraping via Selenium and ChromeDriver
- Parses number of rooms, square meters, and price
- Applies custom filters
- Sends Telegram notifications
- Saves and updates listings in a local JSON file
- Can be deployed on an Azure Linux VM

## Setup

1. Clone the repo:
```
git clone https://github.com/kakn/boligportal-scraper.git
cd boligportal-scraper
```
2. Create a virtual environment and install dependencies:
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
3. Add an .env file in the root directory (see .env.sample)
4. Run the scraper:
```
python main.py
```
