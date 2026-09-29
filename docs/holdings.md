# Holdings: manual entries + Gate spot wallet sync (read-only)

Holdings shows what you actually hold on spot: coins, quantity, average entry price, value and
profit/loss, next to the co-trader rule. It is decision support only. Nothing here places, amends
or cancels an order, and there is no code path that could: the Gate client is GET-only with an exact
endpoint allowlist, checked before any request is built.

Code: `crypto_eval/holdings.py` (service), `crypto_eval/gate_account.py` (read-only client and
normalizers), routes in `crypto_eval/paper_server.py`. Tests: `tests/test_holdings.py`.

## User guide: connect Gate read-only

1. On Gate, open **API Management** and create a new **APIv4** key.
2. Give it **read-only spot** permission only. Never enable trading, withdrawal, transfer,
   margin or futures-trade rights. This app never needs them and never asks for them.
3. Add an **IP whitelist** with your machine's public IP if you can.
4. In this app, open **Settings › Exchange Accounts**, create the account (for example `gate-main`),
   and paste the key and secret. They go straight into the OS credential store (macOS Keychain); only
   masked metadata comes back. The browser never stores them.
5. Press **Validate**. It makes one signed read-only GET to `/spot/accounts` and reports whether the
   key authenticates and can read spot balances. Gate's API cannot prove a key is read-only without
   attempting a write, and this app never attempts one, so confirm the permission on Gate yourself.
6. Press **Sync from Gate**. The co-trader scheduler is meant to call `sync_if_due()` every
   15 minutes when an account is configured (wired together with the co-trader); the endpoints work
   without it.

You can also add coins by hand (**Add coin**), for example coins held on another exchange or in a
cold wallet, and set an average price for coins that were deposited to Gate.

## Sources and precedence

| Source | What it gives |
|---|---|
| Manual (`holdings_manual`) | `base`, `qty`, `avg_price`, `opened_at`, `note`. One entry per coin; adding a coin that exists edits that entry. |
| Gate spot (`/spot/accounts`) | Wallet quantity (`available + locked`) per currency. |
| Gate fills (`/spot/my_trades`) | Moving-average cost basis and realized P&L for each held `{BASE}_USDT` pair. |
| Gate public tickers | Last price in USDT. |

- A coin on Gate uses the Gate quantity (`qty_source: "gate"`). If a manual entry exists for the
  same coin, its `avg_price` overrides the trade-derived one (`avg_source: "manual"`, `manual_id`
  set). Realized P&L still comes from the Gate fills.
- A coin that is only manual uses the manual quantity and price; `realized_usdt` is null.
- USDT and USDC are listed under `cash`, not as holdings. USDC is valued at its `USDC_USDT` ticker.
- If a sync fails, the last good snapshot is still shown with `status: "ERROR"` and the time of the
  last good sync.

## Cost basis (moving average)

Fills of each held `{BASE}_USDT` pair are fetched newest first, in 30-day windows, back at most
365 days or 2,000 fills (whichever comes first), then replayed oldest first:

- **Buy:** `cost += amount × price`, plus the fee when it is paid in USDT. `qty += amount`, minus the
  fee when it is paid in the coin.
- **Sell:** `realized += (price − avg) × amount − USDT fee`. The cost falls by `avg × amount`, so the
  average is unchanged. A sell larger than the tracked quantity only realizes the matched part.
- **GT, point or other-currency fees** cannot be converted, so they are left out of the cost and
  listed in `cost_basis_note`.
- **Unexplained balance.** When the wallet holds more than the fills explain (within 0.1%), for
  example after a deposit, a transfer, or buys on another quote pair, the row gets
  `avg_source: "unknown"` with null `avg_price`, `cost_usdt` and unrealized P&L, and
  `cost_basis_note` says how much the fills explain. A cost is never invented. Set a manual average
  price to track P&L for that coin.
- A balance lower than the fills imply (a withdrawal) keeps the moving average, with a note.

## Totals

- `value_usdt` sums the holdings whose price is known (cash is separate, in `cash_usdt`).
- `cost_usdt` sums the holdings whose cost is known.
- `unrealized_usdt` and `unrealized_pct` use only the holdings where both value and cost are known.
- `allocation` is each holding's share of `value_usdt`.
- `cash_usdt` is null when Gate has never synced (the cash is not known).

## Rule alignment and AI sharing

- `Holdings(runtime, rule_state_provider=...)` takes an optional callable
  `base -> "HOLD" | "CASH" | "WATCH" | None` (the co-trader rule). Without it, `rule_state` and
  `alignment` are null. A holding is `aligned` in HOLD, and `holding_in_cash_state` in CASH or WATCH
  (the rule holds nothing in WATCH). `alignment_for(state, held)` also returns
  `not_held_in_hold_state` for a coin in HOLD that is not held.
