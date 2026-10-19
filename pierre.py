"""Meu Pierre — núcleo: ferramentas de finanças pessoais (MCP) usadas no Claude Desktop e na Vercel.

Lê suas contas pelo Open Finance via Meu Pluggy (gratuito para uso pessoal),
guarda um cache local em SQLite e oferece ao Claude ferramentas para
responder perguntas sobre seus gastos, orçamentos, assinaturas e investimentos.

SOMENTE LEITURA: nada aqui movimenta dinheiro.
"""
from __future__ import annotations

import calendar
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

try:  # SDK MCP 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK MCP 1.x
    from mcp.server.fastmcp import FastMCP

from mcp.types import ToolAnnotations

from pluggy_client import PluggyClient
from store import NEUTRAS, Store, _sem_acento, agora_br, hoje_br

PASTA = Path(__file__).resolve().parent


# ------------------------------------------------------------------ config
def _carregar_env() -> None:
    arq = PASTA / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_carregar_env()
DEMO = os.environ.get("MEU_PIERRE_DEMO", "0") == "1"
NUVEM = bool(os.environ.get("VERCEL"))  # a Vercel define VERCEL=1
SALDO_MINIMO = float(os.environ.get("SALDO_MINIMO", "500"))


def _itens() -> dict[str, str]:
    """PLUGGY_ITEMS=itemId:Nubank,itemId2:Itaú  ->  {itemId: 'Nubank', ...}"""
    out = {}
    for parte in os.environ.get("PLUGGY_ITEMS", "").split(","):
        parte = parte.strip()
        if not parte:
            continue
        item_id, _, nome = parte.partition(":")
        out[item_id.strip()] = nome.strip()
    return out


DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
if DB_URL and not DEMO:
    store = Store(DB_URL)
elif NUVEM and not DEMO:
    raise RuntimeError("Na Vercel é preciso um banco Postgres: configure DATABASE_URL (ex.: Neon).")
else:
    db_nome = "/tmp/meu_pierre_demo.db" if (DEMO and NUVEM) else PASTA / ("meu_pierre_demo.db" if DEMO else "meu_pierre.db")
    store = Store(os.environ.get("MEU_PIERRE_DB", db_nome))
if DEMO:
    store.popular_demo()

mcp = FastMCP(
    "meu-pierre",
    instructions=(
        "Assistente de finanças pessoais do usuário, com dados reais das contas dele via "
        "Open Finance. Responda em português, com valores em R$. Antes de analisar, use "
        "visao_geral ou resumo_mensal. Pagamentos de fatura, transferências entre contas "
        "próprias e aplicações em investimentos são neutros e não contam como gasto. "
        "Seja direto, aponte padrões e sugira ações concretas. Nunca invente números."
    ),
)


# Ferramentas só de consulta: o Claude sabe que não alteram nada.
LEITURA = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
AJUSTE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
ESCRITA = {"definir_orcamento", "criar_regra_categoria", "remover_regra_categoria", "sincronizar"}

# ----------------------------------------------------------------- helpers
def brl(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.1f}%".replace(".", ",")


MESES = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
     "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}


def _mes(mes: str = "") -> tuple[str, str, str]:
    """Aceita '', '2026-10', '10/2026', 'outubro', 'outubro 2025', 'mes passado'."""
    hoje = hoje_br()
    m = _sem_acento(mes.strip())
    ano, num = hoje.year, hoje.month
    if m in ("", "atual", "este mes", "mes atual"):
        pass
    elif "passado" in m or "anterior" in m:
        num, ano = (12, ano - 1) if num == 1 else (num - 1, ano)
    elif re.fullmatch(r"\d{4}-\d{1,2}", m):
        ano, num = map(int, m.split("-"))
    elif re.fullmatch(r"\d{1,2}/\d{4}", m):
        num, ano = map(int, m.split("/"))
    else:
        for nome, i in MESES.items():
            if m.startswith(nome[:3]):
                num = i
                achou = re.search(r"\d{4}", m)
                if achou:
                    ano = int(achou.group())
                elif num > hoje.month:
                    ano -= 1
                break
        else:
            raise ValueError(f"Não entendi o mês '{mes}'. Use AAAA-MM, ex.: 2026-10.")
    ult = calendar.monthrange(ano, num)[1]
    return f"{ano}-{num:02d}", f"{ano}-{num:02d}-01", f"{ano}-{num:02d}-{ult}"


def _periodo(mes: str, de: str, ate: str) -> tuple[str, str, str]:
    if de or ate:
        de = de or "2000-01-01"
        ate = ate or hoje_br().isoformat()
        return f"{de} a {ate}", de, ate
    return _mes(mes)


def _garantir_dados() -> str:
    """Sincroniza sozinho se nunca sincronizou ou se faz mais de 24h."""
    if DEMO:
        return ""
    ult = store.get_meta("ultima_sync")
    limite = timedelta(hours=30 if NUVEM else 24)  # na nuvem o cron diário cuida disso
    if ult and agora_br() - datetime.fromisoformat(ult) < limite:
        return ""
    try:
        _sync(90 if NUVEM and not ult else 365 if not ult else 30)
        return ""
    except Exception as e:  # noqa: BLE001
        return f"\n\n(Aviso: não consegui atualizar os dados agora: {e}. Mostrando o último cache.)"


