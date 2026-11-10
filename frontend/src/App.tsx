import { Header } from "./components/Header";
import { SyntheticBanner } from "./components/SyntheticBanner";
import { DataPage } from "./pages/DataPage";
import { StockPage } from "./pages/StockPage";
import { PicksPage } from "./pages/PicksPage";
import { RunPage } from "./pages/runs/RunPage";
import { RunsListPage } from "./pages/runs/RunsListPage";
import { StrategyPage } from "./pages/strategy/StrategyPage";
import { usePrefs } from "./prefs";
import { useRoute } from "./router";

export function App() {
  const prefs = usePrefs();
  const route = useRoute();
  return (
    <div className="app">
      <Header route={route} {...prefs} />
      <SyntheticBanner />
      {route.page === "strategy" ? (
        <StrategyPage />
      ) : route.page === "picks" ? (
        <PicksPage key={route.screen ?? ""} name={route.screen} look={`${prefs.theme}/${prefs.conv}`} />
      ) : route.page === "backtest" ? (
        <RunsListPage />
      ) : route.page === "run" ? (
        <RunPage key={route.id} id={route.id} day={route.day} look={`${prefs.theme}/${prefs.conv}`} conv={prefs.conv} />
      ) : route.page === "stock" ? (
        <StockPage key={`${route.code}/${route.date}`} code={route.code} date={route.date} theme={prefs.theme} conv={prefs.conv} />
      ) : (
        <DataPage key={`${route.code}/${route.date}`} focusCode={route.code} focusDate={route.date} />
      )}
    </div>
  );
}
