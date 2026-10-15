"""Explicit E2 Responses routes; historical extraction profiles stay frozen."""
from dataclasses import dataclass, asdict
from typing import Mapping

from genshin_corpus.retrieval.navi_exploration import ExplorationError


# Separate credential slots keep account/channel identity even at the same URL.
E2_ROUTES = {
    ('gpt-6-luna', 'tokenmetro'): ('TokenMetro', 'TOKENMETRO', 'https://tokenmetro.com/v1'),
    ('glm-5.3-flash', 'jizhi_glm'): ('Jizhi', 'NAVI_GLM', 'https://jizhiapi.site/v1'),
    ('qwen3.8-flash', 'tokenmetro_qwen'): ('TokenMetro', 'NAVI_QWEN', 'https://tokenmetro.com/v1'),
    ('deepseek-v4.1-flash', 'jizhi'): ('Jizhi', 'JIZHI', 'https://jizhiapi.site/v1'),
}


@dataclass(frozen=True)
class E2Route:
    logical_model: str
    route: str
    provider: str
    model_id: str
    base_url: str
    api_key_env: str
    api_surface: str = 'responses'
    path: str = '/responses'

    def safe_dict(self):
        return asdict(self)


def e2_route(model: str, route: str, env: Mapping[str, str], *, offline=False):
    try:
        provider, prefix, confirmed_url = E2_ROUTES[(model, route)]
    except (KeyError, TypeError) as exc:
        raise ExplorationError('explicit supported model and matching route required') from exc
    if env.get(prefix + '_API_SURFACE', 'responses') != 'responses':
        raise ExplorationError('E2 requires Responses API surface')
    url = env.get(prefix + '_BASE_URL', '').rstrip('/')
    if not url or (url != confirmed_url and not (offline and url == 'https://example.invalid/v1')):
        raise ExplorationError('confirmed route Base URL required; refusing credential dispatch')
    if not env.get(prefix + '_API_KEY'):
        raise ExplorationError('configured credential absent: ' + prefix + '_API_KEY')
    # These wire IDs are explicit candidates until a successful returned identity
    # is observed; there is no alias guessing or discovery request.
    return E2Route(model, route, provider, model, url, prefix + '_API_KEY')
