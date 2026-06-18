# quant/backtest.py

from __future__ import annotations

from typing import Dict, Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252


class BacktestError(Exception):
    pass


class Backtester:
    def __init__(self, annualization_factor: int = TRADING_DAYS):
        self.annualization_factor = annualization_factor
    def _validate_prices(self, prices: pd.DataFrame) -> pd.DataFrame:

        if not isinstance(prices, pd.DataFrame):
            raise BacktestError("prices must be a pandas DataFrame")

        if prices.empty:
            raise BacktestError("price dataframe is empty")

        if not isinstance(prices.index, pd.DatetimeIndex):
            raise BacktestError("index must be DatetimeIndex")

        prices = prices.sort_index()

        prices = prices.ffill()

        if prices.isna().any().any():
            raise BacktestError(
                "NaNs remain after forward fill"
            )

        if (prices <= 0).any().any():
            raise BacktestError(
                "prices must be strictly positive"
            )

        return prices
    
    def compute_log_returns(
        self,
        prices: pd.DataFrame
    ) -> pd.DataFrame:

        prices = self._validate_prices(prices)

        log_returns = np.log(prices / prices.shift(1))

        return log_returns.dropna()

    def equal_weight_portfolio(
        self,
        returns: pd.DataFrame
    ) -> pd.Series:

        n_assets = len(returns.columns)

        if n_assets == 0:
            raise BacktestError("no assets present")

        weights = np.full(n_assets, 1 / n_assets)

        portfolio_returns = returns @ weights

        return portfolio_returns


    def cumulative_return(
        self,
        portfolio_returns: pd.Series
    ) -> float:

        return float(
            np.exp(portfolio_returns.sum()) - 1
        )

    def annualized_return(
        self,
        portfolio_returns: pd.Series
    ) -> float:

        mean_daily = portfolio_returns.mean()

        return float(
            np.exp(mean_daily * self.annualization_factor) - 1
        )

    def annualized_volatility(
        self,
        portfolio_returns: pd.Series
    ) -> float:

        return float(
            portfolio_returns.std(ddof=1)
            * np.sqrt(self.annualization_factor)
        )

    def sharpe_ratio(
        self,
        portfolio_returns: pd.Series,
        risk_free_rate: float = 0.0
    ) -> float:

        ann_return = self.annualized_return(
            portfolio_returns
        )

        ann_vol = self.annualized_volatility(
            portfolio_returns
        )

        if ann_vol == 0:
            return 0.0

        return float(
            (ann_return - risk_free_rate)
            / ann_vol
        )

    def max_drawdown(
        self,
        portfolio_returns: pd.Series
    ) -> float:

        equity_curve = np.exp(
            portfolio_returns.cumsum()
        )

        running_max = equity_curve.cummax()

        drawdown = (
            equity_curve - running_max
        ) / running_max

        return float(drawdown.min())

    def equity_curve(
        self,
        portfolio_returns: pd.Series,
        initial_capital: float = 1.0
    ) -> pd.Series:

        equity = np.exp(portfolio_returns.cumsum())

        return pd.Series(
            initial_capital * equity,
            index=portfolio_returns.index
        )
        
    def detect_market(tickers):
        if all(t.endswith(".NS") or t.endswith(".BO") for t in tickers):
            return "IN"
        if all(not t.endswith(".NS") and not t.endswith(".BO") for t in tickers):
            return "US"
        return "MIXED"  # this is error, either indian or us cuz we gonna us the ticker from here and put in url to fetch

    def run(
        self,
        prices: pd.DataFrame,
        risk_free_rate: float = 0.0
    ) -> Dict[str, Any]:

        returns = self.compute_log_returns(prices)

        portfolio_returns = self.equal_weight_portfolio(
            returns
        )

        equity = self.equity_curve(
            portfolio_returns
        )

        result = {
            "metrics": {
                "cumulative_return":
                    self.cumulative_return(
                        portfolio_returns
                    ),

                "annualized_return":
                    self.annualized_return(
                        portfolio_returns
                    ),

                "annualized_volatility":
                    self.annualized_volatility(
                        portfolio_returns
                    ),

                "sharpe_ratio":
                    self.sharpe_ratio(
                        portfolio_returns,
                        risk_free_rate
                    ),

                "max_drawdown":
                    self.max_drawdown(
                        portfolio_returns
                    )
            },

            "equity_curve": [
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "value": float(val)
                }
                for idx, val in equity.items()
            ]
        }

        return result