def _sync(dias: int) -> dict:
    itens = _itens()
    if not itens:
        raise RuntimeError("Nenhum item configurado em PLUGGY_ITEMS no arquivo .env.")
    cli = PluggyClient(os.environ.get("PLUGGY_CLIENT_ID", ""), os.environ.get("PLUGGY_CLIENT_SECRET", ""))
    return store.sincronizar(cli, itens, dias)


def _fluxo(txs: list[dict]) -> tuple[float, float]:
    """Receitas e gastos reais. Estornos abatem dos gastos em vez de contar como receita."""
    validas = [t for t in txs if not t["neutra"]]
    rec = sum(t["valor"] for t in validas if t["direcao"] == "entrada" and t["categoria"] != "Estornos")
    estornos = sum(t["valor"] for t in validas if t["direcao"] == "entrada" and t["categoria"] == "Estornos")
    desp = sum(t["valor"] for t in validas if t["direcao"] == "saida") - estornos
    return rec, desp


def _por_categoria(txs: list[dict]) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    for t in txs:
        if t["neutra"]:
            continue
        if t["direcao"] == "saida":
            tot[t["categoria"]] += t["valor"]
        elif t["categoria"] == "Estornos":
            tot["Estornos"] -= t["valor"]  # aparece negativo e abate o total
    return {k: v for k, v in sorted(tot.items(), key=lambda kv: -kv[1]) if abs(v) > 0.005}


def _linha_tx(t: dict) -> str:
    sinal = "-" if t["direcao"] == "saida" else "+"
    parc = f" ({t['parcela_atual']}/{t['parcelas_total']})" if t.get("parcelas_total") else ""
    neutra = " [neutra]" if t["neutra"] else ""
    return (f"{t['data'][8:10]}/{t['data'][5:7]}/{t['data'][:4]}  {sinal}{brl(t['valor'])}  "
            f"{t['descricao']}{parc} · {t['categoria']} · {t.get('conta') or ''}{neutra}")


def _chave_recorrente(t: dict) -> str:
    base = t.get("estabelecimento") or t["descricao"]
    base = _sem_acento(base)
    base = re.sub(r"[\d*#.\-_/]+", " ", base)
    return " ".join(base.split()[:3])


# ================================================================== TOOLS
@mcp.tool(annotations=AJUSTE)
def sincronizar(dias: int = 180) -> str:
    """Busca dados novos no Open Finance (Meu Pluggy) e atualiza o cache local.
    `dias`: quantos dias de histórico buscar (padrão 180, máx. 365).
    O Meu Pluggy atualiza os bancos 1x por dia, então rodar mais que isso não traz novidade."""
    if DEMO:
        return "Modo demonstração ativo: os dados são fictícios e não há o que sincronizar."
    r = _sync(max(1, min(dias, 365)))
    txt = (f"Sincronizado: {r['contas']} contas, {r['transacoes']} transações, "
           f"{r['investimentos']} investimentos, {r['faturas']} faturas.")
    if r["erros"]:
        txt += "\nProblemas:\n- " + "\n- ".join(r["erros"])
    return txt


@mcp.tool(annotations=LEITURA)
def status_conexoes() -> str:
    """Status de cada banco conectado: última atualização, erros e quando a autorização expira."""
    rows = store.con.execute("SELECT * FROM items").fetchall()
    if not rows:
        return "Nenhuma conexão no cache ainda. Rode `sincronizar`."
    hoje = hoje_br()
    out = [f"Última sincronização local: {store.get_meta('ultima_sync') or 'nunca'}"]
    for r in rows:
        exp = r["consentimento_expira"]
        dias = (date.fromisoformat(exp[:10]) - hoje).days if exp else None
        aviso = ""
        if dias is not None and dias <= 30:
            aviso = f"  ⚠ autorização expira em {dias} dias, renove no Dashboard da Pluggy"
        out.append(f"- {r['banco'] or r['id']}: {r['status']} · atualizado {r['ultima_atualizacao'] or '—'}"
                   f" · expira {exp[:10] if exp else '—'}{aviso}" + (f" · ERRO: {r['erro']}" if r["erro"] else ""))
    return "\n".join(out)


@mcp.tool(annotations=LEITURA)
def visao_geral() -> str:
    """Panorama completo: saldos, cartões, investimentos, patrimônio líquido e o mês atual
    (receitas, gastos, ritmo de gasto). Bom ponto de partida para qualquer conversa."""
    aviso = _garantir_dados()
    contas = store.con.execute("SELECT * FROM accounts ORDER BY banco").fetchall()
    invs = store.con.execute("SELECT SUM(saldo) s FROM investments").fetchone()["s"] or 0
    saldo = sum(c["saldo"] or 0 for c in contas if c["tipo"] == "BANK")
    faturas = sum(c["saldo"] or 0 for c in contas if c["subtipo"] == "CREDIT_CARD")
    rotulo, de, ate = _mes()
    txs = store.transacoes(de, ate)
    rec, desp = _fluxo(txs)
    hoje = hoje_br()
    dias_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    proj = _projecao_mes(txs)
    _, de_ant, ate_ant = _mes("mes passado")
    _, desp_ant = _fluxo(store.transacoes(de_ant, ate_ant))
    cats = list(_por_categoria(txs).items())[:3]
    out = [
        "PATRIMÔNIO",
        f"- Saldo em conta: {brl(saldo)}",
        f"- Investimentos: {brl(invs)}",
        f"- Faturas de cartão em aberto: {brl(-faturas)}",
        f"- Patrimônio líquido: {brl(saldo + invs - faturas)}",
        "",
        f"MÊS ATUAL ({rotulo}, dia {hoje.day} de {dias_mes})",
        f"- Receitas: {brl(rec)}",
        f"- Gastos: {brl(desp)} (mês passado inteiro: {brl(desp_ant)})",
        f"- Projeção de gastos até o fim do mês: {brl(proj)}",
        f"- Sobra até agora: {brl(rec - desp)}",
        "- Maiores categorias: " + ", ".join(f"{c} {brl(v)}" for c, v in cats),
    ]
    n_alertas = len(_lista_alertas())
    if n_alertas:
        out.append(f"\n{n_alertas} alerta(s) ativo(s). Use a ferramenta `alertas` para ver.")
    return "\n".join(out) + aviso


