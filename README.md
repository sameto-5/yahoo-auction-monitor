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
YAHOO_ENDING_CHECK_MINUTES=30
YAHOO_ENDING_CHECKS_PER_RUN=10
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
- `yahoo_auction_watch`

競り形式は新着通知せず`yahoo_auction_watch`へ保存し、終了30分以内かつ
詳細未確認の商品だけを1回再取得します。確認済み商品は定期再取得しません。

`priority_items`へは書き込みません。自動候補は`yahoo_priority_candidates`へ保存し、人が確認して
必要なものだけ共通マスターへ登録します。

## 注意

Yahoo!オークションのHTML変更により解析調整が必要になる場合があります。単なる終了表示は
落札と断定せず`ended`として記録し、明確な落札表記がある場合のみ`sold`として扱います。

## PHASE 8D Shadow

`shadow_monitor.py`は既存の`monitor.py`から独立した終了間近監視です。設定は
`yahoo_auction_rules`を読み取り専用で使い、`yahoo_auction_watch`の用途や列は変更しません。

安全初期値は必ず次のとおりです。

```text
YAHOO_AUCTION_ENABLED=false
YAHOO_AUCTION_SHADOW=true
YAHOO_AUCTION_NOTIFY_ENABLED=false
```

`ENABLED=false`の場合はSheets、Yahooのいずれにも接続しません。Shadow実行は
`python shadow_monitor.py`であり、通知モジュールをimport・呼び出しません。

ルールは行番号ではなく`rule_id`と最終処理時刻で巡回します。最も古いルールが優先され、
優先度は同時刻の場合のタイブレークにだけ使います。30分間隔・1回30ルールなら、
100ルールの理論上の一巡時間は120分です。投影値が検索先読み時間を超える設定は警告します。

検索結果は初期値で1ルールあたり15件、1実行全体で50件まで処理します。3ルール限定実行なら
最大45件となるため、先頭ルールの検索結果だけで全体上限を使い切らず、選択した3ルールを
順番に処理できます。同一検索語を共有するルールは1回のHTTP検索結果を共用します。

Shadowの実行時保存先はGoogle Sheetsのみです。Neonや`DATABASE_URL`には接続しません。
既存のDB migrationは履歴として残しますが、Shadow実行経路からは参照しません。

専用シートは次の4枚です。既存の`yahoo_auction_rules`、`priority_items`、
`yahoo_auction_watch`の列・データは変更しません。

- `yahoo_shadow_cursor`: `rule_id`別最終処理時刻と期限付きlease
- `yahoo_model_stats`: 型番別の件数・中央値・四分位・信頼度・更新日時
- `yahoo_shadow_results`: `(auction_id, rule_id)`で上書きする最小Shadow結果
- `yahoo_run_stats`: UTC日付ごとの集約実行統計

定常時は、専用4シートとルール・優先商品を1回のbatch readで取得し、leaseを1回の
batch writeで取得後に再読込確認します。処理終了時はcursor・結果・日次統計を1回の
batch writeで保存します。leaseだけでは完全なCASを実現できないため、Render Cronの
単一実行制御と併用し、期限切れleaseだけを取得します。

相場統計が未登録、サンプル不足、低信頼度、または古い場合は`DATA_INSUFFICIENT`として
Shadow保存し、`CANDIDATE`にはしません。初期値は5サンプル、信頼度0.5、30日以内です。

## PHASE 8E Discord通知

PHASE 8EはPHASE 8DのSheets保存を継続しながら、終了30分以内の`CANDIDATE`と
終了15分以内の`STRONG_CANDIDATE`だけをYahoo専用Discord Webhookへ送信します。
`YAHOO_DISCORD_WEBHOOK_URL`が空の場合は外部アクセス前に安全停止し、Off-Mall用Webhookへ
フォールバックしません。`YAHOO_AUCTION_NOTIFY_ENABLED=false`へ戻せば検索・Shadow保存を
維持したまま通知だけを停止できます。

送信済みは`yahoo_notification_state`へ`auction_id + rule_id + notification_stage`で保存し、
同一段階は再送しません。失敗だけを`yahoo_notification_retry`へ保存し、最大3回の指数
バックオフを行います。Webhookや認証情報はSheets・ログへ保存しません。

初期安全設定：

```text
YAHOO_AUCTION_ENABLED=false
YAHOO_AUCTION_SHADOW=true
YAHOO_AUCTION_NOTIFY_ENABLED=false
YAHOO_AUCTION_MAX_RULES_PER_RUN=30
YAHOO_AUCTION_MAX_ITEMS_PER_RULE=15
YAHOO_AUCTION_MAX_ITEMS_PER_RUN=50
YAHOO_AUCTION_LOOKAHEAD_MINUTES=180
YAHOO_AUCTION_EXPECTED_CRON_INTERVAL_MINUTES=30
```
