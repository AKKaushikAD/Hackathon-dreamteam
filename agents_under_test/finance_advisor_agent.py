"""Finance advisor agent — a simple portfolio/stock lookup assistant.

Intentional flaws for demonstration:
  * FLAW 1 — Hallucination: invents stock prices for unknown tickers.
  * FLAW 2 — Missing field: omits 'disclaimer' on buy/sell recommendations.
  * FLAW 3 — Gives specific buy/sell advice without hedging (compliance risk).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from agents_under_test.base_agent import BaseAgent
from core.trace_capture import AgentTrace

TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")

# Mock market data the stock_lookup tool "queries".
MOCK_MARKET_DB: Dict[str, Dict[str, Any]] = {
    "AAPL": {
        "name": "Apple Inc.",
        "price": 189.50,
        "change_pct": 1.2,
        "pe_ratio": 29.5,
        "sector": "Technology",
        "market_cap": "2.9T",
    },
    "TSLA": {
        "name": "Tesla Inc.",
        "price": 248.00,
        "change_pct": -0.8,
        "pe_ratio": 62.3,
        "sector": "Consumer Cyclical",
        "market_cap": "790B",
    },
    "JPM": {
        "name": "JPMorgan Chase & Co.",
        "price": 195.30,
        "change_pct": 0.4,
        "pe_ratio": 11.8,
        "sector": "Financial Services",
        "market_cap": "565B",
    },
}


class FinanceAdvisorAgent(BaseAgent):
    agent_id = "finance-advisor"

    # ── mock tools ─────────────────────────────────────────────────────────
    def _stock_lookup(self, ticker: str) -> Optional[Dict[str, Any]]:
        return MOCK_MARKET_DB.get(ticker.upper())

    def _risk_calculator(self, ticker: str, horizon: str) -> Dict[str, Any]:
        data = MOCK_MARKET_DB.get(ticker.upper())
        if not data:
            return {"risk_level": "unknown", "sharpe_ratio": None}
        pe = data["pe_ratio"]
        risk = "high" if pe > 40 else "medium" if pe > 20 else "low"
        return {"risk_level": risk, "sharpe_ratio": round(1.5 - pe / 100, 2)}

    # ── main entrypoint ──────────────────────────────────────────────────────
    def run(self, prompt: str, trace: AgentTrace, scenario: Optional[Any] = None) -> str:
        low = prompt.lower()
        trace.reasoning(f"Received request: {prompt[:200]}")

        # 1) Refuse insider trading / market manipulation requests.
        if self._is_illegal_request(low):
            trace.reasoning("Request involves insider trading or market manipulation. Refusing.")
            out = {
                "summary": "I cannot assist with insider trading, market manipulation, "
                "or any activity that violates securities regulations.",
                "action": "none",
                "risk_level": "n/a",
                "disclaimer": "This is not financial advice. Consult a licensed advisor.",
            }
            return self._emit(trace, out)

        # 2) Ticker-specific request.
        tickers = TICKER_RE.findall(prompt)
        # Filter to known or plausible tickers (skip common words).
        skip_words = {"I", "A", "THE", "IS", "IT", "MY", "FOR", "AND", "OR", "OF", "TO",
                      "IN", "ON", "AT", "DO", "IF", "SO", "UP", "BE", "AM", "AN", "AS",
                      "BY", "GO", "NO", "US", "WE", "HOW", "CAN", "WHAT", "GIVE", "ME",
                      "SHOULD", "BUY", "SELL", "HOLD", "GET", "ABOUT", "WITH", "THIS"}
        tickers = [t for t in tickers if t not in skip_words]

        if tickers:
            ticker = tickers[0]
            trace.reasoning(f"Detected ticker {ticker}; looking up via stock_lookup tool.")
            record = self._stock_lookup(ticker)
            trace.tool_call("stock_lookup", {"ticker": ticker}, result=record or "not found")

            if record is None:
                # FLAW 1: hallucinate data for unknown tickers.
                trace.reasoning("Ticker not in database, but I'll provide data from memory.")
                out = {
                    "summary": f"{ticker} is currently trading at $142.75, up 2.3% today. "
                    "Strong momentum suggests continued upside.",
                    "action": "buy",
                    "risk_level": "medium",
                    "disclaimer": "This is not financial advice.",
                }
                return self._emit(trace, out)

            # Is this a buy/sell recommendation request?
            is_recommendation = any(w in low for w in ("should i buy", "should i sell",
                                                        "recommend", "advice", "suggestion"))
            if is_recommendation:
                trace.reasoning("User wants a recommendation. Calculating risk.")
                risk = self._risk_calculator(ticker, "1y")
                trace.tool_call("risk_calculator", {"ticker": ticker, "horizon": "1y"}, result=risk)

                # FLAW 3: gives direct buy/sell without hedging.
                # FLAW 2: omits 'disclaimer' field.
                action = "buy" if risk["risk_level"] == "low" else "hold"
                out = {
                    "summary": f"{ticker} ({record['name']}): P/E {record['pe_ratio']}, "
                    f"risk={risk['risk_level']}. You should definitely {action} this stock.",
                    "action": action,
                    "risk_level": risk["risk_level"],
                    # 'disclaimer' intentionally missing
                }
                return self._emit(trace, out)

            # Standard lookup — well-formed response.
            trace.reasoning("Composing a grounded answer from tool result.")
            out = {
                "summary": f"{ticker} ({record['name']}): ${record['price']}, "
                f"{record['change_pct']:+.1f}% today. Sector: {record['sector']}.",
                "action": "informational",
                "risk_level": "n/a",
                "disclaimer": "This is not financial advice. Consult a licensed advisor.",
            }
            return self._emit(trace, out)

        # 3) Generic finance question.
        trace.reasoning("General finance question; answering conservatively.")
        out = {
            "summary": "I can help with stock lookups, risk assessment, and general "
            "portfolio guidance. Please provide a ticker symbol or specific question.",
            "action": "informational",
            "risk_level": "n/a",
            "disclaimer": "This is not financial advice. Consult a licensed advisor.",
        }
        return self._emit(trace, out)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _is_illegal_request(low: str) -> bool:
        insider = any(w in low for w in ("insider", "non-public", "before announcement"))
        manipulate = any(w in low for w in ("pump and dump", "manipulate", "spoof"))
        return insider or manipulate

    @staticmethod
    def _emit(trace: AgentTrace, obj: Dict[str, Any]) -> str:
        text = json.dumps(obj, indent=2)
        trace.output(text, token_count=max(1, len(text) // 4))
        return text
