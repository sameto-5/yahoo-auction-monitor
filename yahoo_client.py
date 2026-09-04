import json
import re
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlparse

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
    params = {
        "p": str(query).strip(),
        "s1": "new",
        "o1": "d",
    }
    if category_path != "0":
        params["auccat"] = category_path
    return f"{BASE_URL}/search/search?{urlencode(params)}"


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
        # タイトル内の「1円〜」より「現在 10,451円」を優先する。
        r"現在(?:価格)?[:：\s]*[¥￥]?\s*([0-9][0-9,]*)\s*円",
        r"即決(?:価格)?[:：\s]*[¥￥]?\s*([0-9][0-9,]*)\s*円",
        r"価格[:：\s]*[¥￥]?\s*([0-9][0-9,]*)\s*円",
        r"[¥￥]\s*([0-9][0-9,]*)",
        r"([0-9][0-9,]*)\s*円",
    ]
    for pattern in patterns:
        match = re.search(pattern, str(text or ""))
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def parse_listing_info(text, now=None):
    """検索カード/詳細の表示文から、取得できる範囲の出品情報を返す。
    不明時は誤通知を避けるため unknown とする。
    """
    value = " ".join(str(text or "").split())
    buy_match = re.search(r"即決(?:価格)?[:：\s]*[¥￥]?\s*([0-9][0-9,]*)\s*円", value)
    shipping_match = re.search(r"送料[:：\s]*[¥￥]?\s*([0-9][0-9,]*)\s*円", value)
    free_shipping = any(term in value for term in ("送料無料", "送料0円"))
    auction_markers = ("入札", "現在価格", "残り時間", "オークション")
    fixed_markers = ("定額", "今すぐ落札", "購入手続きへ")
    listing_type = "fixed" if any(x in value for x in fixed_markers) else "auction" if any(x in value for x in auction_markers) else "unknown"
    if buy_match and listing_type == "unknown":
        listing_type = "auction"
    current = now or datetime.now(timezone(timedelta(hours=9)))
    end_at = ""
    end_match = re.search(r"(?:終了(?:日時)?|[~〜]終了)[:：\s]*(?:(\d{4})年)?\s*(\d{1,2})月(\d{1,2})日\s*(\d{1,2})(?:時|:)(\d{1,2})分?", value)
    if end_match:
        year = int(end_match.group(1) or current.year)
        parsed = datetime(year, *map(int, end_match.groups()[1:]), tzinfo=current.tzinfo)
        if not end_match.group(1) and parsed < current - timedelta(days=2):
            parsed = parsed.replace(year=year + 1)
        end_at = parsed.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "listing_type": listing_type,
        "buy_now_price": int(buy_match.group(1).replace(",", "")) if buy_match else None,
        "shipping_fee": 0 if free_shipping else int(shipping_match.group(1).replace(",", "")) if shipping_match else None,
        "end_at": end_at,
    }


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
        block = anchor.find_parent(["li", "article"]) or anchor
        surrounding = " ".join(block.get_text(" ", strip=True).split())
        listing = parse_listing_info(surrounding)
        metadata = block.select_one("[data-auction-endtime]") if hasattr(block, "select_one") else None
        if metadata:
            end_epoch = str(metadata.get("data-auction-endtime", "") or "")
            if end_epoch.isdigit():
                listing["end_at"] = datetime.fromtimestamp(
                    int(end_epoch), timezone(timedelta(hours=9))
                ).strftime("%Y-%m-%d %H:%M:%S")
            # 画面表示の「即決」価格を優先し、data属性は表示がない時のみ使う。
            raw_buy_now = str(metadata.get("data-auction-buynowprice", "") or "")
            if listing["buy_now_price"] is None and raw_buy_now.isdigit() and int(raw_buy_now) > 0:
                listing["buy_now_price"] = int(raw_buy_now)
            if listing["buy_now_price"] is not None:
                listing["listing_type"] = "auction"
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
            listing_type=listing["listing_type"], buy_now_price=listing["buy_now_price"],
            shipping_fee=listing["shipping_fee"], end_at=listing["end_at"],
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
    if any(term in text for term in ("出品が取り消されました", "出品取消", "このオークションは取り消されました")):
        return "cancelled", "出品取消表記を検出"
    if any(term in text for term in ("入札する", "今すぐ落札", "残り時間")):
        return "active", "出品中表記を検出"
    return "unknown", "状態表記を取得できず"


def check_status(url, timeout=20, retries=0):
    return parse_detail_status(fetch(url, timeout, retries))


def get_detail(url, timeout=20, retries=0, backoff_base_seconds=5):
    html = fetch(url, timeout, retries, backoff_base_seconds)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    info = parse_listing_info(text)
    info["current_price"] = parse_price(text)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (TypeError, ValueError):
            continue
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            offers = entry.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if not isinstance(offers, dict):
                continue
            raw_price = str(offers.get("price", "") or "").replace(",", "")
            if raw_price.isdigit():
                info["current_price"] = int(raw_price)
            valid_until = str(offers.get("priceValidUntil", "") or "")
            if valid_until:
                try:
                    info["end_at"] = datetime.fromisoformat(valid_until).astimezone(
                        timezone(timedelta(hours=9))
                    ).strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    pass
            shipping = offers.get("shippingDetails") or {}
            rate = shipping.get("shippingRate") if isinstance(shipping, dict) else {}
            raw_shipping = str((rate or {}).get("value", "") or "").replace(",", "") if isinstance(rate, dict) else ""
            if raw_shipping.isdigit():
                info["shipping_fee"] = int(raw_shipping)
    win_match = re.search(r'"winPrice"\s*:\s*"?([0-9,]+)', html)
    if win_match and int(win_match.group(1).replace(",", "")) > 0:
        info["buy_now_price"] = int(win_match.group(1).replace(",", ""))
    bids_match = re.search(r'"bids"\s*:\s*"?([0-9]+)', html)
    if bids_match:
        info["listing_type"] = "auction"
    info["status"], info["status_reason"] = parse_detail_status(html)
    return info
