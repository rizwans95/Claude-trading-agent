class ReplaySimulator:

    def __init__(self, strategy_engine):
        self.strategy = strategy_engine
        self.trades = []

    def run(self, df):
        position = None
        entry_price = 0

        for i in range(len(df)):

            candle = df.iloc[i]

            signal = self.strategy.generate_signal(df.iloc[:i+1])

            if position is None:

                if signal["direction"] == "LONG":
                    position = "LONG"
                    entry_price = candle["close"]

                elif signal["direction"] == "SHORT":
                    position = "SHORT"
                    entry_price = candle["close"]

            else:

                exit_signal = signal["exit"]

                if exit_signal:

                    pnl = 0

                    if position == "LONG":
                        pnl = candle["close"] - entry_price

                    if position == "SHORT":
                        pnl = entry_price - candle["close"]

                    self.trades.append({
                        "entry": entry_price,
                        "exit": candle["close"],
                        "pnl": pnl
                    })

                    position = None

        return self.trades