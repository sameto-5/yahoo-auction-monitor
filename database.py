import json
from contextlib import contextmanager

try:
    import psycopg
except ImportError:  # Tests can exercise pure logic without installing DB dependencies.
    psycopg = None


LOCK_NAMESPACE = 20260912
LOCK_KEY = 801


def connect(database_url):
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")
    return psycopg.connect(database_url)


@contextmanager
def advisory_lock(connection):
    acquired = False
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (LOCK_NAMESPACE, LOCK_KEY))
            acquired = bool(cur.fetchone()[0])
        yield acquired
    finally:
        if acquired:
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, LOCK_KEY))
            except Exception:
                # An aborted transaction must be cleared before the explicit unlock.
                connection.rollback()
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, LOCK_KEY))


def load_scheduler_state(connection):
    with connection.cursor() as cur:
        cur.execute("SELECT last_processed_at FROM yahoo_auction_state WHERE singleton = TRUE")
        row = cur.fetchone()
    return dict(row[0] or {}) if row else {}


def save_scheduler_state(connection, state):
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO yahoo_auction_state(singleton,last_processed_at,updated_at)
               VALUES(TRUE,%s::jsonb,NOW()) ON CONFLICT(singleton) DO UPDATE SET
               last_processed_at=EXCLUDED.last_processed_at,updated_at=NOW()""",
            (json.dumps(state),),
        )


def upsert_shadow_records(connection, records):
    sql = """INSERT INTO yahoo_auction_shadow_results
        (auction_id,rule_id,title,item_url,normalized_model,current_price,buy_shipping,
         buy_shipping_source,estimated_total_cost,expected_sale_price,sale_fee,
         estimated_sale_shipping,other_cost,minimum_profit,estimated_profit,
         explicit_purchase_limit,priority_purchase_limit,calculated_purchase_limit,
         effective_purchase_limit,minutes_remaining,end_at,decision,reason_code,
         reason_detail,expires_at)
        VALUES (%(auction_id)s,%(rule_id)s,%(title)s,%(item_url)s,%(normalized_model)s,
         %(current_price)s,%(buy_shipping)s,%(buy_shipping_source)s,%(estimated_total_cost)s,
         %(expected_sale_price)s,%(sale_fee)s,%(estimated_sale_shipping)s,%(other_cost)s,
         %(minimum_profit)s,%(estimated_profit)s,%(explicit_purchase_limit)s,
         %(priority_purchase_limit)s,%(calculated_purchase_limit)s,%(effective_purchase_limit)s,
         %(minutes_remaining)s,%(end_at)s,%(decision)s,%(reason_code)s,%(reason_detail)s,%(expires_at)s)
        ON CONFLICT(auction_id,rule_id) DO UPDATE SET title=EXCLUDED.title,item_url=EXCLUDED.item_url,
         current_price=EXCLUDED.current_price,buy_shipping=EXCLUDED.buy_shipping,
         buy_shipping_source=EXCLUDED.buy_shipping_source,estimated_total_cost=EXCLUDED.estimated_total_cost,
         estimated_profit=EXCLUDED.estimated_profit,effective_purchase_limit=EXCLUDED.effective_purchase_limit,
         minutes_remaining=EXCLUDED.minutes_remaining,end_at=EXCLUDED.end_at,decision=EXCLUDED.decision,
         reason_code=EXCLUDED.reason_code,reason_detail=EXCLUDED.reason_detail,last_seen_at=NOW(),
         expires_at=EXCLUDED.expires_at,updated_at=NOW()"""
    with connection.cursor() as cur:
        cur.executemany(sql, records)


def cleanup(connection, batch_size):
    with connection.cursor() as cur:
        cur.execute("""WITH doomed AS (SELECT auction_id,rule_id FROM yahoo_auction_shadow_results
            WHERE expires_at<=NOW() ORDER BY expires_at LIMIT %s)
            DELETE FROM yahoo_auction_shadow_results t USING doomed d
            WHERE t.auction_id=d.auction_id AND t.rule_id=d.rule_id""", (batch_size,))
        shadow_deleted = cur.rowcount
        cur.execute("""WITH doomed AS (SELECT run_id FROM yahoo_auction_run_stats
            WHERE expires_at<=NOW() ORDER BY expires_at LIMIT %s)
            DELETE FROM yahoo_auction_run_stats t USING doomed d WHERE t.run_id=d.run_id""", (batch_size,))
        return shadow_deleted, cur.rowcount


def save_run_stats(connection, stats):
    columns = [
        "run_id", "status", "started_at", "finished_at", "duration_ms",
        "enabled_rule_count", "searched_rule_count", "fetched_item_count",
        "ending_item_count", "candidate_count", "shadow_upsert_count",
        "detail_fetch_count", "http_error_count", "projected_cycle_minutes",
        "error_code", "expires_at",
    ]
    values = [stats.get(name) for name in columns]
    placeholders = ",".join(["%s"] * len(columns))
    with connection.cursor() as cur:
        cur.execute(
            f"INSERT INTO yahoo_auction_run_stats ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
