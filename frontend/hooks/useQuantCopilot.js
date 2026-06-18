/**
 * hooks/useQuantCopilot.js
 */

import { useState, useRef, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL;

export function useQuantCopilot() {
  const [events, setEvents]               = useState([]);
  const [equityData, setEquityData]       = useState([]);
  const [metrics, setMetrics]             = useState(null);
  const [tickers, setTickers]             = useState([]);
  const [isLoading, setIsLoading]         = useState(false);  // WebSocket query
  const [btLoading, setBtLoading]         = useState(false);  // Backtest independently
  const [error, setError]                 = useState(null);

  const wsRef          = useRef(null);
  const runBacktestRef = useRef(null);

  const runBacktest = useCallback(async (tickerList, startDate, endDate) => {
    setError(null);
    setBtLoading(true);
    try {
      const res = await fetch(`${API}/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers:    tickerList,
          start_date: startDate,
          end_date:   endDate,
        }),
      });

      if (!res.ok) throw new Error(`Server error: ${res.status}`);

      const data = await res.json();
      setEquityData(data.equity_curve || []);
      setMetrics(data.metrics || null);
      setTickers(data.tickers || tickerList);
      return data;

    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBtLoading(false);
    }
  }, []);

  runBacktestRef.current = runBacktest;

  const submitQuery = useCallback((query) => {
    setEvents([]);
    //setEquityData([]); (turn all 3 on if i wanna reset evertthing with each search)
    //setMetrics(null);
    //setTickers([]);
    setError(null);
    setIsLoading(true);

    if (wsRef.current) wsRef.current.close();

    const ws = new WebSocket(`${API.replace("http", "ws")}/ws/query`);
    wsRef.current = ws;

    ws.onopen = () => ws.send(JSON.stringify({ query }));

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        let normalised = null;

        if (event.type === "tool_call") {
          normalised = { type: "tool_call", content: `Calling ${event.tool}` };
        } else if (event.type === "tool_done") {
          normalised = { type: "tool_done", content: `${event.tool} completed` };
        } else if (event.type === "answer") {
          normalised = { type: "answer", content: event.data?.answer || JSON.stringify(event.data) };
        } else if (event.type === "cache_hit") {
          normalised = { type: "tool_done", content: "Cache hit — returning cached result" };
        } else if (event.type === "error") {
          normalised = { type: "answer", content: `Error: ${event.message}` };
        } else if (event.type === "start") {
          normalised = { type: "tool_call", content: event.message };
        }

        if (normalised) setEvents(prev => [...prev, normalised]);

        if (event.type === "answer" || event.type === "error") {
          setIsLoading(false);
          ws.close();

          if (event.type === "answer") {
            const tickerList = event.data?.tickers || [];
            const today      = new Date().toISOString().split("T")[0];
            const oneYearAgo = new Date(Date.now() - 365*24*60*60*1000).toISOString().split("T")[0];
            if (tickerList.length > 0) {
              runBacktestRef.current(tickerList, oneYearAgo, today);
            }
          }
        }

      } catch (e) {
        console.error("WebSocket parse error:", e);
      }
    };

    ws.onerror = () => {
      setError("Connection to backend failed. Is the server running?");
      setIsLoading(false);
    };

    ws.onclose = () => setIsLoading(false);

  }, []);

  return {
    events, equityData, metrics, tickers,
    isLoading, btLoading,
    error, submitQuery, runBacktest,
  };
}