@mcp.tool(annotations=LEITURA)
def listar_contas() -> str:
    """Contas correntes, poupanças e cartões de crédito com saldo, limite e vencimento."""
    aviso = _garantir_dados()
    out = []
    for c in store.con.execute("SELECT * FROM accounts ORDER BY banco, tipo").fetchall():
        if c["subtipo"] == "CREDIT_CARD":
            out.append(f"- {c['banco']} · {c['nome']} (cartão final {str(c['numero'] or '')[-4:]}): "
                       f"fatura {brl(c['saldo'])} · limite {brl(c['limite'])} · disponível {brl(c['limite_disponivel'])}"
                       f" · vence {c['vencimento_fatura'] or '—'}")
        else:
            out.append(f"- {c['banco']} · {c['nome']}: saldo {brl(c['saldo'])}")
    return ("\n".join(out) or "Nenhuma conta no cache. Rode `sincronizar`.") + aviso


@mcp.tool(annotations=LEITURA)
def buscar_transacoes(texto: str = "", de: str = "", ate: str = "", mes: str = "",
                      categoria: str = "", banco: str = "", tipo: str = "",
                      valor_min: float = 0, limite: int = 60) -> str:
    """Procura transações. Todos os filtros são opcionais e combináveis.
    texto: parte da descrição ou estabelecimento (ex.: 'ifood', 'uber').
    de/ate: datas AAAA-MM-DD. mes: '2026-10', 'outubro', 'mes passado' (se nada for dado, últimos 90 dias).
    categoria: ex. 'Delivery'. banco: ex. 'Nubank'. tipo: 'saida' ou 'entrada'.
    Retorna a lista e o total somado."""
    aviso = _garantir_dados()
    if mes:
        _, de, ate = _mes(mes)
    elif not de and not ate:
        de = (hoje_br() - timedelta(days=90)).isoformat()
    de = de or "2000-01-01"
    ate = ate or hoje_br().isoformat()
    txs = store.transacoes(de, ate)
    q = _sem_acento(texto)
    res = [
        t for t in txs
        if (not q or q in _sem_acento(f"{t['descricao']} {t.get('estabelecimento') or ''}"))
        and (not categoria or _sem_acento(categoria) in _sem_acento(t["categoria"]))
        and (not banco or _sem_acento(banco) in _sem_acento(t.get("banco") or ""))
        and (not tipo or t["direcao"] == _sem_acento(tipo))
        and t["valor"] >= valor_min
    ]
    if not res:
        return f"Nenhuma transação encontrada entre {de} e {ate}." + aviso
    saidas = sum(t["valor"] for t in res if t["direcao"] == "saida" and not t["neutra"])
    entradas = sum(t["valor"] for t in res if t["direcao"] == "entrada" and not t["neutra"])
    cab = f"{len(res)} transações entre {de} e {ate} · saídas {brl(saidas)} · entradas {brl(entradas)}"
    linhas = [_linha_tx(t) for t in res[:limite]]
    if len(res) > limite:
        linhas.append(f"... e mais {len(res) - limite} (aumente `limite` para ver).")
    return cab + "\n" + "\n".join(linhas) + aviso


@mcp.tool(annotations=LEITURA)
def resumo_mensal(mes: str = "") -> str:
    """Resumo de um mês: receitas, gastos, sobra, taxa de poupança, gastos por categoria,
    maiores estabelecimentos e comparação com o mês anterior.
    mes: '2026-10', 'outubro', 'mes passado' ou vazio para o mês atual."""
    aviso = _garantir_dados()
    rotulo, de, ate = _mes(mes)
    txs = store.transacoes(de, ate)
    if not txs:
        return f"Não há transações de {rotulo} no cache. Se for um mês antigo, rode `sincronizar` com mais dias." + aviso
    rec, desp = _fluxo(txs)
    a = date.fromisoformat(de) - timedelta(days=1)
    _, de_a, ate_a = _mes(a.strftime("%Y-%m"))
    txs_a = store.transacoes(de_a, ate_a)
    rec_a, desp_a = _fluxo(txs_a)
    cats, cats_a = _por_categoria(txs), _por_categoria(txs_a)
    estab: dict[str, float] = defaultdict(float)
    for t in txs:
        if t["direcao"] == "saida" and not t["neutra"]:
            estab[t.get("estabelecimento") or t["descricao"]] += t["valor"]
    poup = (rec - desp) / rec if rec else None
    out = [
        f"RESUMO {rotulo}",
        f"- Receitas: {brl(rec)} (mês anterior {brl(rec_a)})",
        f"- Gastos: {brl(desp)} (mês anterior {brl(desp_a)}, variação {pct((desp - desp_a) / desp_a if desp_a else None)})",
        f"- Sobra: {brl(rec - desp)} · taxa de poupança {pct(poup).lstrip('+')}",
        "", "GASTOS POR CATEGORIA (vs mês anterior)",
    ]
    for c, v in cats.items():
        ant = cats_a.get(c, 0)
        var = f" ({pct((v - ant) / ant)})" if ant else " (novo)"
        out.append(f"- {c}: {brl(v)} · {v / desp * 100:.0f}% do total{var}" if desp else f"- {c}: {brl(v)}")
    out += ["", "ONDE MAIS GASTOU"]
    for e, v in sorted(estab.items(), key=lambda kv: -kv[1])[:8]:
        out.append(f"- {e}: {brl(v)}")
    return "\n".join(out) + aviso


