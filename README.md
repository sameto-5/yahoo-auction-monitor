[README.md](https://github.com/user-attachments/files/31803057/README.md)
# ヤフオク監視

オフモール監視とは独立した監視プロジェクトです。同じGoogleスプレッドシートの
`priority_items`を読み取り専用で参照し、書き込みは`yahoo_`から始まる専用シートだけに行います。

## 動作

- `有効=1`の型番・キーワードから検索語を生成
- 同一検索語を共通化
- A 60%、B 25%、その他15%を基本枠とするラウンドロビン検索
- 検索・判定・必要データ保存が完了した検索語だけ巡回カーソルを確定
- Google Sheetsへの読み書きはシート単位でまとめ、APIリクエスト数を抑制
- 429検出時は当該実行の追加アクセスを停止し、次回実行へ持ち越し
- 新着、状態、SOLD、10分以内SOLDを専用シートへ保存
- 優先商品は状態別仕入れ上限以下の場合だけDiscord/LINE通知
- 状態別列が空欄なら旧`予想相場`・`仕入れ上限`へフォールバック

## 初回設定

1. `.env.example`の項目をRender等の環境変数へ登録します。
2. Googleサービスアカウントへ対象スプレッドシートの編集権限を付与します。
3. 初回は`DRY_RUN=1`でログと作成シートを確認します。
4. 問題がなければ`DRY_RUN=0`にします。

LINE通知は`LINE_NOTIFY_MODE`で切り替えます。初期値は`personal`です。

- `personal`: `LINE_USER_ID`だけに通知
- `group`: `LINE_GROUP_ID`だけに通知
- `both`: 個人とグループの両方へ通知

`LINE_CHANNEL_SECRET`はWebhook受信用なので、このCron Jobには設定しません。個人・グループの
送信は独立しており、片方が失敗しても他方、Discord、監視処理は継続します。

既存のLINEグループを使う場合も、新しいWebhook Web Serviceは作成しません。ヤフオク専用
Cron Jobへ既存の`LINE_GROUP_ID`と送信用環境変数を設定するだけです。初回確認は必ず
`LINE_NOTIFY_MODE=personal`で行い、個人通知と監視ログが正常であることを確認してから、
必要な場合だけ`both`へ切り替えます。

## Render

既存の`offmall-line-monitor` Cron Jobと`offmall-line-webhook` Web Serviceは変更しません。
ヤフオク用リポジトリから別のCron Jobを新規作成します。

- Build Command: `pip install -r requirements.txt`
- Command: `python monitor.py`
- 初回環境変数: `DRY_RUN=1`、`LINE_NOTIFY_MODE=personal`

Google Sheets、Discord、LINE等への同時アクセスを避けるため、ヤフオクはJST 09:00～21:00の
30分間隔とし、オフモールから2分ずらします。初期値は1回あたり検索30件、状態確認30件、
検索間待5～8秒、HTTP再試行2回です。

Renderへ設定するCron Schedule例：

```text
2,32 0-11 * * *
```

この設定はヤフオク専用Cron Jobにだけ適用します。

実行コマンド：`python monitor.py`

```text
YAHOO_BATCH_SIZE=30
YAHOO_ACTIVE_START_HOUR=9
YAHOO_ACTIVE_END_HOUR=21
YAHOO_SEARCH_DELAY_MIN_SECONDS=5
YAHOO_SEARCH_DELAY_MAX_SECONDS=8
YAHOO_HTTP_MAX_RETRIES=2
YAHOO_HTTP_BACKOFF_BASE_SECONDS=5
SHEETS_MAX_RETRIES=3
SHEETS_BACKOFF_BASE_SECONDS=3
```

## 作成するシート

- `yahoo_items`
- `yahoo_item_history`
- `yahoo_sold_fast_items`
- `yahoo_notified_items`
- `yahoo_monitor_state`
- `yahoo_priority_candidates`

`priority_items`へは書き込みません。自動候補は`yahoo_priority_candidates`へ保存し、人が確認して
必要なものだけ共通マスターへ登録します。

## 注意

Yahoo!オークションのHTML変更により解析調整が必要になる場合があります。単なる終了表示は
落札と断定せず`ended`として記録し、明確な落札表記がある場合のみ`sold`として扱います。
