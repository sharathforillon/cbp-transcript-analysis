"""
llm_client.py — Abstract LLM client with Anthropic + Azure OpenAI implementations.

Usage:
  from src.coding.llm_client import get_llm_client
  client = get_llm_client()
  result = client.classify(prompt="...", system="...")

Switch implementations by setting `client: anthropic | azure_openai` in llm_config.yaml.
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
LLM_CONFIG_PATH = REPO_ROOT / "config" / "llm_config.yaml"


# ---------------------------------------------------------------------------
# ABSTRACT BASE
# ---------------------------------------------------------------------------

class LLMClient(ABC):
    """Abstract base for all LLM classification clients."""

    @abstractmethod
    def classify(self, prompt: str, system: str, max_tokens: int = 1024) -> dict:
        """
        Run a classification prompt and return parsed JSON.

        Args:
            prompt: The user-facing prompt (task + input text).
            system: The system/role prompt.
            max_tokens: Maximum tokens to generate.

        Returns:
            dict: Parsed JSON from the LLM response.
                  On parse failure: {"error": "...", "raw": "..."}.
        """
        ...

    @abstractmethod
    def estimate_cost(self, n_calls: int, avg_input_tokens: int, avg_output_tokens: int = 150) -> float:
        """Estimate total cost in USD for n_calls with given token averages."""
        ...

    def _parse_json_response(self, raw: str) -> dict:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("JSON parse failed: %s. Raw: %s...", e, raw[:200])
            return {"error": str(e), "raw": raw[:500]}


# ---------------------------------------------------------------------------
# ANTHROPIC CLIENT
# ---------------------------------------------------------------------------

class AnthropicClient(LLMClient):
    """
    Calls Anthropic's API directly.
    Reads ANTHROPIC_API_KEY from environment.
    """

    def __init__(self, model: str = "claude-3-5-haiku-20241022", temperature: float = 0.0):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Add it to .env: ANTHROPIC_API_KEY=sk-ant-..."
            )
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")

        self.model = model
        self.temperature = temperature
        self._cost_rates = _load_cost_rates("anthropic", model)
        self._usage_log: list[dict] = []

    def classify(self, prompt: str, system: str, max_tokens: int = 1024) -> dict:
        start = time.time()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=self.temperature,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_s": round(time.time() - start, 2),
            }
            self._usage_log.append(usage)

            return self._parse_json_response(raw)

        except Exception as e:
            logger.error("Anthropic API call failed: %s", e)
            return {"error": str(e)}

    def estimate_cost(self, n_calls: int, avg_input_tokens: int, avg_output_tokens: int = 150) -> float:
        input_cost = (n_calls * avg_input_tokens / 1_000_000) * self._cost_rates.get("input_per_million", 0.80)
        output_cost = (n_calls * avg_output_tokens / 1_000_000) * self._cost_rates.get("output_per_million", 4.00)
        return round(input_cost + output_cost, 4)

    @property
    def total_tokens_used(self) -> dict:
        return {
            "input": sum(u["input_tokens"] for u in self._usage_log),
            "output": sum(u["output_tokens"] for u in self._usage_log),
            "calls": len(self._usage_log),
        }


# ---------------------------------------------------------------------------
# AZURE OPENAI CLIENT (STUB)
# ---------------------------------------------------------------------------

class AzureOpenAIClient(LLMClient):
    """
    Azure OpenAI PTU client — stub for dev team to fill in.

    ⚠️  IMPORTANT FOR BANK DEV TEAM ⚠️
    Before running this client on real transcript data, you MUST:
    1. Submit an Azure Content Filter Exception Request for banking/financial content.
       URL: https://aka.ms/oai/requestaccess → "Modify content filters"
       Category: "Financial services / banking"
       Without this, transcript content (fraud language, complaints, financial distress)
       will trigger content filter errors on 5-15% of requests.

    2. Fill in the TODO items below with your PTU endpoint details.

    3. Test with: python -m src.coding.llm_client --test-azure
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        deployment_name: Optional[str] = None,
        api_version: str = "2024-12-01-preview",
        temperature: float = 0.0,
    ):
        # TODO: Fill in your Azure OpenAI PTU endpoint details
        self.endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        self.deployment_name = deployment_name or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        api_key = os.environ.get("AZURE_OPENAI_KEY")
        self.api_version = api_version
        self.temperature = temperature

        if not self.endpoint:
            raise EnvironmentError(
                "AZURE_OPENAI_ENDPOINT not set.\n"
                "Set it in .env: AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/\n"
                "TODO: Replace with your actual PTU endpoint URL."
            )
        if not self.deployment_name:
            raise EnvironmentError(
                "AZURE_OPENAI_DEPLOYMENT not set.\n"
                "Set it in .env: AZURE_OPENAI_DEPLOYMENT=your-deployment-name\n"
                "TODO: Replace with your actual deployment name (e.g. gpt-4o-cbp-ptu)."
            )

        try:
            from openai import AzureOpenAI  # type: ignore
            self._client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=api_key,
                api_version=self.api_version,
            )
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        self._cost_rates = _load_cost_rates("azure_openai", "gpt-4o")
        self._usage_log: list[dict] = []

    def classify(self, prompt: str, system: str, max_tokens: int = 1024) -> dict:
        start = time.time()
        try:
            response = self._client.chat.completions.create(
                model=self.deployment_name,
                temperature=self.temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            raw = response.choices[0].message.content or ""
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
                "latency_s": round(time.time() - start, 2),
            }
            self._usage_log.append(usage)
            return self._parse_json_response(raw)

        except Exception as e:
            # TODO: Add specific handling for Azure content filter errors:
            # from openai import BadRequestError
            # if isinstance(e, BadRequestError) and "content_filter" in str(e):
            #     logger.warning("Content filter triggered — request a filter exception for banking content")
            #     return {"error": "content_filter", "raw": str(e)}
            logger.error("Azure OpenAI call failed: %s", e)
            return {"error": str(e)}

    def estimate_cost(self, n_calls: int, avg_input_tokens: int, avg_output_tokens: int = 150) -> float:
        # TODO: For PTU deployments, cost model is different (capacity reservation).
        # Adjust this calculation based on your PTU contract.
        input_cost = (n_calls * avg_input_tokens / 1_000_000) * self._cost_rates.get("input_per_million", 2.50)
        output_cost = (n_calls * avg_output_tokens / 1_000_000) * self._cost_rates.get("output_per_million", 10.00)
        return round(input_cost + output_cost, 4)

    @property
    def total_tokens_used(self) -> dict:
        return {
            "input": sum(u["input_tokens"] for u in self._usage_log),
            "output": sum(u["output_tokens"] for u in self._usage_log),
            "calls": len(self._usage_log),
        }


