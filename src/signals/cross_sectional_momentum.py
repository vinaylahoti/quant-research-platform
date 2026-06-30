"""Pure cross-sectional momentum signal.

Committed rule (locked before running; does not change after seeing results):
  At each 4h bar, rank all active universe symbols by their trailing 20-bar
  (4h) close-to-close return.  Top 6 → long (+1), bottom 6 → short (-1),
  middle remainder → flat (0).  Ties broken by symbol name (alphabetical),
  which is deterministic and arbitrary — not information.

This is structurally different from time-series momentum (WS6): it never
makes an absolute-direction bet ("will price go up"), only a relative one
("will this symbol outperform the others currently in the universe").
"""

from __future__ import annotations


def cross_sectional_momentum_signal(
    trailing_returns: dict[str, float],
    *,
    top_k: int = 6,
) -> dict[str, int]:
    """
    Args:
        trailing_returns: {symbol: trailing_N_bar_return} for all active symbols.
            Symbols with NaN returns must be excluded by the caller.
        top_k: number of symbols to go long AND short each.

    Returns:
        {symbol: signal} where signal ∈ {-1, 0, +1}.
    """
    if not trailing_returns:
        return {}

    # Sort by return descending; ties broken alphabetically by symbol name.
    ranked = sorted(trailing_returns.items(), key=lambda kv: (-kv[1], kv[0]))
    n = len(ranked)

    result: dict[str, int] = {}
    for rank, (symbol, _) in enumerate(ranked):
        if rank < top_k:
            result[symbol] = 1
        elif rank >= n - top_k:
            result[symbol] = -1
        else:
            result[symbol] = 0
    return result
