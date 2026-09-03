import json
import re
import random
import time
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

from models import AuctionItem


BASE_URL = "https://auctions.yahoo.co.jp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
}


class RateLimitError(RuntimeError):
    pass


def build_search_url(query, category=""):
    category_path = str(category or "0").strip() or "0"
    return f"{BASE_URL}/search/search/{quote(str(query).strip(), safe='')}/{category_path}/?s1=new&o1=d"


def fetch(url, timeout=20, retries=2, backoff_base_seconds=5):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout, impersonate="chrome")
            if response.status_code in {403, 429}:
                raise RateLimitError(f"Yahoo!オークションからHTTP {response.status_code}")
            response.raise_for_status()
            return response.text
        except RateLimitError:
            raise
        except Exception as error:
            last_error = error
            if attempt >= retries:
                break
            delay = max(0, backoff_base_seconds) * (2 ** attempt) + random.uniform(0, 1)
            print(f"YAHOO_HTTP_RETRY: attempt={attempt + 1}/{retries} wait={delay:.1f}s")
            time.sleep(delay)
    raise RuntimeError(f"取得失敗: {url}: {last_error}")


def extract_item_id(url):
    path = urlparse(str(url or "")).path
    match = re.search(r"/(?:auction|item)/([A-Za-z0-9_-]+)", path)
    return match.group(1) if match else ""


def parse_price(text):
    patterns = [
        r"(?:現在|即決)?\s*(?:価格)?\s*[¥￥]?\s*([0-9][0-9,]*)\s*円",
        r"[¥￥]\s*([0-9][0-9,]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""))
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def parse_search_results(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    for anchor in soup.select("a[href*='/auction/'], a[href*='/item/']"):
        href = anchor.get("href", "")
        url = urljoin(BASE_URL, href)
        item_id = extract_item_id(url)
        if not item_id or item_id in seen:
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if len(title) < 2:
            title = str(anchor.get("title", "") or anchor.get("aria-label", "")).strip()
        if len(title) < 2:
            continue
        block = anchor
        for _ in range(5):
            if block.parent is None:
                break
            block = block.parent
            classes = " ".join(block.get("class", [])) if hasattr(block, "get") else ""
            if block.name in {"li", "article"} or re.search(r"Product|Item", classes, re.IGNORECASE):
                break
        surrounding = " ".join(block.get_text(" ", strip=True).split())
        seller_match = re.search(r"(?:出品者|ストア)[:：\s]+([^\s]{2,50})", surrounding)
        condition_match = re.search(
            r"(?:商品の状態|状態)[:：\s]+([^|｜]{1,40}?)(?:\s{2,}|送料|残り|入札|$)", surrounding
        )
        results.append(AuctionItem(
            item_id=item_id,
            title=title,
            price=parse_price(surrounding),
            url=url.split("?", 1)[0],
            seller=seller_match.group(1) if seller_match else "",
            store_condition=condition_match.group(1).strip() if condition_match else "",
            description=surrounding,
        ))
        seen.add(item_id)
    return results


def search(query, category="", timeout=20, retries=2, backoff_base_seconds=5):
    return parse_search_results(fetch(build_search_url(query, category), timeout, retries, backoff_base_seconds))


def parse_detail_status(html):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.string or script.get_text() or "")
        except (TypeError, ValueError):
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            offers = entry.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            availability = str(offers.get("availability", "")).lower()
            if availability.endswith("instock"):
                return "active", "JSON-LD: InStock"
            if availability.endswith(("soldout", "outofstock")):
                return "sold", f"JSON-LD: {availability.rsplit('/', 1)[-1]}"
    text = " ".join(soup.get_text(" ", strip=True).split())
    sold_terms = ("落札されました", "落札済み", "購入されました", "この商品は落札されました")
    if any(term in text for term in sold_terms):
        return "sold", "落札済み表記を検出"
    if any(term in text for term in ("オークションは終了しました", "終了したオークション")):
        return "ended", "終了表記のみ（落札有無不明）"
    if any(term in text for term in ("入札する", "今すぐ落札", "残り時間")):
        return "active", "出品中表記を検出"
    return "unknown", "状態表記を取得できず"


def check_status(url, timeout=20, retries=0):
    return parse_detail_status(fetch(url, timeout, retries))