# ---------------------------------------------------------------------------
# MOCK CLIENT (for testing without real API calls)
# ---------------------------------------------------------------------------

class MockLLMClient(LLMClient):
    """
    Returns deterministic mock responses for testing and dry-run mode.
    Does NOT call any external API.
    """

    JOURNEY_IDS = [
        "ACCT_001", "ACCT_002", "ACCT_003", "ACCT_004", "CARD_001", "CARD_004",
        "CARD_011", "DIGI_001", "DIGI_004", "LOAN_001", "REMI_001", "REMI_003",
    ]
    TRANSFER_REASONS = [
        "bot_fallback_exhausted", "customer_demanded_human", "policy_routed",
        "bot_has_data_communicates_poorly", "authentication_friction",
    ]

    def __init__(self):
        self._call_count = 0

    def classify(self, prompt: str, system: str, max_tokens: int = 1024) -> dict:
        self._call_count += 1
        import random

        # Detect prompt type from keywords in EITHER prompt or system
        combined = (prompt + " " + system).lower()
        if "classify a chatbot conversation" in combined or "journey_id" in combined or "90 predefined banking" in combined:
            jid = random.choice(self.JOURNEY_IDS)
            return {
                "journey_id": jid,
                "journey_name": f"Mock Journey {jid}",
                "confidence": random.randint(3, 5),
                "reasoning": "Mock: classified based on keyword matching.",
            }
        elif "transfer_reason_code" in combined or "classify why" in combined or "escalated to a human" in combined:
            return {
                "transfer_reason_code": random.choice(self.TRANSFER_REASONS),
                "confidence": random.randint(2, 5),
                "reasoning": "Mock: transfer reason assigned deterministically.",
                "transfer_point": "Mid-session after NLU failure.",
                "agent_resolution": "Agent resolved the query directly.",
            }
        elif "customer_intent" in combined or "deep-dive analysis" in combined or "structured deep" in combined:
            return {
                "session_id": None,
                "journey_id": "ACCT_004",
                "customer_intent": "Customer wanted to close their account.",
                "bot_failure_point": "Bot gave vague response after retrieving account data.",
                "actual_blocker": "Pending merchant hold not clearly explained.",
                "customer_confusion_signal": "Customer said 'my balance is zero'.",
                "agent_action": "Agent explained merchant hold clearly.",
                "agent_resolution_status": "resolved",
                "what_would_have_prevented_escalation": "improve_response_copy",
                "prevention_detail": "Bot should have named the merchant and amount.",
                "confidence": 4,
                "reasoning": "Mock deep-code analysis.",
            }
        elif "recommended_remediations" in combined or "synthesize" in combined or "remediation" in combined:
            return {
                "journey_id": "MOCK",
                "journey_name": "Mock Journey",
                "sessions_analyzed": 10,
                "primary_failure_mode": "bot_has_data_communicates_poorly",
                "failure_mode_distribution": {"bot_has_data_communicates_poorly": 6, "bot_fallback_exhausted": 4},
                "recommended_remediations": [
                    {"remediation_type": "content_quality_improvement", "priority": 1, "rationale": "Mock", "estimated_impact": "high", "implementation_complexity": "low"}
                ],
                "knowledge_transfer_opportunity": "Agent frequently explains merchant holds.",
                "recommended_faq_topics": ["Account closure blockers", "Merchant authorization holds"],
                "confidence": 3,
                "reasoning": "Mock remediation analysis.",
            }
        else:
            return {"result": "mock", "confidence": 3, "reasoning": "Mock response."}

    def estimate_cost(self, n_calls: int, avg_input_tokens: int, avg_output_tokens: int = 150) -> float:
        return 0.0

    @property
    def total_tokens_used(self) -> dict:
        return {"input": 0, "output": 0, "calls": self._call_count}


