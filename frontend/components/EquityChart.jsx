/**
 * components/EquityChart.jsx
 * Props:
 *   data     { date: string, value: number }[]
 *   metrics  { cumulative_return, annualized_return, annualized_volatility, sharpe_ratio, max_drawdown } | null
 *   tickers  string[]
 */

import { useEffect, useRef, useState } from "react";
import styles from "../styles/EquityChart.module.css";

function fmt(val, type) {
  if (val === undefined || val === null) return "—";
  if (type === "pct") return `${(val * 100).toFixed(1)}%`;
  if (type === "num") return val.toFixed(2);
  return val;
}

function fmtMoney(val) {
  if (!val && val !== 0) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(val);
}

export default function EquityChart({ data, metrics, tickers }) {
  const chartRef = useRef(null);
  const plotRef  = useRef(null);
  const [investment, setInvestment] = useState("10000");

  const hasData = data && data.length > 0;
  const finalMultiplier = hasData ? data[data.length - 1].value : null;
  const investedAmount  = parseFloat(investment.replace(/,/g, "")) || 0;
  const endAmount       = finalMultiplier ? investedAmount * finalMultiplier : null;
  const profit          = endAmount !== null ? endAmount - investedAmount : null;
  const isProfit        = profit >= 0;

  useEffect(() => {
    const container = chartRef.current;
    if (!container) return;

    import("plotly.js-dist-min").then((Plotly) => {
      if (!hasData) {
        if (plotRef.current) { Plotly.purge(container); plotRef.current = false; }
        return;
      }

      const x = data.map((d) => d.date);
      const y = data.map((d) => d.value);

      const trace = {
        x, y,
        type: "scatter", mode: "lines",
        line: { color: "#EF9F27", width: 1.5 },
        fill: "tozeroy",
        fillcolor: "rgba(239,159,39,0.07)",
      };

      const layout = {
        paper_bgcolor: "transparent",
        plot_bgcolor:  "transparent",
        margin: { t: 10, r: 10, b: 40, l: 50 },
        xaxis: {
          color: "#6B7094",
          tickfont:  { family: "JetBrains Mono", size: 9 },
          gridcolor: "rgba(255,255,255,0.04)",
          showline:  false,
        },
        yaxis: {
          color: "#6B7094",
          tickfont:   { family: "JetBrains Mono", size: 9 },
          gridcolor:  "rgba(255,255,255,0.04)",
          tickformat: ".3f",
          showline:   false,
        },
        showlegend: false,
      };

      if (plotRef.current) {
        Plotly.react(container, [trace], layout, { displayModeBar: false, responsive: true });
      } else {
        Plotly.newPlot(container, [trace], layout, { displayModeBar: false, responsive: true });
        plotRef.current = true;
      }
    });
  }, [data]);

  return (
    <div className={styles.card}>
      {/* Header */}
      <div className={styles.header}>
        <div className={styles.label}>PORTFOLIO EQUITY CURVE</div>
        {tickers && tickers.length > 0 && (
          <div className={styles.tickers}>
            {tickers.map(t => <span key={t} className={styles.ticker}>{t}</span>)}
          </div>
        )}
      </div>

      {!hasData ? (
        <div className={styles.empty}>Run a backtest to see the equity curve</div>
      ) : (
        <>
          {/* Chart */}
          <div ref={chartRef} style={{ width: "100%", height: 220 }} />

          {/* Metrics */}
          {metrics && (
            <div className={styles.metrics}>
              <div className={styles.metric}>
                <div className={styles.metricLabel}>TOTAL RETURN</div>
                <div className={`${styles.metricVal} ${metrics.cumulative_return >= 0 ? styles.green : styles.red}`}>
                  {metrics.cumulative_return >= 0 ? "+" : ""}{fmt(metrics.cumulative_return, "pct")}
                </div>
              </div>
              <div className={styles.metric}>
                <div className={styles.metricLabel}>ANN. RETURN</div>
                <div className={`${styles.metricVal} ${metrics.annualized_return >= 0 ? styles.green : styles.red}`}>
                  {metrics.annualized_return >= 0 ? "+" : ""}{fmt(metrics.annualized_return, "pct")}
                </div>
              </div>
              <div className={styles.metric}>
                <div className={styles.metricLabel}>SHARPE RATIO</div>
                <div className={styles.metricVal}>{fmt(metrics.sharpe_ratio, "num")}</div>
              </div>
              <div className={styles.metric}>
                <div className={styles.metricLabel}>VOLATILITY</div>
                <div className={styles.metricVal}>{fmt(metrics.annualized_volatility, "pct")}</div>
              </div>
              <div className={styles.metric}>
                <div className={styles.metricLabel}>MAX DRAWDOWN</div>
                <div className={`${styles.metricVal} ${styles.red}`}>{fmt(metrics.max_drawdown, "pct")}</div>
              </div>
            </div>
          )}

          {/* Investment simulator */}
          <div className={styles.simulator}>
            <div className={styles.simLabel}>INVESTMENT SIMULATOR</div>
            <div className={styles.simRow}>
              <div className={styles.simInputWrap}>
                <span className={styles.simPrefix}>$</span>
                <input
                  className={styles.simInput}
                  type="number"
                  min="0"
                  value={investment}
                  onChange={e => setInvestment(e.target.value)}
                  placeholder="10000"
                />
              </div>
              <div className={styles.simArrow}>→</div>
              <div className={styles.simResult}>
                <div className={`${styles.simEndAmount} ${isProfit ? styles.green : styles.red}`}>
                  {fmtMoney(endAmount)}
                </div>
                <div className={`${styles.simProfit} ${isProfit ? styles.green : styles.red}`}>
                  {isProfit ? "+" : ""}{fmtMoney(profit)} ({isProfit ? "+" : ""}{fmt(metrics?.cumulative_return, "pct")})
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
