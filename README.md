# ヤフオク監視

オフモール監視とは独立した監視プロジェクトです。同じGoogleスプレッドシートの
`priority_items`を読み取り専用で参照し、書き込みは`yahoo_`から始まる専用シートだけに行います。

## 動作

- `有効=1`の型番・キーワードから検索語を生成
- 同一検索語を共通化
- A 60%、B 25%、その他15%を基本枠とするラウンドロビン検索
- 429検出時は当該実行の追加アクセスを停止
- 新着、状態、SOLD、10分以内SOLDを専用シートへ保存
- 優先商品は状態別相場・仕入れ上限を使ってDiscord/LINE通知
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

Google Sheets、Discord、LINE等への同時アクセスを避けるため、既存監視と起動時刻を1～2分
ずらします。例えばオフモールが毎時`00, 05, 10...`なら、ヤフオクは
`02, 07, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57`にします。

Renderへ設定するCron Schedule例：

```text
2,7,12,17,22,27,32,37,42,47,52,57 * * * *
```

この設定はヤフオク専用Cron Jobにだけ適用します。

実行コマンド：`python monitor.py`

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
