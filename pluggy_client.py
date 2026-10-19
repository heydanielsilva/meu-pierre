"""Cliente mínimo e SOMENTE LEITURA da API Pluggy (uso pessoal via Meu Pluggy).

O único POST é o /auth, que troca Client ID/Secret por uma apiKey.
Nenhuma função aqui move dinheiro, faz PIX ou altera dados no banco.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.pluggy.ai"


class PluggyError(RuntimeError):
    pass


class PluggyClient:
    def __init__(self, client_id: str, client_secret: str, timeout: int = 30):
        if not client_id or not client_secret:
            raise PluggyError(
                "PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET não configurados. "
                "Veja o arquivo .env.exemplo."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._api_key: str | None = None
        self._api_key_at = 0.0

    # ---------------------------------------------------------------- base
    def _auth(self) -> str:
        # A apiKey vale ~2h; renovamos com folga.
        if self._api_key and time.time() - self._api_key_at < 90 * 60:
            return self._api_key
        body = json.dumps(
            {"clientId": self.client_id, "clientSecret": self.client_secret}
        ).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/auth",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        data = self._send(req)
        self._api_key = data["apiKey"]
        self._api_key_at = time.time()
        return self._api_key

    def _send(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode(errors="ignore")[:300]
            raise PluggyError(f"Pluggy respondeu {e.code}: {detalhe}") from e
        except urllib.error.URLError as e:
            raise PluggyError(f"Sem conexão com a Pluggy: {e.reason}") from e

    def _get(self, path: str, **params) -> dict:
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"X-API-KEY": self._auth()})
        return self._send(req)

    # ----------------------------------------------------------- endpoints
    def item(self, item_id: str) -> dict:
        return self._get(f"/items/{item_id}")

    def accounts(self, item_id: str) -> list[dict]:
        return self._get("/accounts", itemId=item_id).get("results", [])

    def transactions(self, account_id: str, date_from: str, date_to: str) -> list[dict]:
        """Todas as transações da conta no período, seguindo a paginação."""
        out: list[dict] = []
        page = 1
        while True:
            data = self._get(
                "/transactions",
                accountId=account_id,
                **{"from": date_from, "to": date_to},
                pageSize=500,
                page=page,
            )
            out.extend(data.get("results", []))
            total_pages = data.get("totalPages", 1) or 1
            if page >= total_pages:
                break
            page += 1
        return out

    def investments(self, item_id: str) -> list[dict]:
        return self._get("/investments", itemId=item_id).get("results", [])

    def bills(self, account_id: str) -> list[dict]:
        return self._get("/bills", accountId=account_id).get("results", [])
