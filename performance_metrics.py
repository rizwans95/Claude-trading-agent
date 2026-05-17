class PerformanceMetrics:

    def calculate_pnl(self, trades):
        pnl = 0
        for t in trades:
            pnl += t["pnl"]
        return pnl

    def win_rate(self, trades):
        if len(trades) == 0:
            return 0

        wins = len([t for t in trades if t["pnl"] > 0])
        return wins / len(trades)

    def avg_rr(self, trades):
        if len(trades) == 0:
            return 0

        gains = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [abs(t["pnl"]) for t in trades if t["pnl"] < 0]

        if len(losses) == 0:
            return 0

        return (sum(gains) / len(gains)) / (sum(losses) / len(losses))