@mcp.tool(annotations=LEITURA)
def gastos_por_categoria(mes: str = "", de: str = "", ate: str = "") -> str:
    """Total gasto por categoria num mês (padrão: atual) ou num intervalo de/ate (AAAA-MM-DD)."""
    aviso = _garantir_dados()
    rotulo, de, ate = _periodo(mes, de, ate)
    cats = _por_categoria(store.transacoes(de, ate))
    total = sum(cats.values())
    out = [f"Gastos por categoria · {rotulo} · total {brl(total)}"]
    out += [f"- {c}: {brl(v)} ({pct(v / total).lstrip('+')})" for c, v in cats.items()] if total else ["Sem gastos."]
    return "\n".join(out) + aviso


@mcp.tool(annotations=LEITURA)
def maiores_gastos(mes: str = "", quantidade: int = 10) -> str:
    """As maiores compras/saídas individuais do mês (ignora fatura e transferências próprias)."""
    aviso = _garantir_dados()
    rotulo, de, ate = _mes(mes)
    txs = [t for t in store.transacoes(de, ate) if t["direcao"] == "saida" and not t["neutra"]]
    txs.sort(key=lambda t: -t["valor"])
    return f"Maiores gastos de {rotulo}:\n" + "\n".join(_linha_tx(t) for t in txs[:quantidade]) + aviso


@mcp.tool(annotations=LEITURA)
def comparar_meses(mes_a: str, mes_b: str) -> str:
    """Compara dois meses categoria a categoria. Ex.: mes_a='setembro', mes_b='outubro'."""
    aviso = _garantir_dados()
    ra, da, aa = _mes(mes_a)
    rb, db_, ab = _mes(mes_b)
    ta, tb = store.transacoes(da, aa), store.transacoes(db_, ab)
    ca, cb = _por_categoria(ta), _por_categoria(tb)
    (reca, despa), (recb, despb) = _fluxo(ta), _fluxo(tb)
    out = [f"{ra} → {rb}",
           f"- Receitas: {brl(reca)} → {brl(recb)}",
           f"- Gastos: {brl(despa)} → {brl(despb)} ({brl(despb - despa)})", "", "Por categoria (maior variação primeiro):"]
    todas = set(ca) | set(cb)
    for c in sorted(todas, key=lambda c: -abs(cb.get(c, 0) - ca.get(c, 0))):
        out.append(f"- {c}: {brl(ca.get(c, 0))} → {brl(cb.get(c, 0))} ({brl(cb.get(c, 0) - ca.get(c, 0))})")
    return "\n".join(out) + aviso


@mcp.tool(annotations=LEITURA)
def evolucao_mensal(meses: int = 6) -> str:
    """Receitas, gastos e sobra mês a mês nos últimos N meses, com médias."""
    aviso = _garantir_dados()
    hoje = hoje_br().replace(day=1)
    linhas, gastos, sobras = [], [], []
    for i in range(meses - 1, -1, -1):
        m = hoje
        for _ in range(i):
            m = (m - timedelta(days=1)).replace(day=1)
        rot, de, ate = _mes(m.strftime("%Y-%m"))
        rec, desp = _fluxo(store.transacoes(de, ate))
        parcial = " (parcial)" if i == 0 else ""
        linhas.append(f"- {rot}{parcial}: receitas {brl(rec)} · gastos {brl(desp)} · sobra {brl(rec - desp)}")
        if i:
            gastos.append(desp)
            sobras.append(rec - desp)
    if gastos:
        linhas.append(f"\nMédia dos meses fechados: gastos {brl(statistics.mean(gastos))} · sobra {brl(statistics.mean(sobras))}")
    return "\n".join(linhas) + aviso


CONTAS_FIXAS = {"Aluguel", "Contas da casa", "Internet e telefone", "Plano de saúde", "Seguros",
                "Educação", "Moradia", "Academia", "Previdência", "Empréstimos e financiamentos"}


