"""HTTP client for the hive-server REST API."""

from __future__ import annotations

import requests


class HiveError(Exception):
    def __init__(self, status: int, code: str, message: str, data: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message, self.data = status, code, message, data or {}


class ServerClient:
    def __init__(self, url: str, token: str, timeout: float = 20):
        self.base = url.rstrip("/") + "/api/v1"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, path: str, json: dict | None = None,
                params: dict | None = None):
        try:
            r = self.session.request(method, self.base + path, json=json, params=params,
                                     timeout=self.timeout)
        except requests.RequestException as e:
            raise HiveError(0, "unreachable", str(e)) from e
        try:
            data = r.json()
        except ValueError:
            data = {"error": "bad_response", "message": r.text[:300]}
        if r.status_code >= 400:
            raise HiveError(r.status_code, data.get("error", "error"),
                            data.get("message", str(data)), data)
        return data

    def get(self, path, **params):
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path, json=None):
        return self.request("POST", path, json=json or {})

    def patch(self, path, json=None):
        return self.request("PATCH", path, json=json or {})

    def put(self, path, json=None):
        return self.request("PUT", path, json=json or {})
