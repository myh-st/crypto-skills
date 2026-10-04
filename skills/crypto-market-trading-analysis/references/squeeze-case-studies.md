# Squeeze and Leverage-Flush Case Studies

This file contains dated research examples. Never use these snapshot values as current market
data.

Research snapshot: 2026-10-04 Asia/Bangkok.

## Cross-sectional snapshot

| Asset | OI / market cap | Futures / spot 24h | Snapshot interpretation |
|---|---:|---:|---|
| SUI | 19.1% | 4.4x | high squeeze + flush capacity; strongest spot participation of the four |
| AVAX | 12.5% | 6.2x | meaningful leverage, less OI-crowded |
| SEI | 19.3% | 9.3x | highest two-sided cascade fragility / thin spot base |
| PYTH | 9.0% | 6.6x | lower OI concentration but thin-liquidity gap risk |

Recalculate these values for every live analysis.

## SUI institutional snapshot

The dossier directly verified at least three SUI exchange-traded products with combined AUM of
about $144.3M at the research snapshot: 21Shares U.S. TSUI, Canary SUIS and 21Shares European
ASUI. This was a verified minimum, not total institutional exposure.

AUM is not net inflow. Track creations/redemptions or token entitlement where possible. Staked
holdings may modify liquid float but do not guarantee appreciation.

## SUI leverage lesson

Preferred upside evidence chain:

~~~text
spot absorption
→ cheap/neutral funding
→ controlled OI build
→ structural breakout
→ short liquidations
→ spot/arb follow-through
~~~

Preferred DCA-after-selloff chain:

~~~text
forced long liquidation
→ OI collapse
→ funding reset
→ spot absorption
→ structural reclaim
~~~

A static price such as $1.10 is not sufficient by itself.

## October 10, 2025 systemic stress

The dossier identified a marketwide stress case in which more than $19B of leveraged positions
were reported liquidated in 24h and aggregate perpetual OI reportedly contracted about 43%,
roughly $217B to $123B. SUI experienced a major one-day decline in the same event.

Lesson: liquidation cascades become nonlinear when executable depth disappears. A liquidation
map without a depth/liquidity model is incomplete.

## Unlock and source-conflict caution

Post-unlock drawdowns observed across SUI, AVAX and SEI were associations, not proof that unlock
recipients sold.

The SUI unlock trackers in the research run disagreed on the exact next event date. Use an event
window, prefer authoritative project/on-chain schedules and never silently choose the date that
best supports a thesis.

This case study does not prove a universal squeeze cycle, intentional stop hunting, that high OI
is bearish, that a heatmap level will be visited, that ETP assets force price higher, or that SUI
will repeat the 2026 snapshot.