def _recorrentes() -> list[dict]:
    """Cobranças que se repetem todo mês: valor fixo (assinatura) ou conta fixa de valor variável."""
    hoje = hoje_br()
    txs = store.transacoes((hoje - timedelta(days=185)).isoformat(), hoje.isoformat())
    grupos: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        if t["direcao"] == "saida" and not t["neutra"] and not t.get("parcelas_total"):
            grupos[_chave_recorrente(t)].append(t)
    achados = []
    for lst in grupos.values():
        meses = {t["data"][:7] for t in lst}
        ult = max(lst, key=lambda t: t["data"])
        d = date.fromisoformat(ult["data"])
        if len(meses) < 3 or len(lst) > len(meses) * 1.5 or (hoje - d).days > 45:
            continue  # 3+ meses, ~1x por mês e ainda ativa
        valores = [t["valor"] for t in lst]
        media = statistics.mean(valores)
        cv = statistics.pstdev(valores) / media if media else 0
        if ult["categoria"] in CONTAS_FIXAS and cv <= 0.40:
            tipo = "conta fixa"
        elif cv <= 0.05:
            tipo = "assinatura"
        else:
            continue
        prox = (d.replace(day=1) + timedelta(days=32)).replace(day=min(d.day, 28))
        achados.append({"nome": ult.get("estabelecimento") or ult["descricao"], "categoria": ult["categoria"],
                        "media": media, "ultima": d, "proxima": prox, "tipo": tipo,
                        "chave": _chave_recorrente(ult)})
    return sorted(achados, key=lambda a: -a["media"])


@mcp.tool(annotations=LEITURA)
def assinaturas_recorrentes() -> str:
    """Detecta assinaturas (valor fixo todo mês, ex.: Netflix) e contas fixas (aluguel, luz, internet)
    nos últimos 6 meses. Mostra valor médio, próxima cobrança esperada e custo anual."""
    aviso = _garantir_dados()
    rec = _recorrentes()
    if not rec:
        return "Não encontrei cobranças recorrentes claras nos últimos 6 meses." + aviso
    out = []
    for tipo, titulo in (("assinatura", "ASSINATURAS"), ("conta fixa", "CONTAS FIXAS")):
        lst = [r for r in rec if r["tipo"] == tipo]
        if not lst:
            continue
        tot = sum(r["media"] for r in lst)
        out.append(f"{titulo} · {brl(tot)}/mês · {brl(tot * 12)}/ano")
        for r in lst:
            out.append(f"- {r['nome']} ({r['categoria']}): ~{brl(r['media'])}/mês · última {r['ultima'].isoformat()}"
                       f" · próxima ~{r['proxima'].isoformat()} · {brl(r['media'] * 12)}/ano")
        out.append("")
    return "\n".join(out).strip() + aviso


def _projecao_mes(txs_mes: list[dict]) -> float:
    """Gasto atual + ritmo diário dos gastos variáveis + recorrentes que ainda vão cair no mês."""
    hoje = hoje_br()
    dias_mes = calendar.monthrange(hoje.year, hoje.month)[1]
    rec = _recorrentes()
    chaves = {r["chave"] for r in rec}
    gastos = [t for t in txs_mes if t["direcao"] == "saida" and not t["neutra"]]
    atual = sum(t["valor"] for t in gastos)
    variavel = sum(t["valor"] for t in gastos if _chave_recorrente(t) not in chaves and not t.get("parcelas_total"))
    ja_cobradas = {_chave_recorrente(t) for t in gastos}
    pendentes = sum(r["media"] for r in rec if r["chave"] not in ja_cobradas
                    and r["proxima"].month == hoje.month and r["proxima"].year == hoje.year)
    return atual + variavel / hoje.day * (dias_mes - hoje.day) + pendentes


@mcp.tool(annotations=LEITURA)
def parcelas_futuras() -> str:
    """Compras parceladas em andamento no cartão e quanto ainda falta pagar em cada uma."""
    aviso = _garantir_dados()
    rows = store.con.execute(
        """SELECT * FROM transactions WHERE parcelas_total > 1 ORDER BY data DESC"""
    ).fetchall()
    ultimas: dict = {}
    for r in rows:
        k = _chave_recorrente(dict(r))
        if k not in ultimas or r["parcela_atual"] > ultimas[k]["parcela_atual"]:
            ultimas[k] = r
    out, total = [], 0.0
    for r in ultimas.values():
        rest = r["parcelas_total"] - r["parcela_atual"]
        if rest <= 0:
            continue
        total += rest * r["valor"]
        out.append(f"- {r['descricao']}: parcela {r['parcela_atual']}/{r['parcelas_total']} de {brl(r['valor'])} · "
                   f"faltam {rest} ({brl(rest * r['valor'])})")
    if not out:
        return "Nenhuma compra parcelada em andamento encontrada." + aviso
    return f"Parcelas a vencer: {brl(total)} no total\n" + "\n".join(out) + aviso



@mcp.tool(annotations=LEITURA)
def faturas_cartao() -> str:
    """Faturas dos cartões: valor atual, vencimento, limite usado e faturas anteriores."""
    aviso = _garantir_dados()
    out = []
    for c in store.con.execute("SELECT * FROM accounts WHERE subtipo='CREDIT_CARD'").fetchall():
        uso = (1 - (c["limite_disponivel"] or 0) / c["limite"]) if c["limite"] else None
        out.append(f"{c['banco']} · {c['nome']}\n- Fatura atual: {brl(c['saldo'])} · vence {c['vencimento_fatura'] or '—'}"
                   f" · fecha {c['fechamento_fatura'] or '—'}\n- Limite: {brl(c['limite'])} · usado {pct(uso).lstrip('+')}")
        for b in store.con.execute("SELECT * FROM bills WHERE account_id=? ORDER BY vencimento DESC LIMIT 6", (c["id"],)):
            out.append(f"  · fatura venc. {b['vencimento']}: {brl(b['valor'])} (mínimo {brl(b['pagamento_minimo'])})")
    return ("\n".join(out) or "Nenhum cartão encontrado.") + aviso


