from data_loader import DataLoader
from replay_simulator import ReplaySimulator
from performance_metrics import PerformanceMetrics

class BacktestEngine:

    def __init__(self, strategy_engine, data_path):
        self.strategy = strategy_engine
        self.data_path = data_path

        self.loader = DataLoader(data_path)
        self.metrics = PerformanceMetrics()
        self.simulator = ReplaySimulator(strategy_engine)

    def run_backtest(self):

        df = self.loader.load()
        trades = self.simulator.run(df)

        results = {
            "total_pnl": self.metrics.calculate_pnl(trades),
            "win_rate": self.metrics.win_rate(trades),
            "avg_rr": self.metrics.avg_rr(trades),
            "total_trades": len(trades)
        }

        return results