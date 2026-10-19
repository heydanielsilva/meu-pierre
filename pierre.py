"""Meu Pierre — núcleo: sincronização e análises de finanças pessoais usadas pelo painel.

Lê suas contas pelo Open Finance via Meu Pluggy (gratuito para uso pessoal),
guarda um cache no banco (SQLite no computador, Postgres na Vercel) e calcula
os números que o painel mostra: saldos, gastos, orçamentos, assinaturas e investimentos.

SOMENTE LEITURA: nada aqui movimenta dinheiro.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

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
SALDO_MINIMO = float(os.environ.get("SALDO_MINIMO") or "500")


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


def _db_url() -> str:
    """DATABASE_URL ou POSTGRES_URL, inclusive com o prefixo que a Vercel adiciona ao conectar o Neon
    (ex.: DATABASE_URL_DATABASE_URL, STORAGE_POSTGRES_URL)."""
    postgres = ("postgres://", "postgresql://")
    for nome in ("DATABASE_URL", "POSTGRES_URL"):
        if os.environ.get(nome, "").startswith(postgres):
            return os.environ[nome]
    for nome, valor in sorted(os.environ.items()):
        if nome.endswith(("DATABASE_URL", "POSTGRES_URL")) and valor.startswith(postgres):
            return valor
    return ""


DB_URL = _db_url()
if DB_URL and not DEMO:
    store = Store(DB_URL)
elif NUVEM and not DEMO:
    raise RuntimeError("Na Vercel é preciso um banco Postgres: conecte um banco Neon em Storage e faça Redeploy.")
else:
    db_nome = "/tmp/meu_pierre_demo.db" if (DEMO and NUVEM) else PASTA / ("meu_pierre_demo.db" if DEMO else "meu_pierre.db")
    store = Store(os.environ.get("MEU_PIERRE_DB", db_nome))
if DEMO:
    store.popular_demo()


# ----------------------------------------------------------------- helpers
def brl(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {s}" if v < 0 else f"R$ {s}"


def _mes(mes: str = "") -> tuple[str, str, str]:
    """'' (mês atual) ou 'AAAA-MM'  ->  ('AAAA-MM', primeiro dia, último dia)."""
    hoje = hoje_br()
    ano, num = hoje.year, hoje.month
    if mes:
        if not re.fullmatch(r"\d{4}-\d{1,2}", mes.strip()):
            raise ValueError(f"Mês inválido '{mes}'. Use AAAA-MM, ex.: 2026-10.")
        ano, num = map(int, mes.strip().split("-"))
    ult = calendar.monthrange(ano, num)[1]
    return f"{ano}-{num:02d}", f"{ano}-{num:02d}-01", f"{ano}-{num:02d}-{ult}"


def _mes_anterior(rotulo: str) -> str:
    return (date.fromisoformat(f"{rotulo}-01") - timedelta(days=1)).strftime("%Y-%m")


def garantir_dados() -> str:
    """Sincroniza sozinho se nunca sincronizou ou se faz mais de 24h. Devolve um aviso se falhar."""
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
        return f"Não consegui atualizar os dados agora: {e}. Mostrando o último cache."


def _sync(dias: int) -> dict:
    itens = _itens()
    if not itens:
        raise RuntimeError("Nenhum item configurado em PLUGGY_ITEMS.")
    cli = PluggyClient(os.environ.get("PLUGGY_CLIENT_ID", ""), os.environ.get("PLUGGY_CLIENT_SECRET", ""))
    return store.sincronizar(cli, itens, dias)


def sincronizar(dias: int = 30) -> dict:
    """Busca dados novos no Open Finance (Meu Pluggy). O Meu Pluggy atualiza os bancos 1x por dia."""
    if DEMO:
        return {"contas": 0, "transacoes": 0, "investimentos": 0, "faturas": 0,
                "erros": ["Modo demonstração: os dados são fictícios e não há o que sincronizar."]}
    return _sync(max(1, min(dias, 365)))


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


def _chave_recorrente(t: dict) -> str:
    base = t.get("estabelecimento") or t["descricao"]
    base = _sem_acento(base)
    base = re.sub(r"[\d*#.\-_/]+", " ", base)
    return " ".join(base.split()[:3])


# ================================================================ ANÁLISES
def meses_disponiveis() -> list[str]:
    """Meses com transações no cache, do mais recente para o mais antigo (inclui sempre o atual)."""
    r = store.con.execute("SELECT MIN(data) de FROM transactions").fetchone()
    atual = hoje_br().replace(day=1)
    m = date.fromisoformat(r["de"][:7] + "-01") if r and r["de"] else atual
    out = []
    while m <= atual:
        out.append(m.strftime("%Y-%m"))
        m = (m + timedelta(days=32)).replace(day=1)
    return out[::-1]


def status_conexoes() -> list[dict]:
    """Cada banco conectado: última atualização, erro e quando a autorização expira."""
    hoje = hoje_br()
    out = []
    for r in store.con.execute("SELECT * FROM items").fetchall():
        exp = r["consentimento_expira"]
        out.append({"banco": r["banco"] or r["id"], "status": r["status"],
                    "atualizado": r["ultima_atualizacao"], "expira": exp[:10] if exp else None,
                    "dias_para_expirar": (date.fromisoformat(exp[:10]) - hoje).days if exp else None,
                    "erro": r["erro"]})
    return out


def contas() -> list[dict]:
    """Contas correntes, poupanças e cartões com saldo, limite e vencimento."""
    out = []
    for c in store.con.execute("SELECT * FROM accounts ORDER BY banco, tipo").fetchall():
        cartao = c["subtipo"] == "CREDIT_CARD"
        out.append({"banco": c["banco"], "nome": c["nome"], "cartao": cartao, "tipo": c["tipo"],
                    "final": str(c["numero"] or "")[-4:], "saldo": c["saldo"], "limite": c["limite"],
                    "limite_disponivel": c["limite_disponivel"], "vencimento": c["vencimento_fatura"],
                    "fechamento": c["fechamento_fatura"],
                    "uso_limite": (1 - (c["limite_disponivel"] or 0) / c["limite"]) if cartao and c["limite"] else None})
    return out


def patrimonio() -> dict:
    lst = contas()
    saldo = sum(c["saldo"] or 0 for c in lst if c["tipo"] == "BANK")
    faturas = sum(c["saldo"] or 0 for c in lst if c["cartao"])
    invs = store.con.execute("SELECT SUM(saldo) s FROM investments").fetchone()["s"] or 0
    return {"saldo": saldo, "investimentos": invs, "faturas": faturas, "liquido": saldo + invs - faturas}


def resumo_mes(mes: str = "") -> dict:
    """Receitas, gastos, sobra, taxa de poupança, categorias e estabelecimentos, comparando com o mês anterior."""
    rotulo, de, ate = _mes(mes)
    txs = store.transacoes(de, ate)
    rec, desp = _fluxo(txs)
    _, de_a, ate_a = _mes(_mes_anterior(rotulo))
    txs_a = store.transacoes(de_a, ate_a)
    rec_a, desp_a = _fluxo(txs_a)
    cats, cats_a = _por_categoria(txs), _por_categoria(txs_a)
    estab: dict[str, float] = defaultdict(float)
    for t in txs:
        if t["direcao"] == "saida" and not t["neutra"]:
            estab[t.get("estabelecimento") or t["descricao"]] += t["valor"]
    hoje = hoje_br()
    atual = rotulo == hoje.strftime("%Y-%m")
    return {
        "mes": rotulo, "atual": atual, "dia": hoje.day if atual else None,
        "dias_mes": calendar.monthrange(int(rotulo[:4]), int(rotulo[5:]))[1],
        "receitas": rec, "gastos": desp, "sobra": rec - desp,
        "poupanca": (rec - desp) / rec if rec else None,
        "receitas_anterior": rec_a, "gastos_anterior": desp_a,
        "projecao": _projecao_mes(txs) if atual else None,
        "categorias": [{"categoria": c, "valor": v, "anterior": cats_a.get(c, 0.0)} for c, v in cats.items()],
        "estabelecimentos": [{"nome": e, "valor": v} for e, v in sorted(estab.items(), key=lambda kv: -kv[1])[:10]],
        "transacoes": txs,
    }


def evolucao_mensal(meses: int = 12) -> list[dict]:
    """Receitas, gastos e sobra mês a mês nos últimos N meses (o último é o atual, parcial)."""
    m = hoje_br().replace(day=1)
    lista = []
    for _ in range(meses):
        lista.append(m.strftime("%Y-%m"))
        m = (m - timedelta(days=1)).replace(day=1)
    out = []
    for rot in reversed(lista):
        _, de, ate = _mes(rot)
        rec, desp = _fluxo(store.transacoes(de, ate))
        out.append({"mes": rot, "receitas": rec, "gastos": desp, "sobra": rec - desp})
    return out


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


def recorrentes() -> list[dict]:
    """Assinaturas e contas fixas dos últimos 6 meses, prontas para o painel."""
    return [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items() if k != "chave"}
            for r in _recorrentes()]


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


def parcelas_futuras() -> list[dict]:
    """Compras parceladas em andamento e quanto ainda falta pagar em cada uma."""
    rows = store.con.execute("SELECT * FROM transactions WHERE parcelas_total > 1 ORDER BY data DESC").fetchall()
    ultimas: dict = {}
    for r in rows:
        k = _chave_recorrente(dict(r))
        if k not in ultimas or r["parcela_atual"] > ultimas[k]["parcela_atual"]:
            ultimas[k] = r
    out = []
    for r in ultimas.values():
        rest = r["parcelas_total"] - r["parcela_atual"]
        if rest > 0:
            out.append({"descricao": r["descricao"], "parcela": r["parcela_atual"], "total_parcelas": r["parcelas_total"],
                        "valor": r["valor"], "restantes": rest, "falta": rest * r["valor"]})
    return sorted(out, key=lambda p: -p["falta"])


def faturas() -> list[dict]:
    """Faturas anteriores de cada cartão."""
    out = []
    for c in store.con.execute("SELECT * FROM accounts WHERE subtipo='CREDIT_CARD'").fetchall():
        for b in store.con.execute("SELECT * FROM bills WHERE account_id=? ORDER BY vencimento DESC LIMIT 6", (c["id"],)):
            out.append({"cartao": f"{c['banco']} · {c['nome']}", "vencimento": b["vencimento"],
                        "valor": b["valor"], "minimo": b["pagamento_minimo"]})
    return out


def investimentos() -> list[dict]:
    """Carteira: saldo por aplicação e rentabilidade no mês e em 12 meses."""
    return [{"nome": r["nome"], "banco": r["banco"], "tipo": r["tipo"], "saldo": r["saldo"],
             "aplicado": r["valor_aplicado"], "rent_mes": r["rent_mes"], "rent_12m": r["rent_12m"],
             "vencimento": r["vencimento"]}
            for r in store.con.execute("SELECT * FROM investments ORDER BY saldo DESC").fetchall()]


def definir_orcamento(categoria: str, valor_mensal: float) -> None:
    """Cria ou altera o limite mensal de uma categoria. valor_mensal <= 0 remove o orçamento."""
    if valor_mensal <= 0:
        store.con.execute("DELETE FROM orcamentos WHERE lower(categoria)=lower(?)", (categoria,))
    else:
        store.con.execute("INSERT OR REPLACE INTO orcamentos VALUES (?,?)", (categoria.strip(), valor_mensal))
    store.con.commit()


def status_orcamentos(mes: str = "") -> list[dict]:
    """Quanto já foi gasto de cada orçamento, projeção até o fim do mês e quanto ainda dá por dia."""
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
                    "uso": gasto / r["valor"], "projecao": proj, "atual": atual,
                    "restante_dia": (r["valor"] - gasto) / max(1, dias_mes - hoje.day + 1) if atual else None})
    return out


def criar_regra(texto: str, categoria: str) -> None:
    """Toda transação cuja descrição contenha `texto` passa a ser `categoria`, no histórico todo."""
    store.add_regra(texto, categoria)


def remover_regra(texto: str) -> int:
    return store.del_regra(texto)


def regras() -> list[dict]:
    return [{"texto": r["padrao"], "categoria": r["categoria"]}
            for r in store.con.execute("SELECT padrao, categoria FROM regras ORDER BY categoria").fetchall()]


def alertas() -> list[dict]:
    """Orçamentos estourando, saldo baixo, fatura vencendo, gastos fora do padrão e autorização expirando."""
    al: list[dict] = []

    def add(nivel: str, texto: str) -> None:
        al.append({"nivel": nivel, "texto": texto})

    hoje = hoje_br()
    for s in status_orcamentos():
        if s["uso"] >= 1:
            add("alto", f"Orçamento de {s['categoria']} estourado: {brl(s['gasto'])} de {brl(s['limite'])}.")
        elif s["projecao"] > s["limite"] * 1.05:
            add("medio", f"No ritmo atual, {s['categoria']} vai fechar o mês em {brl(s['projecao'])} (limite {brl(s['limite'])}).")
    for c in store.con.execute("SELECT * FROM accounts").fetchall():
        if c["tipo"] == "BANK" and c["saldo"] is not None and c["saldo"] < SALDO_MINIMO:
            add("medio", f"Saldo baixo em {c['banco']} · {c['nome']}: {brl(c['saldo'])}.")
        if c["subtipo"] == "CREDIT_CARD" and c["vencimento_fatura"]:
            d = (date.fromisoformat(c["vencimento_fatura"]) - hoje).days
            if 0 <= d <= 7:
                add("medio", f"Fatura {c['banco']} de {brl(c['saldo'])} vence em {d} dia(s) ({c['vencimento_fatura']}).")
    for it in store.con.execute("SELECT * FROM items").fetchall():
        if it["consentimento_expira"]:
            d = (date.fromisoformat(it["consentimento_expira"][:10]) - hoje).days
            if d <= 30:
                add("medio", f"Autorização do Meu Pluggy ({it['banco'] or it['id']}) expira em {d} dias.")
        if it["erro"]:
            add("alto", f"Conexão {it['banco'] or it['id']} com erro: {it['erro']}")
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
                add("medio", f"Gasto fora do padrão: {t['descricao']} {brl(t['valor'])} em {t['data']} "
                             f"(normal em {t['categoria']}: ~{brl(med)}).")
    ult = store.get_meta("ultima_sync")
    if not DEMO and ult and agora_br() - datetime.fromisoformat(ult) > timedelta(days=3):
        add("medio", f"Dados desatualizados desde {ult}. Clique em Atualizar.")
    return al


def painel(mes: str = "") -> dict:
    """Tudo que o painel mostra, de uma vez."""
    aviso = garantir_dados()
    return {
        "aviso": aviso,
        "demo": DEMO,
        "ultima_sync": store.get_meta("ultima_sync"),
        "ultimo_resultado": json.loads(store.get_meta("ultimo_resultado") or "null"),
        "total_transacoes": store.con.execute("SELECT COUNT(*) n FROM transactions").fetchone()["n"],
        "meses": meses_disponiveis(),
        "patrimonio": patrimonio(),
        "resumo": resumo_mes(mes),
        "evolucao": evolucao_mensal(12),
        "contas": contas(),
        "conexoes": status_conexoes(),
        "orcamentos": status_orcamentos(mes),
        "recorrentes": recorrentes(),
        "parcelas": parcelas_futuras(),
        "faturas": faturas(),
        "investimentos": investimentos(),
        "regras": regras(),
        "alertas": alertas(),
        "neutras": sorted(NEUTRAS),
    }


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