@mcp.tool(annotations=LEITURA)
def investimentos() -> str:
    """Carteira de investimentos: saldo por aplicação, rentabilidade no mês e em 12 meses, total por tipo."""
    aviso = _garantir_dados()
    rows = store.con.execute("SELECT * FROM investments ORDER BY saldo DESC").fetchall()
    if not rows:
        return "Nenhum investimento encontrado nas conexões." + aviso
    total = sum(r["saldo"] or 0 for r in rows)
    por_tipo: dict[str, float] = defaultdict(float)
    out = [f"Total investido: {brl(total)}"]
    for r in rows:
        por_tipo[r["tipo"] or "Outros"] += r["saldo"] or 0
        ganho = (r["saldo"] - r["valor_aplicado"]) if r["valor_aplicado"] else None
        out.append(f"- {r['nome']} ({r['banco'] or ''}): {brl(r['saldo'])} · mês {pct(r['rent_mes'])} · 12m {pct(r['rent_12m'])}"
                   + (f" · resultado {brl(ganho)}" if ganho is not None else "")
                   + (f" · vence {r['vencimento']}" if r["vencimento"] else ""))
    out.append("\nPor tipo: " + ", ".join(f"{k} {brl(v)} ({pct(v / total).lstrip('+')})" for k, v in por_tipo.items()))
    return "\n".join(out) + aviso


@mcp.tool(annotations=AJUSTE)
def definir_orcamento(categoria: str, valor_mensal: float) -> str:
    """Cria ou altera o limite mensal de gasto de uma categoria (ex.: 'Delivery', 600).
    Use valor_mensal=0 para remover o orçamento."""
    if valor_mensal <= 0:
        store.con.execute("DELETE FROM orcamentos WHERE lower(categoria)=lower(?)", (categoria,))
        store.con.commit()
        return f"Orçamento de {categoria} removido."
    store.con.execute("INSERT OR REPLACE INTO orcamentos VALUES (?,?)", (categoria.strip(), valor_mensal))
    store.con.commit()
    return f"Orçamento de {categoria}: {brl(valor_mensal)}/mês."


def _status_orcamentos(mes: str = "") -> list[dict]:
    rotulo, de, ate = _mes(mes)
    cats = _por_categoria(store.transacoes(de, ate))
    hoje = hoje_br()
    atual = rotulo == hoje.strftime("%Y-%m")
    dias_mes = calendar.monthrange(int(rotulo[:4]), int(rotulo[5:]))[1]
    out = []
    for r in store.con.execute("SELECT * FROM orcamentos ORDER BY categoria"):
        gasto = next((v for c, v in cats.items() if c.lower() == r["categoria"].lower()), 0.0)
        proj = gasto / hoje.day * dias_mes if atual and hoje.day else gasto
        out.append({"categoria": r["categoria"], "limite": r["valor"], "gasto": gasto,
                    "uso": gasto / r["valor"], "projecao": proj, "rotulo": rotulo, "atual": atual,
                    "restante_dia": (r["valor"] - gasto) / max(1, dias_mes - hoje.day + 1) if atual else None})
    return out


@mcp.tool(annotations=LEITURA)
def acompanhar_orcamentos(mes: str = "") -> str:
    """Quanto já foi gasto de cada orçamento no mês, projeção até o fim do mês
    e quanto ainda dá para gastar por dia sem estourar."""
    aviso = _garantir_dados()
    st = _status_orcamentos(mes)
    if not st:
        return "Nenhum orçamento definido. Use `definir_orcamento`, ex.: Delivery 600." + aviso
    out = [f"Orçamentos · {st[0]['rotulo']}"]
    for s in st:
        icone = "🔴" if s["uso"] >= 1 else "🟡" if s["uso"] >= 0.8 or s["projecao"] > s["limite"] else "🟢"
        linha = f"{icone} {s['categoria']}: {brl(s['gasto'])} de {brl(s['limite'])} ({s['uso'] * 100:.0f}%)"
        if s["atual"]:
            linha += f" · projeção {brl(s['projecao'])}"
            if s["restante_dia"] and s["restante_dia"] > 0:
                linha += f" · pode gastar ~{brl(s['restante_dia'])}/dia"
        out.append(linha)
    return "\n".join(out) + aviso


@mcp.tool(annotations=AJUSTE)
def criar_regra_categoria(texto: str, categoria: str) -> str:
    """Corrige a categorização: toda transação cuja descrição contenha `texto` passa a ser `categoria`.
    Ex.: texto='PIX ALUGUEL', categoria='Aluguel'. Vale para o histórico todo.
    Categorias especiais que não contam como gasto: 'Transferência entre contas próprias',
    'Pagamento de fatura', 'Investimentos'."""
    store.add_regra(texto, categoria)
    rows = store.con.execute("SELECT COUNT(*) n FROM transactions WHERE lower(descricao) LIKE ? OR lower(estabelecimento) LIKE ?",
                             (f"%{texto.lower()}%", f"%{texto.lower()}%")).fetchone()
    return f"Regra salva: '{texto}' → {categoria}. Afeta cerca de {rows['n']} transações."


