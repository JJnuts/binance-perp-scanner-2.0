"""HTTP session and raw JSON getters for all upstream APIs."""

import requests
from typing import Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import API_TIMEOUT, BINANCE_BASE, DERIBIT_BASE, MAX_WORKERS, YAHOO_CHART_BASE


def _make_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "binance-perp-scanner/2.0"})
    return session
_SESSION = _make_session()
def _get_json(path: str, params: Optional[dict] = None, timeout: int = API_TIMEOUT):
    response = _SESSION.get(f"{BINANCE_BASE}{path}", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()
def _get_json_url(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = API_TIMEOUT,
):
    response = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()
def _get_deribit(method: str, params: Optional[dict] = None, timeout: int = API_TIMEOUT):
    response = _get_json_url(f"{DERIBIT_BASE}/{method}", params=params, timeout=timeout)
    if isinstance(response, dict) and "result" in response:
        return response["result"]
    return response
def _get_yahoo_chart(symbol: str, range_: str, interval: str, timeout: int = API_TIMEOUT):
    return _get_json_url(
        f"{YAHOO_CHART_BASE}/{symbol}",
        params={"range": range_, "interval": interval, "includePrePost": "false"},
        timeout=timeout,
    )
