import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class CostProfiles:
    """Manages provider rate cards loaded from a central config/provider_costs.yaml file."""
    
    def __init__(self, config_path: Optional[str] = None) -> None:
        if not config_path:
            # Resolve relative to root
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "provider_costs.yaml")
        
        self.config_path = config_path
        self.costs: Dict[str, Any] = {}
        self.load_costs()

    def load_costs(self) -> None:
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    self.costs = yaml.safe_load(f) or {}
                logger.info(f"Loaded provider costs from {self.config_path}")
            else:
                logger.warning(f"Provider costs file not found at {self.config_path}. Using empty profiles.")
                self.costs = {}
        except Exception as e:
            logger.error(f"Failed to load provider costs: {e}")
            self.costs = {}

    def get_telephony_cost_per_minute(self, provider_name: str) -> float:
        telephony = self.costs.get("telephony", {})
        prov = telephony.get(provider_name.lower(), {})
        return float(prov.get("cost_per_minute", 0.01))

    def get_stt_cost_per_minute(self, provider_name: str) -> float:
        stt = self.costs.get("stt", {})
        prov = stt.get(provider_name.lower(), {})
        cost_sec = float(prov.get("cost_per_second", 0.0))
        if cost_sec == 0.0 and provider_name.lower() == "deepgram":
            cost_sec = 0.000072
        return cost_sec * 60.0

    def get_tts_cost_per_minute(self, provider_name: str, avg_chars_per_minute: float = 900.0) -> float:
        tts = self.costs.get("tts", {})
        prov = tts.get(provider_name.lower(), {})
        cost_char = float(prov.get("cost_per_character", 0.0))
        if cost_char == 0.0 and provider_name.lower() == "elevenlabs":
            cost_char = 0.0003
        return cost_char * avg_chars_per_minute

    def get_llm_cost_per_1k_tokens(self, provider_name: str, model_name: str = "", is_output: bool = False) -> float:
        llm = self.costs.get("llm", {})
        prov = llm.get(provider_name.lower(), {})
        
        # Check model specific costs if present (nested)
        if model_name:
            model_key = model_name.lower()
            # Try to match substring (e.g. gpt-4o-mini inside model name)
            matched_key = None
            for key in prov.keys():
                if key in model_key or model_key in key:
                    matched_key = key
                    break
            if matched_key and isinstance(prov[matched_key], dict):
                sub_prov = prov[matched_key]
                cost_1m = sub_prov.get("output_cost_per_1m_tokens" if is_output else "input_cost_per_1m_tokens")
                if cost_1m is not None:
                    return float(cost_1m) / 1000.0

        # Fallback to provider general or default key
        cost_1m = None
        if "default" in prov and isinstance(prov["default"], dict):
            cost_1m = prov["default"].get("output_cost_per_1m_tokens" if is_output else "input_cost_per_1m_tokens")
        if cost_1m is None:
            cost_1m = prov.get("output_cost_per_1m_tokens" if is_output else "input_cost_per_1m_tokens", 0.20)
        
        return float(cost_1m) / 1000.0
