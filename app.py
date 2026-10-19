"""Meu Pierre na nuvem (Vercel): conector MCP remoto para o claude.ai.

Rotas
  POST /mcp                      endpoint MCP com cabeçalho "Authorization: Bearer <MEU_PIERRE_TOKEN>"
  POST /mcp/<MEU_PIERRE_TOKEN>   o mesmo, com o token na URL (alternativa)
  GET  /api/cron                 sincronização diária (chamada pelo Cron da Vercel)
  GET  /exportar.xlsx            download de planilha com link assinado e temporário
  GET  /                         só diz que está no ar (não mostra nenhum dado)

Sem o token certo, o endpoint MCP responde 404, como se não existisse.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

try:
    from fastapi import FastAPI as _App  # a Vercel detecta apps FastAPI automaticamente
except ImportError:  # para rodar testes locais sem FastAPI
    from starlette.applications import Starlette as _App

VERSAO_PROTOCOLO = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
_pierre = None


def _nucleo():
    """Importa o núcleo só quando precisa: um erro de configuração não derruba o site todo."""
    global _pierre
    if _pierre is None:
        import pierre

        _pierre = pierre
    return _pierre


def _token() -> str:
    tok = os.environ.get("MEU_PIERRE_TOKEN", "")
    if len(tok) < 32:
        raise RuntimeError("MEU_PIERRE_TOKEN ausente ou curto demais (mínimo 32 caracteres).")
    return tok


def _dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_none=True, mode="json")
    return obj


# ------------------------------------------------------------------ MCP
async def _despachar(msg: dict) -> dict | None:
    metodo, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notificação (ex.: notifications/initialized): não tem resposta
        return None

    def ok(resultado):
        return {"jsonrpc": "2.0", "id": mid, "result": resultado}

    def erro(codigo, texto):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": codigo, "message": texto}}

    p = _nucleo()
    if metodo == "initialize":
        pedida = params.get("protocolVersion")
        return ok({
            "protocolVersion": pedida if pedida in VERSAO_PROTOCOLO else VERSAO_PROTOCOLO[0],
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": "meu-pierre", "title": "Meu Pierre", "version": "2.0.0"},
            "instructions": p.mcp.instructions,
        })
    if metodo == "ping":
        return ok({})
    if metodo == "tools/list":
        return ok({"tools": [_dump(t) for t in await p.mcp.list_tools()]})
    if metodo == "tools/call":
        try:
            r = await p.mcp.call_tool(params.get("name"), params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001
            causa = e.__cause__ or e  # o SDK embrulha o erro original
            return ok({"content": [{"type": "text", "text": f"Erro: {causa}"}], "isError": True})
        if isinstance(r, tuple):  # SDK 1.x devolve (conteúdo, estruturado)
            r = r[0]
        if isinstance(r, (list, tuple)):
            return ok({"content": [_dump(c) for c in r], "isError": False})
        return ok(_dump(r))
    if metodo == "prompts/list":
        return ok({"prompts": [_dump(x) for x in await p.mcp.list_prompts()]})
    if metodo == "prompts/get":
        return ok(_dump(await p.mcp.get_prompt(params.get("name"), params.get("arguments"))))
    if metodo in ("resources/list", "resources/templates/list"):
        return ok({"resources": []} if metodo == "resources/list" else {"resourceTemplates": []})
    return erro(-32601, f"Método não suportado: {metodo}")


async def mcp_endpoint(request: Request) -> Response:
    try:
        esperado = _token()
    except RuntimeError as e:
        return PlainTextResponse(str(e), status_code=500)
    # Token pelo cabeçalho "Authorization: Bearer <token>" (recomendado) ou na própria URL.
    recebido = request.path_params.get("token") or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(recebido.encode(), esperado.encode()):
        return PlainTextResponse("Not Found", status_code=404)
    if request.method != "POST":
        # Sem streaming SSE: a especificação permite responder 405 ao GET.
        return Response(status_code=405, headers={"Allow": "POST"})
    try:
        corpo = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}},
                            status_code=400)
    try:
        if isinstance(corpo, list):
            respostas = [r for r in [await _despachar(m) for m in corpo] if r]
            return JSONResponse(respostas) if respostas else Response(status_code=202)
        resp = await _despachar(corpo)
    except Exception as e:  # noqa: BLE001  (ex.: banco fora do ar)
        return JSONResponse({"jsonrpc": "2.0", "id": corpo.get("id") if isinstance(corpo, dict) else None,
                             "error": {"code": -32603, "message": f"Erro interno: {e}"}})
    return JSONResponse(resp) if resp else Response(status_code=202)


# ----------------------------------------------------------------- cron
async def cron(request: Request) -> Response:
    segredo = os.environ.get("CRON_SECRET", "")
    if not segredo or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {segredo}"):
        return PlainTextResponse("Unauthorized", status_code=401)
    p = _nucleo()
    try:
        r = p._sync(30)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=500)
    return JSONResponse({"ok": True, **r})


# ------------------------------------------------------------- exportar
async def exportar(request: Request) -> Response:
    q = request.query_params
    de, ate, exp, sig = q.get("de", ""), q.get("ate", ""), q.get("exp", "0"), q.get("sig", "")
    try:
        msg = f"{de}|{ate}|{int(exp)}".encode()
        certo = hmac.new(_token().encode(), msg, hashlib.sha256).hexdigest()
    except (ValueError, RuntimeError):
        return PlainTextResponse("Link inválido.", status_code=403)
    if not hmac.compare_digest(sig, certo) or int(exp) < time.time():
        return PlainTextResponse("Link inválido ou expirado. Peça ao Claude para exportar de novo.", status_code=403)
    wb, de, ate, *_ = _nucleo().gerar_planilha(de, ate)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="financas_{de}_a_{ate}.xlsx"',
                             "Cache-Control": "no-store"})


async def inicio(request: Request) -> Response:
    return PlainTextResponse("Meu Pierre está no ar.")


app = _App(routes=[
    Route("/", inicio),
    Route("/mcp", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    Route("/mcp/{token}", mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    Route("/api/cron", cron),
    Route("/exportar.xlsx", exportar),
])
