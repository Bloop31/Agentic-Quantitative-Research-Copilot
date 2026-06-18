/**
 * pages/chat.jsx
 */

import Head from "next/head";
import dynamic from "next/dynamic";
import Link from "next/link";
import QueryInput    from "../components/QueryInput";
import LiveFeed      from "../components/LiveFeed";
import EquityChart   from "../components/EquityChart";
import BacktestPanel from "../components/BacktestPanel";
import { useQuantCopilot } from "../hooks/useQuantCopilot";
import styles from "../styles/Chat.module.css";

const GridBackground = dynamic(() => import("../components/GridBackground"), { ssr: false });

export default function Chat() {
  const {
    events, equityData, metrics, tickers,
    isLoading, btLoading,
    error, submitQuery, runBacktest,
  } = useQuantCopilot();

  return (
    <>
      <Head>
        <title>Quant Copilot — Terminal</title>
        <meta name="description" content="Agentic Quant Research Terminal" />
      </Head>

      <GridBackground />

      <div className={styles.page}>
        <header className={styles.header}>
          <Link href="/" className={styles.logoWrap}>
            <div className={styles.logoMark} />
            <div>
              <div className={styles.logoName}>QUANT COPILOT</div>
              <div className={styles.logoSub}>AGENTIC RESEARCH TERMINAL</div>
            </div>
          </Link>
          <div className={styles.headerRight}>
            <div className={styles.statusDot} />
            <span className={styles.statusTxt}>CONNECTED</span>
          </div>
        </header>

        <main className={styles.main}>
          <div className={styles.container}>

            {/* isLoading here — query should block while WS is open */}
            <QueryInput onSubmit={submitQuery} isLoading={isLoading} />

            {/* btLoading here — backtest has its own independent loading state */}
            <BacktestPanel onRun={runBacktest} isLoading={btLoading} />

            {error && <div className={styles.error}>{error}</div>}

            <div className={styles.grid}>
              <LiveFeed events={events} isLoading={isLoading} />
              <EquityChart data={equityData} metrics={metrics} tickers={tickers} />
            </div>

          </div>
        </main>
      </div>
    </>
  );
}
