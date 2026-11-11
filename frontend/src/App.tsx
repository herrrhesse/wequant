import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useSyncExternalStore } from "react";
import { backendState } from "./api/http";
import { Header } from "./components/Header";
import { BackendDown } from "./components/Problem";
import { t } from "./i18n";
import { usePrefs } from "./prefs";
import { useRoute } from "./router";

// One chunk per page: klinecharts loads with the stock page, echarts with a run's page.
const DataPage = lazy(() => import("./pages/DataPage").then((m) => ({ default: m.DataPage })));
const StockPage = lazy(() => import("./pages/StockPage").then((m) => ({ default: m.StockPage })));
const PicksPage = lazy(() => import("./pages/PicksPage").then((m) => ({ default: m.PicksPage })));
const RunPage = lazy(() => import("./pages/runs/RunPage").then((m) => ({ default: m.RunPage })));
const RunsListPage = lazy(() => import("./pages/runs/RunsListPage").then((m) => ({ default: m.RunsListPage })));
const StrategyPage = lazy(() => import("./pages/strategy/StrategyPage").then((m) => ({ default: m.StrategyPage })));

export function App() {
  const prefs = usePrefs();
  const route = useRoute();
  const down = useSyncExternalStore(backendState.subscribe, backendState.get);
  const client = useQueryClient();
  const look = `${prefs.theme}/${prefs.conv}`;
  const retry = () => {
    backendState.reset();
    void client.resetQueries();
  };
  return (
    <div className="app">
      <Header route={route} {...prefs} />
      {down ? (
        <BackendDown onRetry={retry} />
      ) : (
        <Suspense fallback={<main className="page"><p className="note">{t.app.loading}</p></main>}>
          {route.page === "strategy" ? (
            <StrategyPage key={`${route.id}/${route.n}`} id={route.id} n={route.n} />
          ) : route.page === "picks" ? (
            <PicksPage route={route} look={look} />
          ) : route.page === "backtest" ? (
            <RunsListPage cmp={route.cmp} />
          ) : route.page === "run" ? (
            <RunPage key={route.id} id={route.id} day={route.day} look={look} conv={prefs.conv} />
          ) : route.page === "stock" ? (
            <StockPage key={route.code} route={route} look={look} />
          ) : (
            <DataPage key={`${route.code}/${route.date}`} focusCode={route.code} focusDate={route.date} />
          )}
        </Suspense>
      )}
    </div>
  );
}
