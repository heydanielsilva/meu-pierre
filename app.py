"""Meu Pierre na nuvem (Vercel): painel web de finanças pessoais.

Rotas
  GET  /                  painel (ou a tela de login, se não estiver logado)
  POST /entrar            login com a senha do painel
  GET  /sair              logout
  GET  /api/painel        todos os dados do painel em JSON (?mes=AAAA-MM)
  POST /api/sincronizar   busca dados novos na Pluggy
  GET  /api/diagnostico   como as faturas dos cartões foram calculadas (para conferir com o banco)
  POST /api/orcamento     cria, altera ou remove um orçamento
  POST /api/regra         cria uma regra de categoria
  POST /api/regra/remover remove uma regra de categoria
  GET  /exportar.xlsx     download da planilha (?meses=6)
  GET  /api/cron          sincronização diária (chamada pelo Cron da Vercel)
  GET  /sw.js, /pwa/...   app instalável (PWA): service worker, manifesto e ícones

A senha do painel é SENHA_PAINEL (se existir) ou MEU_PIERRE_TOKEN.
O login fica guardado num cookie assinado com o MEU_PIERRE_TOKEN, válido por 30 dias.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import time
import urllib.parse
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

try:
    from fastapi import FastAPI as _App  # a Vercel detecta apps FastAPI automaticamente
except ImportError:  # para rodar testes locais sem FastAPI
    from starlette.applications import Starlette as _App

PASTA = Path(__file__).resolve().parent
COOKIE = "meu_pierre_sessao"
VALIDADE_SESSAO = 30 * 24 * 3600
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


def _senha() -> str:
    senha = os.environ.get("SENHA_PAINEL", "")
    if senha and len(senha) < 10:
        raise RuntimeError("SENHA_PAINEL curta demais (mínimo 10 caracteres).")
    return senha or _token()


# ---------------------------------------------------------------- sessão
def _assinar(exp: int) -> str:
    return hmac.new(_token().encode(), f"sessao|{exp}".encode(), hashlib.sha256).hexdigest()


def _logado(request: Request) -> bool:
    exp, _, sig = request.cookies.get(COOKIE, "").partition(".")
    try:
        return int(exp) > time.time() and hmac.compare_digest(sig, _assinar(int(exp)))
    except (ValueError, RuntimeError):
        return False


def _protegido(fn):
    async def wrapper(request: Request) -> Response:
        if not _logado(request):
            return JSONResponse({"erro": "Sessão expirada. Entre de novo."}, status_code=401)
        try:
            return await fn(request)
        except Exception as e:  # noqa: BLE001  (ex.: banco fora do ar, variável faltando)
            return JSONResponse({"erro": str(e)}, status_code=500)
    return wrapper


def _html(nome: str) -> str:
    return (PASTA / nome).read_text(encoding="utf-8")


async def inicio(request: Request) -> Response:
    try:
        _token()
    except RuntimeError as e:
        return PlainTextResponse(f"Configuração incompleta: {e}", status_code=500)
    if not _logado(request):
        erro = "Senha incorreta." if request.query_params.get("erro") else ""
        return HTMLResponse(_html("login.html").replace("{{erro}}", erro))
    return HTMLResponse(_html("painel.html"), headers={"Cache-Control": "no-store"})


async def entrar(request: Request) -> Response:
    corpo = urllib.parse.parse_qs((await request.body()).decode(errors="ignore"))
    senha = (corpo.get("senha") or [""])[0].strip()
    try:
        certa = hmac.compare_digest(senha.encode(), _senha().encode())
    except RuntimeError as e:
        return PlainTextResponse(f"Configuração incompleta: {e}", status_code=500)
    if not certa:
        await asyncio.sleep(1)  # atrasa quem tenta adivinhar a senha
        return RedirectResponse("/?erro=1", status_code=303)
    exp = int(time.time()) + VALIDADE_SESSAO
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE, f"{exp}.{_assinar(exp)}", max_age=VALIDADE_SESSAO, httponly=True,
                    secure=request.url.scheme == "https" or bool(os.environ.get("VERCEL")), samesite="lax")
    return resp


async def sair(request: Request) -> Response:
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE)
    resp.headers["Clear-Site-Data"] = '"cache"'  # apaga os dados guardados para uso sem internet
    return resp


# ------------------------------------------------------------------ PWA
TIPOS_PWA = {".png": "image/png", ".webmanifest": "application/manifest+json", ".js": "text/javascript; charset=utf-8"}


def _arquivo_pwa(nome: str) -> Response:
    caminho = (PASTA / "pwa" / nome).resolve()
    if caminho.parent != (PASTA / "pwa").resolve() or not caminho.is_file() or caminho.suffix not in TIPOS_PWA:
        return PlainTextResponse("Não encontrado", status_code=404)
    cache = "no-cache" if caminho.suffix in (".js", ".webmanifest") else "public, max-age=604800"
    return Response(caminho.read_bytes(), media_type=TIPOS_PWA[caminho.suffix], headers={"Cache-Control": cache})


async def pwa(request: Request) -> Response:
    return _arquivo_pwa(request.path_params["nome"])


async def service_worker(request: Request) -> Response:
    # servido na raiz para controlar o site todo
    resp = _arquivo_pwa("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ------------------------------------------------------------------- API
async def _json(request: Request) -> dict:
    try:
        corpo = await request.json()
    except json.JSONDecodeError:
        return {}
    return corpo if isinstance(corpo, dict) else {}


@_protegido
async def api_painel(request: Request) -> Response:
    dados = _nucleo().painel(request.query_params.get("mes", ""))
    return JSONResponse(dados, headers={"Cache-Control": "no-store"})


@_protegido
async def api_diagnostico(request: Request) -> Response:
    return JSONResponse({"sync": _nucleo().diagnostico_sync(), "cartoes": _nucleo().diagnostico_cartoes()}, headers={"Cache-Control": "no-store"})


@_protegido
async def api_sincronizar(request: Request) -> Response:
    dias = int((await _json(request)).get("dias") or 30)
    return JSONResponse(_nucleo().sincronizar(dias))


@_protegido
async def api_orcamento(request: Request) -> Response:
    d = await _json(request)
    categoria = str(d.get("categoria") or "").strip()
    if not categoria:
        return JSONResponse({"erro": "Informe a categoria."}, status_code=400)
    _nucleo().definir_orcamento(categoria, float(d.get("valor") or 0))
    return JSONResponse({"ok": True})


@_protegido
async def api_regra(request: Request) -> Response:
    d = await _json(request)
    texto, categoria = str(d.get("texto") or "").strip(), str(d.get("categoria") or "").strip()
    if not texto or not categoria:
        return JSONResponse({"erro": "Informe o texto e a categoria."}, status_code=400)
    _nucleo().criar_regra(texto, categoria)
    return JSONResponse({"ok": True})


@_protegido
async def api_regra_remover(request: Request) -> Response:
    n = _nucleo().remover_regra(str((await _json(request)).get("texto") or ""))
    return JSONResponse({"ok": bool(n)})


async def exportar(request: Request) -> Response:
    if not _logado(request):
        return RedirectResponse("/", status_code=303)
    try:
        meses = max(1, min(int(request.query_params.get("meses", "6")), 24))
    except ValueError:
        meses = 6
    wb, de, ate, *_ = _nucleo().gerar_planilha(meses=meses)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="financas_{de}_a_{ate}.xlsx"',
                             "Cache-Control": "no-store"})


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


app = _App(routes=[
    Route("/", inicio),
    Route("/entrar", entrar, methods=["POST"]),
    Route("/sair", sair),
    Route("/api/painel", api_painel),
    Route("/api/sincronizar", api_sincronizar, methods=["POST"]),
    Route("/api/diagnostico", api_diagnostico),
    Route("/api/orcamento", api_orcamento, methods=["POST"]),
    Route("/api/regra", api_regra, methods=["POST"]),
    Route("/api/regra/remover", api_regra_remover, methods=["POST"]),
    Route("/exportar.xlsx", exportar),
    Route("/api/cron", cron),
    Route("/sw.js", service_worker),
    Route("/pwa/{nome}", pwa),
])