- `share_holdings_with_ai` defaults to false. `Holdings.holding_for_ai(base)` returns
  `{held, qty, avg_price, unrealized_pct}` for that one coin only when it is true, and `None`
  otherwise (or when holdings are unknown). It never includes account ids, keys, or other coins.
- `Holdings.held(base)` returns true/false when holdings are known, else null. `sync_if_due()` is the
  15-minute scheduler hook.

## API (loopback; POSTs keep the origin check)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/holdings` | | The holdings payload below |
| POST | `/api/holdings/manual` | `{id?, base, qty, avg_price, opened_at?, note?}` | `{"entry": {...}}` |
| POST | `/api/holdings/manual/<id>/delete` | | `{"deleted": true}` |
| POST | `/api/holdings/sync` | `{account_id?}` | The holdings payload, with `sources.gate` updated |
| POST | `/api/holdings/settings` | `{share_holdings_with_ai: bool}` | `{share_holdings_with_ai}` |
| POST | `/api/holdings/gate/validate` | none | `{ok, reason?, checks, balances_nonzero, key_hint, checked_at, read_only_note, error_code?}` |

Validation: `base` is 1–20 uppercase letters or digits (not USDT/USDC), `qty > 0`, `avg_price > 0`,
`opened_at` an ISO date or date-time, `note` at most 500 characters. Errors return HTTP 400.

```json
{
  "as_of": "…",
  "sources": {
    "gate": {"account_id": "gate-main", "synced_at": "…", "status": "OK|NOT_CONFIGURED|ERROR", "error": null,
             "last_validation": {"ok": true, "checked_at": "…", "error_code": null, "account_id": "gate-main"}},
    "manual_count": 2,
    "prices": {"source": "gate_spot_public_tickers", "status": "OK|ERROR|NOT_NEEDED", "error": null}
  },
  "share_holdings_with_ai": false,
  "totals": {"value_usdt": 1234.5, "cost_usdt": 1100.0, "unrealized_usdt": 134.5, "unrealized_pct": 0.1223,
             "realized_usdt": 12.3, "cash_usdt": 250.0},
  "holdings": [{"base": "NEAR", "qty": 50.0, "qty_source": "gate", "avg_price": 4.21, "avg_source": "gate_trades",
                "price": 4.86, "value_usdt": 243.0, "cost_usdt": 210.5, "unrealized_usdt": 32.5, "unrealized_pct": 0.154,
                "realized_usdt": 3.1, "allocation": 0.197, "rule_state": "HOLD", "alignment": "aligned",
                "trades_counted": 14, "cost_basis_note": null, "manual_id": null}],
  "cash": [{"currency": "USDT", "qty": 250.0}]
}
```

`sources.gate.last_validation` and `sources.prices` are additive to the spec contract.
`NOT_CONFIGURED` covers no account, no stored keys, and an account that has not been synced yet;
`error` says which.

### Validate (`POST /api/holdings/gate/validate`)

- No stored key: `{ok: false, reason: "no_key"}` and no network call.
- Otherwise exactly one signed GET to `/spot/accounts`. It never calls `/spot/my_trades` and never
  syncs.
- `checks.auth` and `checks.spot_read` are `ok`, `failed` or `skipped`. On failure `error_code` is
  Gate's `label` (for example `INVALID_KEY`, `INVALID_SIGNATURE`, `FORBIDDEN`, `IP_NOT_ALLOWED`),
  or `HTTP_<status>`, `TIMEOUT` or `NETWORK_ERROR`. Only a bare uppercase label is read from the
  error body; the rest of the body is discarded.
- `balances_nonzero` is a count only. `key_hint` is masked metadata (account id, credential backend).
- Only `{ok, checked_at, error_code, account_id}` is persisted, as `holdings_settings.gate_validation`.

## Security notes

- Gate calls are GET only, on the exact allowlist `/spot/accounts` (query `currency`) and
  `/spot/my_trades` (query `currency_pair`, `limit`, `page`, `from`, `to`), plus the existing
  futures read endpoints. Any other method, path or query key raises `LiveExecutionBlocked` before
  any transport call.
- Keys are resolved server-side from the OS credential store via the existing exchange-account
  records (`gate.<account_id>.api-key` / `.api-secret`). They never reach SQLite, responses, logs or
  error text. Error messages are fixed strings (for example `Gate read-only request returned HTTP 401`)
  and pass through `scrub()` as a second guard.
- SQLite stores the manual entries, a bounded history of sync snapshots (balances and the computed
  basis, never raw responses or keys), and the settings.
