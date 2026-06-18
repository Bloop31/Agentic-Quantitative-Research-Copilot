/**
 * components/BacktestPanel.jsx
 * Props:
 *   onRun      (tickers: string[], startDate: string, endDate: string) => void
 *   isLoading  boolean
 */

import { useState, useRef, useEffect, useCallback } from "react";
import styles from "../styles/BacktestPanel.module.css";

const API = process.env.NEXT_PUBLIC_API_URL;

const DEMO = {
  US: [
    { t: "AAPL",  n: "Apple Inc." },
    { t: "MSFT",  n: "Microsoft Corp." },
    { t: "GOOGL", n: "Alphabet Inc." },
  ],
  IN: [
    { t: "RELIANCE.NS", n: "Reliance Industries" },
    { t: "TCS.NS",      n: "Tata Consultancy Services" },
    { t: "INFY.NS",     n: "Infosys Ltd." },
  ],
};

function getToday()      { return new Date().toISOString().split("T")[0]; }
function getOneYearAgo() { return new Date(Date.now() - 365*24*60*60*1000).toISOString().split("T")[0]; }

// Strip .NS / .BO suffix for display
function displayTicker(t) { return t.replace(/\.(NS|BO)$/, ""); }

export default function BacktestPanel({ onRun, isLoading }) {
  const [market, setMarket]       = useState("US");   // "US" | "IN"
  const [mode, setMode]           = useState("demo"); // "demo" | "custom"
  const [query, setQuery]         = useState("");
  const [selected, setSelected]   = useState([]);
  const [dropdown, setDropdown]   = useState([]);
  const [showDrop, setShowDrop]   = useState(false);
  const [searching, setSearching] = useState(false);
  const [startDate, setStartDate] = useState(getOneYearAgo());
  const [endDate, setEndDate]     = useState(getToday());
  const [error, setError]         = useState("");

  const blurTimeout  = useRef(null);
  const debounceRef  = useRef(null);

  useEffect(() => () => {
    clearTimeout(blurTimeout.current);
    clearTimeout(debounceRef.current);
  }, []);

  // Reset selected tickers when market changes
  useEffect(() => {
    setSelected([]);
    setQuery("");
    setShowDrop(false);
    setError("");
  }, [market]);

  const searchTickers = useCallback(async (q) => {
    if (!q.trim()) { setDropdown([]); setShowDrop(false); return; }
    setSearching(true);
    try {
      const res = await fetch(`${API}/search?q=${encodeURIComponent(q)}&market=${market}`);
      if (!res.ok) throw new Error("Search failed");
      const data = await res.json();

      // Yahoo Finance returns quotes array
      const quotes = data.quotes || [];
      const filtered = quotes
        .filter(item => {
          if (market === "IN") return item.exchange === "NSI" || item.exchange === "BSE";
          // US — exclude Indian exchanges
          return item.exchange !== "NSI" && item.exchange !== "BSE" && item.quoteType === "EQUITY";
        })
        .slice(0, 8)
        .map(item => ({
          t: item.symbol,
          n: item.longname || item.shortname || item.symbol,
          s: item.exchange,
        }));

      setDropdown(filtered);
      setShowDrop(filtered.length > 0);
    } catch (e) {
      console.error("Ticker search error:", e);
      setDropdown([]);
      setShowDrop(false);
    } finally {
      setSearching(false);
    }
  }, [market]);

  const handleSearch = (val) => {
    setQuery(val);
    clearTimeout(debounceRef.current);
    if (!val.trim()) { setShowDrop(false); setDropdown([]); return; }
    debounceRef.current = setTimeout(() => searchTickers(val), 300);
  };

  const addTicker = (ticker, name) => {
    if (selected.find(s => s.t === ticker)) return;
    setSelected(prev => [...prev, { t: ticker, n: name }]);
    setQuery("");
    setShowDrop(false);
    setError("");
  };

  const removeTicker = (ticker) => setSelected(prev => prev.filter(s => s.t !== ticker));

  const handleRun = () => {
    setError("");
    if (mode === "demo") {
      const demoTickers = DEMO[market].map(d => d.t);
      onRun(demoTickers, getOneYearAgo(), getToday());
      return;
    }
    if (!selected.length)       { setError("Select at least one ticker."); return; }
    if (!startDate || !endDate) { setError("Both dates are required."); return; }
    if (startDate >= endDate)   { setError("Start date must be before end date."); return; }
    onRun(selected.map(s => s.t), startDate, endDate);
  };

  return (
    <div className={styles.card}>
      <div className={styles.topRow}>
        <div className={styles.label}>BACKTEST</div>

        {/* Market toggle */}
        <div className={styles.marketToggle}>
          <button
            className={`${styles.marketBtn} ${market === "US" ? styles.marketActive : ""}`}
            onClick={() => setMarket("US")}
          >🇺🇸 US</button>
          <button
            className={`${styles.marketBtn} ${market === "IN" ? styles.marketActive : ""}`}
            onClick={() => setMarket("IN")}
          >🇮🇳 IN</button>
        </div>
      </div>

      {/* Mode toggle */}
      <div className={styles.toggle}>
        <button
          className={`${styles.tab} ${mode === "demo" ? styles.active : ""}`}
          onClick={() => { setMode("demo"); setError(""); }}
        >DEMO</button>
        <button
          className={`${styles.tab} ${mode === "custom" ? styles.active : ""}`}
          onClick={() => { setMode("custom"); setError(""); }}
        >CUSTOM</button>
      </div>

      {/* DEMO */}
      {mode === "demo" && (
        <div className={styles.demoSection}>
          <div className={styles.demoChips}>
            {DEMO[market].map(s => (
              <div key={s.t} className={styles.demoChip}>
                <span className={styles.demoTicker}>{displayTicker(s.t)}</span>
                <span className={styles.demoName}>{s.n}</span>
              </div>
            ))}
          </div>
          <div className={styles.demoDates}>
            {getOneYearAgo()} <span className={styles.arrow}>→</span> {getToday()}
          </div>
        </div>
      )}

      {/* CUSTOM */}
      {mode === "custom" && (
        <div className={styles.customSection}>

          {/* Search */}
          <div className={styles.searchBox}>
            <span className={styles.searchIcon}>⌕</span>
            <input
              className={styles.searchInput}
              value={query}
              onChange={e => handleSearch(e.target.value)}
              onFocus={() => {
                clearTimeout(blurTimeout.current);
                if (query.trim() && dropdown.length > 0) setShowDrop(true);
              }}
              onBlur={() => {
                blurTimeout.current = setTimeout(() => setShowDrop(false), 150);
              }}
              onKeyDown={e => e.key === "Escape" && setShowDrop(false)}
              placeholder={market === "IN" ? "Search NSE/BSE stock or company…" : "Search ticker or company name…"}
              disabled={isLoading}
              autoComplete="off"
            />
            {searching && <span className={styles.searchSpinner}>⟳</span>}

            {showDrop && (
              <div className={styles.dropdown}>
                {dropdown.length === 0 ? (
                  <div className={styles.dropEmpty}>No results found</div>
                ) : dropdown.map(s => (
                  <div
                    key={s.t}
                    className={`${styles.dropItem} ${selected.find(x => x.t === s.t) ? styles.dropSelected : ""}`}
                    onMouseDown={() => addTicker(s.t, s.n)}
                  >
                    <div>
                      <div className={styles.dropTicker}>{displayTicker(s.t)}</div>
                      <div className={styles.dropName}>{s.n}</div>
                    </div>
                    <div className={styles.dropSector}>{s.s}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Tags */}
          <div className={styles.tagsLabel}>SELECTED TICKERS</div>
          <div className={styles.tags}>
            {selected.length === 0
              ? <span className={styles.tagsEmpty}>None selected</span>
              : selected.map(s => (
                  <div key={s.t} className={styles.tag}>
                    {displayTicker(s.t)}
                    <span className={styles.tagRemove} onClick={() => removeTicker(s.t)}>×</span>
                  </div>
                ))
            }
          </div>

          {/* Dates */}
          <div className={styles.dateRow}>
            <div className={styles.dateField}>
              <label className={styles.dateLabel}>FROM</label>
              <input
                className={styles.dateInput}
                type="date"
                value={startDate}
                onChange={e => setStartDate(e.target.value)}
                disabled={isLoading}
              />
            </div>
            <div className={styles.dateField}>
              <label className={styles.dateLabel}>TO</label>
              <input
                className={styles.dateInput}
                type="date"
                value={endDate}
                onChange={e => setEndDate(e.target.value)}
                disabled={isLoading}
              />
            </div>
          </div>
        </div>
      )}

      {error && <div className={styles.error}>{error}</div>}

      <button className={styles.runBtn} onClick={handleRun} disabled={isLoading}>
        {isLoading ? "RUNNING..." : "RUN BACKTEST →"}
      </button>
    </div>
  );
}
