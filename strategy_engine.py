class StrategyEngine:

    def __init__(self, llm=None):
        self.llm = llm  # Claude later OR rule-based fallback

    def generate_signal(self, df_slice):

        latest = df_slice.iloc[-1]

        # You will later replace this with Claude output
        signal = {
            "direction": "NO TRADE",
            "confidence": 0.0,
            "exit": False
        }

        return signal