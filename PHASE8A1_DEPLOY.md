# YAHOO PHASE 8A.1 配備手順

## 目的

`priority_items`の原本データを変更せず、`/`を含むYahoo検索queryのHTTP 404と
後続queryの停滞を防ぎます。

## 動作

- Sheet値：`1/20 Lotus 79 OLYMPUS 1978`
- Yahoo送信値：`1 20 Lotus 79 OLYMPUS 1978`
- 404：`SEARCH_404_SKIP`を出力して次queryへ継続
- 403/429：PHASE 8AどおりYahoo HTTP全体を安全停止

## 配備

1. ZIP内の`yahoo_auction_monitor_phase8a1`の中身をYahoo GitHubリポジトリ直下へ上書きします。
2. Cron、Environment、Google Sheetsは変更しません。
3. `DRY_RUN=1`のまま1～2run確認します。
4. `SEARCH_ERROR: 1/20... HTTP Error 404`が消え、`completed=30`となることを確認します。

## Rollback

PHASE 8Aコード一式に戻してredeployします。DB・Sheets migrationはありません。
