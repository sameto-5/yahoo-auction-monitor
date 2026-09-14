# YAHOO PHASE 8A.2 Bootstrap Mode 配備手順

## 目的

DRY RUNで毎回検出される既存Yahoo商品を、大量通知せずSheetsへbaseline登録します。
DB、migration、Cron、Sheets schemaは変更しません。

## 1. コード配備

ZIPを解凍し、`yahoo_auction_monitor_phase8a2`内のファイルをGitHubリポジトリ直下へ
上書きします。

## 2. Bootstrap開始

Render Environmentを次に変更します。

```text
DRY_RUN=0
YAHOO_BOOTSTRAP_MODE=1
```

Bootstrap中は、Yahoo専用Sheetsとcursorへ実書き込みを行い、即時候補は
`BOOTSTRAP_SUPPRESSED`として登録します。Discord/LINE、終了間近、SOLDの実通知は送りません。

## 3. 完了まで維持

queryは1run最大30件のため、Bootstrapは1runで完了するとは限りません。
`YAHOO_PHASE8A_METRIC`で次になるまで`YAHOO_BOOTSTRAP_MODE=1`を維持します。

```text
yahoo_bootstrap_mode=true
yahoo_bootstrap_query_remaining_count=0
yahoo_bootstrap_complete=true
```

## 4. 通常運用へ切替

Bootstrap完了後、次に変更します。

```text
DRY_RUN=0
YAHOO_BOOTSTRAP_MODE=0
```

以後は新しく検出した商品だけが通知対象になります。

## Rollback

PHASE 8A.1に戻してredeployします。Bootstrapで作成したYahoo Sheets行はbaselineとして残し、
一括削除しません。DB・Sheets migrationはありません。
