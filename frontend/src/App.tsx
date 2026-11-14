import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useSyncExternalStore } from "react";
import { useCall } from "./api/client";
import { backendState } from "./api/http";
import { Header } from "./components/Header";
import { BackendDown } from "./components/Problem";
import { setBook, useLabels } from "./labels";
import { usePrefs } from "./prefs";
import { href, useRoute } from "./router";
import { tx } from "./text";
import { UiContext } from "./ui";
import { TodayPage } from "./pages/today/TodayPage";

// One chunk per page: klinecharts loads with the stock and picks pages, echarts with the backtest page.
const DataPage = lazy(() => import("./pages/data/DataPage").then((m) => ({ default: m.DataPage })));
const StockPage = lazy(() => import("./pages/stock/StockPage").then((m) => ({ default: m.StockPage })));
const PicksPage = lazy(() => import("./pages/picks/PicksPage").then((m) => ({ default: m.PicksPage })));
const BacktestPage = lazy(() => import("./pages/backtest/BacktestPage").then((m) => ({ default: m.BacktestPage })));
const StrategyPage = lazy(() => import("./pages/strategy/StrategyPage").then((m) => ({ default: m.StrategyPage })));
const SetupPage = lazy(() => import("./pages/setup/SetupPage").then((m) => ({ default: m.SetupPage })));

export function App() {
  const prefs = usePrefs();
  const route = useRoute();
  const down = useSyncExternalStore(backendState.subscribe, backendState.get);
  const client = useQueryClient();
  setBook(useLabels());
  // A data folder without data: every page leads to the first-run setup, which stays open until it is finished.
  const setup = useCall("setup_status", {});
  const needsSetup = setup.data?.needs_setup === true;
  useEffect(() => {
    if (needsSetup && route.page !== "setup") location.replace(href.setup());
  }, [needsSetup, route.page]);
  const retry = () => {
    backendState.reset();
    void client.resetQueries();
  };
  return (
    <UiContext.Provider value={{ dev: prefs.dev, conv: prefs.conv, look: `${prefs.theme}/${prefs.conv}` }}>
      <div className="app">
        <Header page={route.page} {...prefs} />
        {down ? (
          <BackendDown onRetry={retry} />
        ) : (
          <Suspense fallback={<main className="page"><p className="note">{tx.app.loading}</p></main>}>
            {route.page === "setup" || needsSetup ? (
              <SetupPage />
            ) : route.page === "today" ? (
              <TodayPage />
            ) : route.page === "strategy" ? (
              <StrategyPage route={route} />
            ) : route.page === "picks" ? (
              <PicksPage route={route} />
            ) : route.page === "backtest" ? (
              <BacktestPage route={route} />
            ) : route.page === "stock" ? (
              <StockPage key={route.code} route={route} />
            ) : (
              <DataPage key={`${route.code}/${route.date}`} route={route} />
            )}
          </Suspense>
        )}
      </div>
    </UiContext.Provider>
  );
}
