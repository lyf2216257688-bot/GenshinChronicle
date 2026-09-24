"""Current TokenMetro model routes for Phase 05 SDK execution."""

from __future__ import annotations


TOKENMETRO_BASE_URL = "https://tokenmetro.com/v1"
TOKENMETRO_MODEL_IDS = {
    "gemini": "gemini-3.8-flash",
    "deepseek": "deepseek-v4.1-flash",
    "glm": "glm-5.3-flash",
}


def tokenmetro_model_id(name: str) -> str:
    try:
        return TOKENMETRO_MODEL_IDS[name]
    except KeyError as exc:
        raise ValueError(f"unsupported TokenMetro model: {name}") from exc