# ---------------------------------------------------------------------------
# FACTORY
# ---------------------------------------------------------------------------

def get_llm_client(config_path: Optional[Path] = None) -> LLMClient:
    """
    Return the configured LLM client based on llm_config.yaml.

    Also checks MOCK_LLM env var: if set to 'true', always returns MockLLMClient.
    """
    if os.environ.get("MOCK_LLM", "").lower() == "true":
        logger.info("MOCK_LLM=true — using MockLLMClient (no API calls)")
        return MockLLMClient()

    cfg_path = config_path or LLM_CONFIG_PATH
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("llm_config.yaml not found, defaulting to MockLLMClient")
        return MockLLMClient()

    client_type = cfg.get("client", "anthropic").lower()

    if client_type == "anthropic":
        model = cfg.get("anthropic", {}).get("model", "claude-3-5-haiku-20241022")
        temperature = cfg.get("anthropic", {}).get("temperature", 0.0)
        return AnthropicClient(model=model, temperature=temperature)

    elif client_type == "azure_openai":
        az_cfg = cfg.get("azure_openai", {})
        return AzureOpenAIClient(
            endpoint=az_cfg.get("endpoint") or None,
            deployment_name=az_cfg.get("deployment_name") or None,
            api_version=az_cfg.get("api_version", "2024-12-01-preview"),
            temperature=az_cfg.get("temperature", 0.0),
        )

    else:
        logger.warning("Unknown client type '%s', defaulting to MockLLMClient", client_type)
        return MockLLMClient()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _load_cost_rates(provider: str, model: str) -> dict:
    """Load cost rates from llm_config.yaml."""
    try:
        with open(LLM_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        rates = cfg.get("cost_rates", {}).get(provider, {}).get(model, {})
        return rates
    except Exception:
        return {"input_per_million": 1.0, "output_per_million": 5.0}
