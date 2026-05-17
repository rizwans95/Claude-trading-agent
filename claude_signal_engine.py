import anthropic
import os
import json

class ClaudeSignalEngine:

    def __init__(self):
        self.client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )

    def generate_signal(self, market_data: dict):

        prompt = f"""
You are a professional systematic trading decision engine.

Return ONLY valid JSON.

MARKET DATA:
{json.dumps(market_data, indent=2)}

RULES:
- Decide: LONG, SHORT, or NO TRADE
- Confidence: 0 to 10
- Must consider trend, momentum, structure, volatility, and volume
- Be strict: avoid overtrading

OUTPUT FORMAT:
{{
  "direction": "",
  "confidence": 0,
  "reason": "",
  "risk": ""
}}
"""

        response = self.client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=300,
            temperature=0.2,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return json.loads(response.content[0].text)