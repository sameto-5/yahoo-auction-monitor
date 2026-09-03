import re


LABELS = {
    "working": "正常動作品",
    "unchecked": "動作未確認・現状品",
    "junk": "ジャンク品",
    "unknown": "判定不明",
}

PATTERNS = {
    "junk": [
        r"ジャンク(?:品|扱い)?", r"故障(?:品)?", r"不動", r"動かない",
        r"起動(?:しない|不可|できない)", r"電源(?:が)?(?:入らない|入りません|入らず)",
        r"破損", r"修理前提", r"部品取(?:り)?", r"現状では使用不可",
        r"正常動作しない", r"読み込み(?:不可|できない|できません)",
        r"液晶.{0,8}(?:不良|映らない)", r"ボタン.{0,8}(?:反応なし|不良)",
        r"充電(?:不可|できない|できません)", r"接触不良",
    ],
    "unchecked": [
        r"動作未確認", r"未確認", r"未チェック", r"通電(?:のみ)?確認(?:のみ)?",
        r"通電ok(?:のみ)?", r"通電のみ", r"電源のみ確認", r"詳細未確認",
        r"動作確認していません", r"動作確認環境.{0,8}(?:ありません|ない)",
        r"現状品", r"現状渡し", r"現状販売", r"動作保証なし",
    ],
    "working": [
        r"動作確認済み", r"正常動作品", r"動作品", r"正常動作",
        r"正常に使用できます", r"各機能確認済み", r"各動作確認済み",
        r"動作ok", r"問題なく使用可能", r"問題なく使用できます",
    ],
}


def classify_item_condition(title="", description="", condition_text=""):
    sources = (
        ("タイトル", title),
        ("説明", description),
        ("店舗状態", condition_text),
    )
    for status_class in ("junk", "unchecked", "working"):
        for source_name, value in sources:
            text = re.sub(r"\s+", " ", str(value or "")).lower()
            for pattern in PATTERNS[status_class]:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return status_class, f"{source_name}に『{match.group(0)}』を検出"
    return "unknown", "状態を判断できる表現なし"


def to_int(value):
    try:
        text = str(value).replace(",", "").replace("円", "").strip()
        if not text or text in {"-", "nan", "None"}:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def prices_for_condition(rule, status_class):
    columns = {
        "working": ("動作品相場", "動作品仕入れ上限"),
        "unchecked": ("未確認/現状品相場", "未確認/現状品仕入れ上限"),
        "junk": ("ジャンク相場", "ジャンク仕入れ上限"),
    }
    market_col, limit_col = columns.get(status_class, (None, None))
    market = to_int(rule.get(market_col)) if market_col else None
    limit = to_int(rule.get(limit_col)) if limit_col else None
    return {
        "market": market if market is not None else to_int(rule.get("予想相場")),
        "limit": limit if limit is not None else to_int(rule.get("仕入れ上限")),
        "legacy_market": market is None,
        "legacy_limit": limit is None,
    }