@mcp.tool(annotations=LEITURA)
def listar_regras_e_categorias() -> str:
    """Mostra suas regras de categorização e as categorias usadas nos últimos 6 meses."""
    regras = store.con.execute("SELECT padrao, categoria FROM regras ORDER BY categoria").fetchall()
    hoje = hoje_br()
    cats = _por_categoria(store.transacoes((hoje - timedelta(days=180)).isoformat(), hoje.isoformat()))
    out = ["Regras:"] + ([f"- '{r['padrao']}' → {r['categoria']}" for r in regras] or ["(nenhuma)"])
    out += ["", "Categorias em uso (6 meses): " + ", ".join(cats)]
    out += ["Neutras (não contam como gasto): " + ", ".join(sorted(NEUTRAS))]
    return "\n".join(out)


@mcp.tool(annotations=AJUSTE)
def remover_regra_categoria(texto: str) -> str:
    """Apaga uma regra de categorização criada antes."""
    n = store.del_regra(texto)
    return "Regra removida." if n else f"Não existe regra para '{texto}'."


def _lista_alertas() -> list[str]:
    al: list[str] = []
    hoje = hoje_br()
    for s in _status_orcamentos():
        if s["uso"] >= 1:
            al.append(f"🔴 Orçamento de {s['categoria']} estourado: {brl(s['gasto'])} de {brl(s['limite'])}.")
        elif s["projecao"] > s["limite"] * 1.05:
            al.append(f"🟡 No ritmo atual, {s['categoria']} vai fechar o mês em {brl(s['projecao'])} (limite {brl(s['limite'])}).")
    for c in store.con.execute("SELECT * FROM accounts").fetchall():
        if c["tipo"] == "BANK" and c["saldo"] is not None and c["saldo"] < SALDO_MINIMO:
            al.append(f"🟡 Saldo baixo em {c['banco']} · {c['nome']}: {brl(c['saldo'])}.")
        if c["subtipo"] == "CREDIT_CARD" and c["vencimento_fatura"]:
            d = (date.fromisoformat(c["vencimento_fatura"]) - hoje).days
            if 0 <= d <= 7:
                al.append(f"🟡 Fatura {c['banco']} de {brl(c['saldo'])} vence em {d} dia(s) ({c['vencimento_fatura']}).")
    for it in store.con.execute("SELECT * FROM items").fetchall():
        if it["consentimento_expira"]:
            d = (date.fromisoformat(it["consentimento_expira"][:10]) - hoje).days
            if d <= 30:
                al.append(f"🟡 Autorização do Meu Pluggy ({it['banco'] or it['id']}) expira em {d} dias.")
        if it["erro"]:
            al.append(f"🔴 Conexão {it['banco'] or it['id']} com erro: {it['erro']}")
    # Gastos fora da curva nos últimos 10 dias (> 3x a mediana da categoria em 90 dias)
    hist = store.transacoes((hoje - timedelta(days=100)).isoformat(), (hoje - timedelta(days=11)).isoformat())
    base: dict[str, list[float]] = defaultdict(list)
    for t in hist:
        if t["direcao"] == "saida" and not t["neutra"]:
            base[t["categoria"]].append(t["valor"])
    for t in store.transacoes((hoje - timedelta(days=10)).isoformat(), hoje.isoformat()):
        b = base.get(t["categoria"])
        if t["direcao"] == "saida" and not t["neutra"] and b and len(b) >= 4 and not t.get("parcelas_total"):
            med = statistics.median(b)
            if t["valor"] > 3 * med and t["valor"] > 100:
                al.append(f"🟡 Gasto fora do padrão: {t['descricao']} {brl(t['valor'])} em {t['data']} "
                          f"(normal em {t['categoria']}: ~{brl(med)}).")
    ult = store.get_meta("ultima_sync")
    if not DEMO and ult and agora_br() - datetime.fromisoformat(ult) > timedelta(days=3):
        al.append(f"🟡 Dados desatualizados desde {ult}. Rode `sincronizar`.")
    return al


@mcp.tool(annotations=LEITURA)
def alertas() -> str:
    """Tudo que merece atenção agora: orçamentos estourando, saldo baixo, fatura vencendo,
    gastos fora do padrão e autorização do Open Finance perto de expirar."""
    aviso = _garantir_dados()
    al = _lista_alertas()
    return ("\n".join(al) if al else "Nenhum alerta. Tudo em ordem. ✅") + aviso


