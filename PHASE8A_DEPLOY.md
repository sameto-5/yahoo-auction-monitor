# YAHOO PHASE 8A 配備手順

PHASE 8Aは現行Yahoo監視のアクセス安全性とwatch更新を改善するコード更新です。
DB migration、Neon table、Cron、Sheets schemaの変更はありません。

## 変更点

- watch詳細の2件目以降に既存5～8秒のランダムwaitを適用
- SEARCH / WATCH / STATUSのいずれかで403/429を検出したら、そのrunのYahoo HTTPを停止
- レート制限前に得た商品・履歴・watch更新はSheetsへbatch保存
- 再検索した既存watchの価格、送料、即決価格、終了時刻を、新値が取得できた場合のみ更新
- `ending_notified`と`詳細確認済み`は再検索でリセットしない
- 必要チャネルがFalseを返した場合は通知済みとして保存しない
- `YAHOO_PHASE8A_METRIC` JSON logを追加

## 配備

1. 現行Yahoo Cronのコードをバックアップします。
2. このディレクトリのファイルをYahoo専用リポジトリへ配備します。
3. Build Commandは従来どおり `pip install -r requirements.txt`です。
4. Commandは従来どおり `python monitor.py`です。
5. Cronと既存Environmentは変更しません。
6. 初回runで`YAHOO_PHASE8A_METRIC`、`YAHOO_RATE_LIMIT_STOP`、`RUN_END`を確認します。

## 通常上限

- search logical requests: 30
- watch detail logical requests: 10
- status detail logical requests: 30
- 合計logical requests: 70

5xx/timeoutの検索retryは従来どおりのため、metricのactual request countは70を
超える場合があります。watch/statusはretry 0です。

watchが10件の場合、取得間の9回waitにより約45～72秒増加します。

## Rollback

DB/Sheets migrationはありません。配備前のYahooコード一式に戻してredeployします。
Off-Mall PHASE 6/7の変更やrollbackは不要です。
