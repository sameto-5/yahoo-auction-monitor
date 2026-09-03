from curl_cffi import requests


def send_discord(webhook_url, message, dry_run=False):
    if dry_run:
        print("[DRY_RUN][DISCORD]\n" + message)
        return True
    if not webhook_url:
        print("DISCORD_SKIP: webhook未設定")
        return False
    try:
        response = requests.post(webhook_url, json={"content": message}, timeout=20)
        if 200 <= response.status_code < 300:
            return True
        print(f"DISCORD_ERROR: {response.status_code} {response.text[:200]}")
    except Exception as error:
        print(f"DISCORD_ERROR: {error}")
    return False


def send_line(access_token, destination_id, message, dry_run=False, destination_label="personal"):
    if dry_run:
        print(f"[DRY_RUN][LINE:{destination_label}]\n" + message)
        return True
    if not access_token or not destination_id:
        print(f"LINE_SKIP: {destination_label}の認証情報または送信先未設定")
        return False
    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"to": destination_id, "messages": [{"type": "text", "text": message[:5000]}]},
            timeout=20,
        )
        if 200 <= response.status_code < 300:
            return True
        print(f"LINE_ERROR[{destination_label}]: HTTP {response.status_code}")
    except Exception as error:
        print(f"LINE_ERROR[{destination_label}]: {type(error).__name__}")
    return False


def send_line_notifications(
    access_token,
    user_id,
    group_id,
    notify_mode,
    message,
    dry_run=False,
):
    """個人・グループを独立送信し、片方の失敗で他方を止めない。"""
    mode = str(notify_mode or "personal").strip().lower()
    if mode not in {"personal", "group", "both"}:
        print("LINE_NOTIFY_MODE_INVALID: personalとして処理")
        mode = "personal"

    results = {}
    if mode in {"personal", "both"}:
        results["personal"] = send_line(
            access_token, user_id, message, dry_run, "personal"
        )
    if mode in {"group", "both"}:
        results["group"] = send_line(
            access_token, group_id, message, dry_run, "group"
        )
    return results