def gerar_planilha(de: str = "", ate: str = "", meses: int = 6):
    """Monta a planilha. Retorna (workbook, de, ate, nº de transações, nº de meses)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    hoje = hoje_br()
    if not de:
        inicio = hoje.replace(day=1)
        for _ in range(max(1, meses) - 1):
            inicio = (inicio - timedelta(days=1)).replace(day=1)
        de = inicio.isoformat()
    ate = ate or hoje.isoformat()
    txs = sorted(store.transacoes(de, ate), key=lambda t: t["data"])
    wb = Workbook()
    cab = Font(bold=True, color="FFFFFF")
    fundo = PatternFill("solid", fgColor="1F4E78")
    fmt = '"R$" #,##0.00;[Red]-"R$" #,##0.00'

    def cabecalho(ws, nomes, larguras):
        ws.append(nomes)
        for i, w in enumerate(larguras, 1):
            ws.cell(1, i).font, ws.cell(1, i).fill = cab, fundo
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Transacoes"
    cabecalho(ws, ["Data", "Mês", "Banco", "Conta", "Descrição", "Estabelecimento", "Categoria",
                   "Tipo", "Valor", "Valor com sinal", "Conta como gasto?", "Parcela"],
              [12, 9, 12, 18, 38, 20, 24, 9, 13, 15, 16, 9])
    for t in txs:
        sinal = -t["valor"] if t["direcao"] == "saida" else t["valor"]
        ws.append([date.fromisoformat(t["data"]), t["data"][:7], t.get("banco"), t.get("conta"), t["descricao"],
                   t.get("estabelecimento"), t["categoria"], t["direcao"], t["valor"], sinal,
                   "Não" if t["neutra"] else "Sim",
                   f"{t['parcela_atual']}/{t['parcelas_total']}" if t.get("parcelas_total") else None])
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 1).number_format = "DD/MM/YYYY"
        ws.cell(r, 9).number_format = ws.cell(r, 10).number_format = fmt
    ws.auto_filter.ref = ws.dimensions

    meses_lst = sorted({t["data"][:7] for t in txs})
    matriz: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for m in meses_lst:
        for c, v in _por_categoria([t for t in txs if t["data"][:7] == m]).items():
            matriz[c][m] = v
    ws2 = wb.create_sheet("Categoria x Mes")
    cabecalho(ws2, ["Categoria"] + meses_lst + ["Média"], [26] + [13] * (len(meses_lst) + 1))
    for c in sorted(matriz, key=lambda c: -sum(matriz[c].values())):
        vals = [round(matriz[c].get(m, 0), 2) for m in meses_lst]
        ws2.append([c] + vals + [round(sum(vals) / len(vals), 2)])
    for row in ws2.iter_rows(min_row=2, min_col=2):
        for cell in row:
            cell.number_format = fmt

    ws3 = wb.create_sheet("Resumo mensal")
    cabecalho(ws3, ["Mês", "Receitas", "Gastos", "Sobra", "Taxa de poupança"], [10, 14, 14, 14, 16])
    for m in meses_lst:
        rec, desp = _fluxo([t for t in txs if t["data"][:7] == m])
        ws3.append([m, rec, desp, rec - desp, (rec - desp) / rec if rec else None])
    for r in range(2, ws3.max_row + 1):
        for col in (2, 3, 4):
            ws3.cell(r, col).number_format = fmt
        ws3.cell(r, 5).number_format = "0.0%"

    return wb, de, ate, len(txs), len(meses_lst)


def assinar_link(de: str, ate: str, validade_h: int = 24) -> str:
    """Link de download temporário e assinado (não expõe o token principal)."""
    import hashlib
    import hmac
    import time
    import urllib.parse

    exp = int(time.time()) + validade_h * 3600
    msg = f"{de}|{ate}|{exp}".encode()
    sig = hmac.new(os.environ["MEU_PIERRE_TOKEN"].encode(), msg, hashlib.sha256).hexdigest()
    base = os.environ.get("PUBLIC_URL") or "https://" + os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")
    q = urllib.parse.urlencode({"de": de, "ate": ate, "exp": exp, "sig": sig})
    return f"{base.rstrip('/')}/exportar.xlsx?{q}"


@mcp.tool(annotations=LEITURA)
def exportar_excel(de: str = "", ate: str = "", meses: int = 6) -> str:
    """Gera uma planilha .xlsx com transações, gastos por categoria x mês e resumo mensal,
    pronta para Excel ou Power BI. Sem datas, exporta os últimos `meses` meses.
    No computador salva o arquivo; na nuvem devolve um link de download válido por 24h."""
    aviso = _garantir_dados()
    wb, de, ate, n_tx, n_meses = gerar_planilha(de, ate, meses)
    if NUVEM:
        return (f"Planilha pronta ({n_tx} transações, {n_meses} meses). Baixe aqui (link válido por 24h): "
                f"{assinar_link(de, ate)}" + aviso)
    pasta = PASTA / "exportacoes"
    pasta.mkdir(exist_ok=True)
    arq = pasta / f"financas_{de}_a_{ate}.xlsx"
    wb.save(arq)
    return f"Planilha salva em: {arq}\n{n_tx} transações, {n_meses} meses." + aviso


# ================================================================ PROMPTS
@mcp.prompt()
def checkup_financeiro() -> str:
    """Análise completa das finanças, no estilo de um consultor."""
    return ("Faça um check-up das minhas finanças: use visao_geral, resumo_mensal do mês passado, "
            "evolucao_mensal, assinaturas_recorrentes, acompanhar_orcamentos e alertas. "
            "Depois me diga, de forma direta: 1) como estou, 2) os 3 pontos que mais pesam, "
            "3) quanto dá para economizar por mês e onde, 4) próximos passos concretos.")


@mcp.prompt()
def fechamento_do_mes() -> str:
    """Fechamento do mês anterior com destaques e sugestão de orçamentos."""
    return ("Faça o fechamento do mês passado: resumo_mensal('mes passado'), comparar com o mês "
            "anterior a ele, maiores_gastos e acompanhar_orcamentos('mes passado'). Destaque o que "
            "mudou e proponha orçamentos realistas para este mês por categoria.")
