# quant/analysis.py

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def max_drawdown(returns_df: pd.DataFrame) -> dict:
    """
    Maximum drawdown for each asset.
    """

    result = {}

    for ticker in returns_df.columns:

        cumulative = (1 + returns_df[ticker]).cumprod()

        running_max = cumulative.cummax()

        drawdown = (
            cumulative - running_max
        ) / running_max

        mdd = float(drawdown.min() * 100)

        result[ticker] = {
            "max_drawdown_pct": round(mdd, 2),
            "insight": (
                f"{ticker} fell maximum "
                f"{abs(round(mdd, 2))}% from its peak"
            )
        }

    return result


def var_95(returns_df: pd.DataFrame) -> dict:
    """
    Historical 95% Value-at-Risk.
    """

    result = {}

    for ticker in returns_df.columns:

        var = float(
            np.percentile(
                returns_df[ticker],
                5
            )
        ) * 100

        result[ticker] = {
            "var_95_pct": round(var, 2),
            "insight": (
                f"95% of days, {ticker} "
                f"won't lose more than "
                f"{abs(round(var, 2))}%"
            )
        }

    return result


from typing import cast
import pandas as pd


def correlation_matrix(returns_df: pd.DataFrame) -> dict:

    corr = returns_df.corr(numeric_only=True)

    result = {}

    tickers = list(corr.columns)

    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):

            t1 = tickers[i]
            t2 = tickers[j]

            value = cast(float, corr.loc[t1, t2])

            result[f"{t1}-{t2}"] = {
                "correlation": round(value, 2),
                "insight": (
                    f"{t1} and {t2} move together "
                    f"{value * 100:.2f}% of the time"
                )
            }

    return result

def portfolio_summary(
    returns_df: pd.DataFrame
) -> dict:
    """
    Equal-weight portfolio statistics.
    """

    n_assets = len(
        returns_df.columns
    )

    weights = np.repeat(
        1 / n_assets,
        n_assets
    )

    portfolio_returns = pd.Series(
        returns_df.to_numpy() @ weights,
        index=returns_df.index
    )

    annual_return = (
        float(portfolio_returns.mean())
        * TRADING_DAYS
    )

    annual_volatility = (
        float(portfolio_returns.std())
        * np.sqrt(TRADING_DAYS)
    )

    sharpe = (
        annual_return
        / annual_volatility
        if annual_volatility != 0
        else 0
    )

    return {
        "annual_return_pct":
            round(annual_return * 100, 2),

        "annual_volatility_pct":
            round(annual_volatility * 100, 2),

        "sharpe_ratio":
            round(sharpe, 2),

        "insight":
            f"Equal weight portfolio Sharpe Ratio = {sharpe:.2f}"
    }