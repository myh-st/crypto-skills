from __future__ import annotations

import io
import json
import shutil
import urllib.error
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from crypto_eval.contracts import EvaluationError
from crypto_eval.market_data import (
    BINANCE_PROVIDER_ID,
    BinanceSpotKlinesProvider,
    build_market_archive_record,
    load_market_archive,
    write_market_archive,
)


ROOT = Path(__file__).resolve().parents[1]


def epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def kline(open_time: datetime, index: int = 0, interval_seconds: int = 3600) -> list[object]:
    base = 100.0 + index
    return [
        epoch_ms(open_time),
        str(base),
        str(base + 2),
        str(base - 1),
        str(base + 1),
        "12.5",
        epoch_ms(open_time + timedelta(seconds=interval_seconds)) - 1,
        "1250",
        10,
        "6",
        "600",
        "0",
    ]


class BinanceSpotProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.interval_seconds = 3600
        self.end = self.start + timedelta(hours=3)

    def _transport_for(self, rows: list[list[object]]):
        calls: list[dict[str, object]] = []

        def transport(url: str, timeout: float) -> bytes:
            parsed = urlparse(url)
            raw_query = parse_qs(parsed.query)
            query = {
                key: int(raw_query[key][0])
                if key in {"startTime", "endTime", "limit"}
                else raw_query[key][0]
                for key in raw_query
            }
            calls.append({"path": parsed.path, "query": query, "timeout": timeout})
            selected = [
                row for row in rows if query["startTime"] <= int(row[0]) <= query["endTime"]
            ]
            return json.dumps(selected[: query["limit"]]).encode("utf-8")

        return transport, calls

    def test_fetch_range_normalizes_closed_klines_and_paginates(self) -> None:
        rows = [kline(self.start + timedelta(hours=index), index) for index in range(3)]
        transport, calls = self._transport_for(rows)
        provider = BinanceSpotKlinesProvider(transport=transport, timeout=4)

        candles = provider.fetch_range(
            "btcusdt", "1h", self.start, self.end, limit=2
        )

        self.assertEqual(len(candles), 3)
        self.assertEqual(candles[0]["open_time"], "2026-01-01T00:00:00.000000Z")
        self.assertEqual(candles[0]["close_time"], "2026-01-01T01:00:00.000000Z")
        self.assertEqual(candles[-1]["close_time"], "2026-01-01T03:00:00.000000Z")
        self.assertEqual(candles[0]["close"], 101.0)
        self.assertEqual(candles[0]["volume"], 12.5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["path"] == "/api/v3/klines" for call in calls))
        self.assertEqual(calls[0]["query"]["endTime"], epoch_ms(self.end) - 1)
        self.assertEqual(calls[0]["query"]["timeZone"], "0")
        self.assertNotIn("apiKey", json.dumps(calls[0]))

    def test_offset_ranges_use_utc_and_current_candles_fail_cutoff_check(self) -> None:
        rows = [kline(self.start + timedelta(hours=index), index) for index in range(3)]
        transport, calls = self._transport_for(rows)
        provider = BinanceSpotKlinesProvider(transport=transport)
        bangkok_start = self.start.astimezone(timezone(timedelta(hours=7)))
        bangkok_end = self.end.astimezone(timezone(timedelta(hours=7)))

        candles = provider.fetch_range("BTCUSDT", "1h", bangkok_start, bangkok_end)

        self.assertEqual(candles[0]["open_time"], "2026-01-01T00:00:00.000000Z")
        self.assertEqual(calls[0]["query"]["startTime"], epoch_ms(self.start))
        self.assertEqual(calls[0]["query"]["timeZone"], "0")
        with self.assertRaisesRegex(EvaluationError, "closes after the requested data cutoff"):
            BinanceSpotKlinesProvider._normalize_kline(
                kline(self.start + timedelta(hours=1)),
                "BTCUSDT",
                "1h",
                self.interval_seconds,
                self.start + timedelta(hours=1, minutes=30),
                "current_kline",
            )

    def test_fetch_range_fails_closed_on_missing_or_future_candles(self) -> None:
        missing_middle = [
            kline(self.start, 0),
            kline(self.start + timedelta(hours=2), 2),
        ]
        provider = BinanceSpotKlinesProvider(transport=self._transport_for(missing_middle)[0])
        with self.assertRaisesRegex(EvaluationError, "missing|incomplete"):
            provider.fetch_range("BTCUSDT", "1h", self.start, self.end)

        future_row = [kline(self.end, 3)]
        provider = BinanceSpotKlinesProvider(transport=self._transport_for(future_row)[0])
        with self.assertRaises(EvaluationError):
            provider.fetch_range("BTCUSDT", "1h", self.start, self.end)

    def test_fetch_range_rejects_malformed_ohlcv_and_unaligned_ranges(self) -> None:
        malformed = kline(self.start)
        malformed[4] = "NaN"
        provider = BinanceSpotKlinesProvider(transport=self._transport_for([malformed])[0])
        with self.assertRaises(EvaluationError):
            provider.fetch_range("BTCUSDT", "1h", self.start, self.start + timedelta(hours=1))

        provider = BinanceSpotKlinesProvider(transport=lambda _url, _timeout: b"[]")
        with self.assertRaisesRegex(EvaluationError, "aligned"):
            provider.fetch_range(
                "BTCUSDT",
                "1h",
                self.start + timedelta(minutes=1),
                self.start + timedelta(hours=1),
            )

    def test_rate_limit_and_transport_errors_are_sanitized(self) -> None:
        secret = "mock-token-not-a-real-credential"

        def rate_limited(url: str, _timeout: float) -> bytes:
            raise urllib.error.HTTPError(
                url,
                429,
                f"rate-limited {secret}",
                {},
                io.BytesIO(secret.encode("utf-8")),
            )

        provider = BinanceSpotKlinesProvider(transport=rate_limited)
        with self.assertRaises(EvaluationError) as context:
            provider.fetch_range("BTCUSDT", "1h", self.start, self.start + timedelta(hours=1))
        self.assertIn("429", str(context.exception))
        self.assertNotIn(secret, str(context.exception))

        provider = BinanceSpotKlinesProvider(
            transport=lambda _url, _timeout: (_ for _ in ()).throw(TimeoutError(secret))
        )
        with self.assertRaises(EvaluationError) as context:
            provider.fetch_range("BTCUSDT", "1h", self.start, self.start + timedelta(hours=1))
        self.assertNotIn(secret, str(context.exception))

    def test_history_rejects_stale_latest_bar(self) -> None:
        now = self.end + timedelta(minutes=10)
        rows = [kline(self.start + timedelta(hours=index), index) for index in range(2)]
        provider = BinanceSpotKlinesProvider(
            transport=self._transport_for(rows)[0],
            clock=lambda: now,
        )
        with self.assertRaises(EvaluationError):
            provider.fetch_history("BTCUSDT", "1h", bars=3)

    def test_archive_records_retrieval_cutoff_and_content_hash_immutably(self) -> None:
        rows = [kline(self.start + timedelta(hours=index), index) for index in range(3)]
        provider = BinanceSpotKlinesProvider(transport=self._transport_for(rows)[0])
        candles = provider.fetch_range("BTCUSDT", "1h", self.start, self.end)
        retrieved_at = self.end + timedelta(minutes=2)
        record = build_market_archive_record(
            BINANCE_PROVIDER_ID,
            "BTCUSDT",
            "1h",
            self.start,
            self.end,
            candles,
            retrieved_at,
        )
        archive_dir = ROOT / f".test-market-archive-{__import__('os').getpid()}"
        self.addCleanup(shutil.rmtree, archive_dir, ignore_errors=True)

        path = write_market_archive(record, archive_dir)
        loaded = load_market_archive(path)

        self.assertEqual(loaded["provider_id"], BINANCE_PROVIDER_ID)
        self.assertEqual(loaded["data_cutoff"], candles[-1]["close_time"])
        self.assertEqual(loaded["retrieved_at"], "2026-01-01T03:02:00.000000Z")
        self.assertEqual(loaded["content_sha256"], record["content_sha256"])
        self.assertEqual(write_market_archive(record, archive_dir), path)
        self.assertNotIn("api_key", json.dumps(loaded).lower())


if __name__ == "__main__":
    unittest.main()
