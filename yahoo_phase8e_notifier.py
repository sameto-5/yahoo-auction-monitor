from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from curl_cffi import requests


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    status_code: int | None = None
    retry_after_seconds: int | None = None
    error: str = ""


def _retry_after(response):
    value = response.headers.get("Retry-After") if getattr(response, "headers", None) else None
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return None


def send_discord(webhook_url, message, timeout=20):
    """Send to the Yahoo-only webhook. Never logs URL, headers, or payload."""
    if not webhook_url:
        return DeliveryResult(False, error="WEBHOOK_NOT_CONFIGURED")
    try:
        response = requests.post(webhook_url, json={"content": message}, timeout=timeout)
    except Exception as error:
        return DeliveryResult(False, error=type(error).__name__)
    if 200 <= response.status_code < 300:
        return DeliveryResult(True, status_code=response.status_code)
    return DeliveryResult(
        False,
        status_code=response.status_code,
        retry_after_seconds=_retry_after(response) if response.status_code == 429 else None,
        error=f"HTTP_{response.status_code}",
    )


def format_notification(record, stage, model_stat, now):
    def yen(value):
        return f"{int(float(value)):,}円"

    def text(value, fallback="不明"):
        return str(value) if value not in (None, "") else fallback

    end_at = record.get("end_at")
    if isinstance(end_at, datetime):
        end_at = end_at.isoformat()
    confidence = float(model_stat.get("confidence") or 0)
    sample_count = int(float(model_stat.get("sample_count") or 0))
    margin = float(record.get("estimated_margin_rate") or 0) * 100
    return "\n".join([
        "【Yahoo仕入れ候補】",
        f"判定：{stage}",
        f"商品名：{text(record.get('title'))}",
        f"型番：{text(record.get('normalized_model'))}",
        f"状態：{text(record.get('condition_class'))}",
        f"現在価格：{yen(record.get('current_price'))}",
        f"購入送料：{yen(record.get('buy_shipping'))}",
        f"総仕入額：{yen(record.get('estimated_total_cost'))}",
        f"想定販売価格：{yen(record.get('expected_sale_price'))}",
        f"最大入札価格：{yen(record.get('effective_purchase_limit'))}",
        f"上限までの余裕：{yen(record.get('headroom_yen'))}",
        f"想定利益：{yen(record.get('estimated_profit'))}",
        f"想定利益率：{margin:.1f}%",
        f"相場件数：{sample_count}件",
        f"confidence：{confidence:.2f}",
        f"残り時間：{int(float(record.get('minutes_remaining') or 0))}分",
        f"終了日時：{text(end_at)}",
        f"URL：{text(record.get('item_url'))}",
        f"判定理由：{text(record.get('reason_code'))}",
    ])
