"""Banco de dados com o cache das suas contas, transações e configurações.

- No computador: SQLite (arquivo meu_pierre.db).
- Na nuvem (Vercel): Postgres, ex. Neon, via DATABASE_URL.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")


def hoje_br() -> date:
    """Data de hoje no horário de Brasília (a Vercel roda em UTC)."""
    return datetime.now(FUSO).date()


def agora_br() -> datetime:
    return datetime.now(FUSO).replace(tzinfo=None, microsecond=0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, banco TEXT, status TEXT, ultima_atualizacao TEXT,
    consentimento_expira TEXT, erro TEXT
);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, item_id TEXT, banco TEXT, nome TEXT, tipo TEXT,
    subtipo TEXT, numero TEXT, saldo REAL, limite REAL, limite_disponivel REAL,
    vencimento_fatura TEXT, fechamento_fatura TEXT, atualizado_em TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, account_id TEXT, data TEXT, descricao TEXT,
    valor REAL, direcao TEXT, categoria_origem TEXT, estabelecimento TEXT,
    parcela_atual INTEGER, parcelas_total INTEGER
);
CREATE INDEX IF NOT EXISTS ix_tx_data ON transactions(data);
CREATE TABLE IF NOT EXISTS investments (
    id TEXT PRIMARY KEY, item_id TEXT, banco TEXT, nome TEXT, tipo TEXT,
    subtipo TEXT, saldo REAL, valor_aplicado REAL, rent_mes REAL,
    rent_12m REAL, vencimento TEXT, atualizado_em TEXT
);
CREATE TABLE IF NOT EXISTS bills (
    id TEXT PRIMARY KEY, account_id TEXT, vencimento TEXT, valor REAL,
    pagamento_minimo REAL
);
CREATE TABLE IF NOT EXISTS regras (
    id INTEGER PRIMARY KEY AUTOINCREMENT, padrao TEXT UNIQUE, categoria TEXT
);
CREATE TABLE IF NOT EXISTS orcamentos (categoria TEXT PRIMARY KEY, valor REAL);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
"""

# Categorias da Pluggy (em inglês) -> português. O que não estiver aqui
# aparece com o nome original; crie uma regra para renomear.
TRADUCAO = {
    "income": "Receitas", "salary": "Salário", "retirement": "Aposentadoria",
    "entrepreneurial activities": "Renda extra", "government aid": "Benefícios",
    "non-recurring income": "Receitas eventuais", "loans and financing": "Empréstimos e financiamentos",
    "loans": "Empréstimos e financiamentos", "financing": "Empréstimos e financiamentos",
    "investments": "Investimentos", "automatic investment": "Investimentos",
    "fixed income": "Investimentos", "mutual funds": "Investimentos", "variable income": "Investimentos",
    "margin": "Investimentos", "proceeds interests and dividends": "Rendimentos",
    "pension": "Previdência", "same person transfer": "Transferência entre contas próprias",
    "same person transfer - pix": "Transferência entre contas próprias",
    "same person transfer - ted": "Transferência entre contas próprias",
    "transfers": "Transferências", "transfer - pix": "Transferências", "transfer - ted": "Transferências",
    "transfer - bank slip": "Boletos", "transfer - cash": "Saques", "transfer - check": "Transferências",
    "transfer - foreign exchange": "Câmbio", "transfer - internal": "Transferências",
    "credit card payment": "Pagamento de fatura", "legal obligations": "Obrigações legais",
    "blocked balances": "Saldo bloqueado", "alimony": "Pensão",
    "services": "Serviços", "telecommunications": "Internet e telefone", "internet": "Internet e telefone",
    "mobile": "Internet e telefone", "tv": "Assinaturas e streaming", "education": "Educação",
    "online courses": "Educação", "university": "Educação", "school": "Educação",
    "kindergarten": "Educação", "wellness and fitness": "Saúde e bem-estar",
    "gyms and fitness centers": "Academia", "sports practice": "Esportes",
    "wellness": "Saúde e bem-estar", "tickets": "Lazer", "stadiums and arenas": "Lazer",
    "landmarks and museums": "Lazer", "cinema, theater and concerts": "Lazer",
    "leisure": "Lazer", "shopping": "Compras", "online shopping": "Compras online",
    "electronics": "Eletrônicos", "clothing": "Roupas", "pet supplies and vet": "Pets",
    "houseware": "Casa", "bookstore": "Livros", "sporting goods": "Esportes",
    "office supplies": "Papelaria", "digital services": "Assinaturas e streaming",
    "gaming": "Jogos", "video streaming": "Assinaturas e streaming",
    "music streaming": "Assinaturas e streaming", "groceries": "Supermercado",
    "food and drinks": "Alimentação", "eating out": "Restaurantes",
    "food delivery": "Delivery", "travel": "Viagens", "airport and airlines": "Viagens",
    "accommodation": "Viagens", "accomodation": "Viagens", "mileage programs": "Viagens", "bus tickets": "Viagens",
    "donations": "Doações", "gambling": "Apostas", "lottery": "Apostas", "online bet": "Apostas",
    "taxes": "Impostos", "income taxes": "Impostos", "taxes on investments": "Impostos",
    "tax on financial operations": "Impostos", "bank fees": "Tarifas bancárias",
    "account fees": "Tarifas bancárias", "wire transfer fees and atm fees": "Tarifas bancárias",
    "credit card fees": "Tarifas bancárias", "late payment and overdraft costs": "Juros e multas",
    "interests charged": "Juros e multas", "housing": "Moradia", "rent": "Aluguel",
    "utilities": "Contas da casa", "water": "Contas da casa", "electricity": "Contas da casa",
    "gas": "Contas da casa", "houseware and home improvement": "Casa", "home improvement": "Casa",
    "healthcare": "Saúde", "dentist": "Saúde", "pharmacy": "Farmácia", "optometry": "Saúde",
    "hospital clinics and labs": "Saúde", "transportation": "Transporte", "taxi and ride-hailing": "Transporte por app",
    "public transportation": "Transporte", "car rental": "Transporte", "bicycle": "Transporte",
    "automotive": "Carro", "gas stations": "Combustível", "parking": "Carro", "tolls and in vehicle payment": "Pedágio",
    "vehicle ownership taxes and fees": "Carro", "vehicle maintenance": "Carro",
    "insurance": "Seguros", "life insurance": "Seguros", "home insurance": "Seguros",
    "health insurance": "Plano de saúde", "vehicle insurance": "Seguros",
    "other": "Outros",
}

# Categorias que NÃO contam como receita nem despesa (só movem dinheiro entre
# você e você mesmo), para não duplicar gastos do cartão.
NEUTRAS = {
    "Pagamento de fatura", "Transferência entre contas próprias", "Investimentos",
    "Saldo bloqueado",
}

# Sobe quando muda a forma de interpretar as transações da Pluggy: a próxima
# atualização busca 12 meses e regrava tudo com a regra nova.
VERSAO_TRANSACOES = "2"

PADROES_FATURA = re.compile(r"(PAG(AMEN)?T?O?\.?\s*(DE\s*)?FATURA|PGTO\.?\s*FATURA|FATURA\s+PAGA)", re.I)


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


# Chave primária de cada tabela, para traduzir "INSERT OR REPLACE" no Postgres.
CHAVES = {"items": "id", "accounts": "id", "transactions": "id", "investments": "id",
          "bills": "id", "orcamentos": "categoria", "meta": "chave"}
_COLUNAS: dict[str, list[str]] = {}
for _bloco in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\);", SCHEMA, re.S):
    _COLUNAS[_bloco[0]] = [c.strip().split()[0] for c in _bloco[1].split(",") if c.strip()]
_UPSERT = re.compile(r"INSERT OR REPLACE INTO (\w+) VALUES", re.I)


class _CursorPg:
    def __init__(self, cur):
        self._cur = cur

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    @property
    def rowcount(self):
        return self._cur.rowcount

    def __iter__(self):
        return iter(self.fetchall())


class ConexaoPg:
    """Imita a parte da API do sqlite3 usada aqui, traduzindo o SQL para Postgres."""

    def __init__(self, url: str):
        self.url = url
        self._con = None

    def _c(self):
        import psycopg
        from psycopg.rows import dict_row

        if self._con is None or self._con.closed:
            self._con = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=15)
        return self._con

    @staticmethod
    def traduzir(sql: str) -> str:
        m = _UPSERT.search(sql)
        sql = sql.replace("?", "%s")
        if m:
            tabela = m.group(1)
            pk = CHAVES[tabela]
            sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in _COLUNAS[tabela] if c != pk)
            sql = _UPSERT.sub(f"INSERT INTO {tabela} VALUES", sql) + f" ON CONFLICT ({pk}) DO UPDATE SET {sets}"
        return sql

    def execute(self, sql: str, params=()):
        con = self._c()
        try:
            cur = con.cursor()
            cur.execute(self.traduzir(sql), params)
            return _CursorPg(cur)
        except Exception:
            con.rollback()
            raise

    def executemany(self, sql: str, seq):
        con = self._c()
        try:
            con.cursor().executemany(self.traduzir(sql), list(seq))
        except Exception:
            con.rollback()
            raise

    def executescript(self, script: str):
        script = script.replace(" REAL", " DOUBLE PRECISION").replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        for comando in script.split(";"):
            if comando.strip():
                self.execute(comando)
        self.commit()

    def commit(self):
        self._c().commit()


class Store:
    def __init__(self, destino: str | Path):
        destino = str(destino)
        self.postgres = destino.startswith(("postgres://", "postgresql://"))
        if self.postgres:
            self.con = ConexaoPg(destino)
        else:
            self.con = sqlite3.connect(destino, check_same_thread=False)
            self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        self._regras_cache: list[tuple[str, str]] | None = None

    # -------------------------------------------------------------- meta
    def get_meta(self, chave: str) -> str | None:
        r = self.con.execute("SELECT valor FROM meta WHERE chave=?", (chave,)).fetchone()
        return r["valor"] if r else None

    def set_meta(self, chave: str, valor: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (chave, valor))
        self.con.commit()

    # ------------------------------------------------------- categorias
    def regras(self) -> list[tuple[str, str]]:
        if self._regras_cache is None:
            self._regras_cache = [
                (_sem_acento(r["padrao"]), r["categoria"])
                for r in self.con.execute("SELECT padrao, categoria FROM regras ORDER BY length(padrao) DESC")
            ]
        return self._regras_cache

    def add_regra(self, padrao: str, categoria: str) -> None:
        self.con.execute(
            "INSERT INTO regras (padrao, categoria) VALUES (?,?) "
            "ON CONFLICT(padrao) DO UPDATE SET categoria=excluded.categoria",
            (padrao.strip(), categoria.strip()),
        )
        self.con.commit()
        self._regras_cache = None

    def del_regra(self, padrao: str) -> int:
        n = self.con.execute("DELETE FROM regras WHERE lower(padrao)=lower(?)", (padrao.strip(),)).rowcount
        self.con.commit()
        self._regras_cache = None
        return n

    def categoria(self, tx: sqlite3.Row | dict) -> str:
        desc = _sem_acento(f"{tx['descricao']} {tx['estabelecimento'] or ''}")
        for padrao, cat in self.regras():
            if padrao in desc:
                return cat
        if PADROES_FATURA.search(tx["descricao"] or ""):
            return "Pagamento de fatura"
        orig = (tx["categoria_origem"] or "").strip()
        if not orig:
            return "Outros"
        return TRADUCAO.get(orig.lower(), orig)

    def transacoes(self, de: str, ate: str) -> list[dict]:
        """Transações do período já com categoria final e flag de neutralidade."""
        rows = self.con.execute(
            """SELECT t.*, a.banco, a.nome AS conta, a.subtipo
               FROM transactions t LEFT JOIN accounts a ON a.id = t.account_id
               WHERE t.data BETWEEN ? AND ? ORDER BY t.data DESC, t.valor DESC""",
            (de, ate),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            cat = self.categoria(r)
            # No cartão, crédito é pagamento da fatura (neutro) ou estorno.
            if d["subtipo"] == "CREDIT_CARD" and d["direcao"] == "entrada" and cat not in NEUTRAS:
                cat = "Estornos" if "estorno" in _sem_acento(d["descricao"]) else "Pagamento de fatura"
            d["categoria"] = cat
            d["neutra"] = cat in NEUTRAS
            out.append(d)
        return out

    # ------------------------------------------------------------- sync
    def sincronizar(self, cliente, itens: dict[str, str], dias: int) -> dict:
        hoje = hoje_br()
        reprocessar = self.get_meta("versao_transacoes") != VERSAO_TRANSACOES
        if reprocessar:
            dias = max(dias, 365)  # regrava o histórico gravado com a regra antiga
        de = (hoje - timedelta(days=dias)).isoformat()
        ate = hoje.isoformat()
        resumo = {"contas": 0, "transacoes": 0, "investimentos": 0, "faturas": 0, "erros": [], "status": {}}
        agora = agora_br().isoformat()

        for item_id, banco_cfg in itens.items():
            # Cada banco é gravado e confirmado separadamente: se um falhar (ou a Vercel
            # interromper por tempo), o que já foi salvo dos outros não se perde.
            try:
                self._sincronizar_item(cliente, item_id, banco_cfg, de, ate, agora, resumo)
                self.con.commit()
            except Exception as e:  # noqa: BLE001
                self._desfazer()
                resumo["erros"].append(f"{banco_cfg or item_id}: falha ao gravar no banco de dados: {e}")

        self.set_meta("ultima_sync", agora)
        if reprocessar and not resumo["erros"]:
            self.set_meta("versao_transacoes", VERSAO_TRANSACOES)
        self.registrar_resultado({"em": agora, "dias": dias, **resumo})
        return resumo

    def registrar_resultado(self, resultado: dict) -> None:
        self.set_meta("ultimo_resultado", json.dumps(resultado, ensure_ascii=False))

    def _desfazer(self) -> None:
        try:
            if self.postgres:
                self.con._c().rollback()
            else:
                self.con.rollback()
        except Exception:  # noqa: BLE001
            pass

    def _sincronizar_item(self, cliente, item_id: str, banco_cfg: str, de: str, ate: str,
                          agora: str, resumo: dict) -> None:
        try:
            it = cliente.item(item_id)
        except Exception as e:  # noqa: BLE001
            resumo["erros"].append(f"Item {item_id}: {e}")
        else:
            erro = (it.get("error") or {}).get("message") if it.get("error") else None
            self.con.execute(
                "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)",
                (item_id, banco_cfg or None, it.get("status"), it.get("lastUpdatedAt"),
                 it.get("consentExpiresAt"), erro),
            )
            resumo["status"][banco_cfg or item_id] = it.get("status")

        try:
            contas = cliente.accounts(item_id)
        except Exception as e:  # noqa: BLE001
            resumo["erros"].append(f"Contas do item {item_id}: {e}")
            return

        for c in contas:
            cd = c.get("creditData") or {}
            banco = banco_cfg or c.get("marketingName") or c.get("name") or "Banco"
            self.con.execute(
                "INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["id"], item_id, banco, c.get("marketingName") or c.get("name"),
                 c.get("type"), c.get("subtype"), c.get("number"), c.get("balance"),
                 cd.get("creditLimit"), cd.get("availableCreditLimit"),
                 (cd.get("balanceDueDate") or "")[:10] or None,
                 (cd.get("balanceCloseDate") or "")[:10] or None, agora),
            )
            resumo["contas"] += 1
            try:
                txs = cliente.transactions(c["id"], de, ate)
            except Exception as e:  # noqa: BLE001
                resumo["erros"].append(f"Transações de {banco} · {c.get('name') or c['id']}: {e}")
                txs = []
            # Tudo de uma vez: gravar uma a uma no Postgres da nuvem estoura o tempo da Vercel.
            linhas = [self._linha_tx(c, t) for t in txs if t.get("id")]
            if linhas:
                self.con.executemany("INSERT OR REPLACE INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", linhas)
            resumo["transacoes"] += len(linhas)

            if c.get("subtype") == "CREDIT_CARD":
                try:
                    faturas = cliente.bills(c["id"])
                except Exception:  # noqa: BLE001
                    faturas = []  # nem todo item MeuPluggy expõe /bills
                linhas = [(b["id"], c["id"], (b.get("dueDate") or "")[:10], b.get("totalAmount"),
                           b.get("minimumPaymentAmount")) for b in faturas if b.get("id")]
                if linhas:
                    self.con.executemany("INSERT OR REPLACE INTO bills VALUES (?,?,?,?,?)", linhas)
                resumo["faturas"] += len(linhas)

        try:
            invs = cliente.investments(item_id)
        except Exception as e:  # noqa: BLE001
            resumo["erros"].append(f"Investimentos do item {item_id}: {e}")
            invs = []
        linhas = [(inv["id"], item_id, banco_cfg or None, inv.get("name"), inv.get("type"),
                   inv.get("subtype"), inv.get("balance"), inv.get("amountOriginal"),
                   inv.get("lastMonthRate"), inv.get("lastTwelveMonthsRate"),
                   (inv.get("dueDate") or "")[:10] or None, agora) for inv in invs if inv.get("id")]
        if linhas:
            self.con.executemany("INSERT OR REPLACE INTO investments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", linhas)
        resumo["investimentos"] += len(linhas)

    @staticmethod
    def _linha_tx(conta: dict, t: dict) -> tuple:
        valor = float(t.get("amount") or 0)
        tipo = (t.get("type") or "").upper()
        if conta.get("subtype") == "CREDIT_CARD" or conta.get("type") == "CREDIT":
            # No cartão a Pluggy usa o sinal ao contrário da conta: positivo é compra,
            # negativo é pagamento da fatura ou estorno.
            direcao = "saida" if valor > 0 else "entrada"
        elif tipo in ("DEBIT", "CREDIT"):
            direcao = "saida" if tipo == "DEBIT" else "entrada"
        else:
            direcao = "saida" if valor < 0 else "entrada"
        cc = t.get("creditCardMetadata") or {}
        merchant = t.get("merchant") or {}

        def texto(v):  # o Postgres recusa o caractere nulo
            return v.replace("\x00", "") if isinstance(v, str) else v

        def inteiro(v):
            try:
                return int(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        return (t["id"], conta["id"], (t.get("date") or "")[:10],
                texto(t.get("descriptionRaw") or t.get("description") or ""),
                abs(valor), direcao, texto(t.get("category")),
                texto(merchant.get("businessName") or merchant.get("name")),
                inteiro(cc.get("installmentNumber")), inteiro(cc.get("totalInstallments")))

    # ------------------------------------------------------------- demo
    def popular_demo(self) -> None:
        """Dados fictícios para testar sem conectar banco nenhum."""
        if self.get_meta("demo") == "1":
            return
        rnd = random.Random(42)
        hoje = hoje_br()
        agora = agora_br().isoformat()
        venc = (hoje.replace(day=1) + timedelta(days=40)).replace(day=10)
        self.con.executemany("INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)", [
            ("demo-nu", "Nubank", "UPDATED", agora, (hoje + timedelta(days=300)).isoformat(), None),
            ("demo-itau", "Itaú", "UPDATED", agora, (hoje + timedelta(days=25)).isoformat(), None),
        ])
        self.con.executemany("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("cc-nu", "demo-nu", "Nubank", "Conta Nubank", "BANK", "CHECKING_ACCOUNT", "1234", 3180.45, None, None, None, None, agora),
            ("card-nu", "demo-nu", "Nubank", "Cartão Nubank", "CREDIT", "CREDIT_CARD", "5678", 2350.10, 9000, 6649.90, venc.isoformat(), (venc - timedelta(days=7)).isoformat(), agora),
            ("cc-itau", "demo-itau", "Itaú", "Conta Itaú", "BANK", "CHECKING_ACCOUNT", "9012", 412.80, None, None, None, None, agora),
        ])
        self.con.executemany("INSERT OR REPLACE INTO investments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("inv1", "demo-nu", "Nubank", "Caixinha Reserva", "FIXED_INCOME", "CDB", 18450.32, 17000, 0.0095, 0.118, None, agora),
            ("inv2", "demo-itau", "Itaú", "Tesouro Selic 2029", "FIXED_INCOME", "TREASURY", 12010.77, 11000, 0.0091, 0.114, "2029-03-01", agora),
            ("inv3", "demo-itau", "Itaú", "Fundo Imobiliário XPML11", "EQUITY", "REAL_ESTATE_FUND", 4870.00, 5200, -0.012, 0.031, None, agora),
        ])
        self.con.execute("INSERT OR REPLACE INTO bills VALUES (?,?,?,?,?)", ("bill1", "card-nu", venc.isoformat(), 2350.10, 352.52))

        txs = []
        n = 0

        def add(conta, d, desc, valor, direcao, cat, merch=None, p=None, pt=None):
            nonlocal n
            n += 1
            if d <= hoje:
                txs.append((f"demo{n}", conta, d.isoformat(), desc, round(valor, 2), direcao, cat, merch, p, pt))

        inicio = (hoje.replace(day=1) - timedelta(days=150)).replace(day=1)
        mes = inicio
        while mes <= hoje:
            def dia(x):
                return mes.replace(day=min(x, 28))
            add("cc-itau", dia(5), "SALARIO CONSORCIO", 9200, "entrada", "Salary")
            add("cc-itau", dia(6), "PIX ENVIADO MESMA TITULARIDADE NUBANK", 6000, "saida", "Same person transfer")
            add("cc-nu", dia(6), "PIX RECEBIDO MESMA TITULARIDADE ITAU", 6000, "entrada", "Same person transfer")
            add("cc-nu", dia(7), "ALUGUEL APTO PIX", 1900, "saida", "Rent")
            add("cc-nu", dia(10), "PAGAMENTO DE FATURA NUBANK", rnd.uniform(2100, 2900), "saida", "Credit card payment")
            add("card-nu", dia(10), "Pagamento recebido", rnd.uniform(2100, 2900), "entrada", "Credit card payment")
            add("cc-nu", dia(12), "ENERGISA CONTA DE LUZ", rnd.uniform(180, 320), "saida", "Electricity")
            add("cc-nu", dia(15), "APLICACAO CAIXINHA", 800, "saida", "Automatic investment")
            add("card-nu", dia(3), "Netflix.com", 55.90, "saida", "Video streaming", "Netflix")
            add("card-nu", dia(8), "Spotify", 21.90, "saida", "Music streaming", "Spotify")
            add("card-nu", dia(9), "Claro Internet", 119.90, "saida", "Internet", "Claro")
            add("card-nu", dia(2), "Smartfit", 129.90, "saida", "Gyms and fitness centers", "Smart Fit")
            add("card-nu", dia(20), "Amazon Prime", 19.90, "saida", "Online shopping", "Amazon")
            for _ in range(rnd.randint(7, 11)):
                add("card-nu", dia(rnd.randint(1, 28)), "IFD*IFOOD", rnd.uniform(35, 95), "saida", "Food delivery", "iFood")
            for _ in range(rnd.randint(3, 5)):
                add("card-nu", dia(rnd.randint(1, 28)), rnd.choice(["ATACADAO", "ASSAI ATACADISTA", "SUPERM. BOM PRECO"]), rnd.uniform(120, 480), "saida", "Groceries")
            for _ in range(rnd.randint(4, 8)):
                add("card-nu", dia(rnd.randint(1, 28)), "UBER *TRIP", rnd.uniform(12, 38), "saida", "Taxi and ride-hailing", "Uber")
            for _ in range(rnd.randint(2, 3)):
                add("card-nu", dia(rnd.randint(1, 28)), rnd.choice(["POSTO IPIRANGA", "AUTO POSTO SHELL"]), rnd.uniform(150, 260), "saida", "Gas stations")
            for _ in range(rnd.randint(2, 4)):
                add("card-nu", dia(rnd.randint(1, 28)), rnd.choice(["OUTBACK", "COCO BAMBU", "BAR DO ZE", "PADARIA REAL"]), rnd.uniform(40, 260), "saida", "Eating out")
            add("card-nu", dia(rnd.randint(1, 28)), "DROGASIL", rnd.uniform(30, 140), "saida", "Pharmacy")
            add("cc-nu", dia(rnd.randint(1, 28)), "PIX RECEBIDO FREELA PROJETO", rnd.choice([0, 0, 1200, 850]), "entrada", "Entrepreneurial activities")
            mes = (mes + timedelta(days=32)).replace(day=1)

        # Compra parcelada e um gasto fora da curva no mês atual
        compra = hoje.replace(day=1) - timedelta(days=60)
        for i in range(3):
            add("card-nu", (compra + timedelta(days=30 * i)), "MAGALU NOTEBOOK", 899.67, "saida", "Electronics", "Magazine Luiza", i + 1, 10)
        add("card-nu", hoje - timedelta(days=min(3, hoje.day - 1)), "IFD*IFOOD", 389.00, "saida", "Food delivery", "iFood")
        txs = [t for t in txs if t[4] > 0]
        self.con.executemany("INSERT OR REPLACE INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)", txs)
        self.con.executemany("INSERT OR REPLACE INTO orcamentos VALUES (?,?)", [
            ("Delivery", 600), ("Restaurantes", 400), ("Supermercado", 1200), ("Transporte por app", 200),
        ])
        self.con.commit()
        self.set_meta("demo", "1")
        self.set_meta("ultima_sync", agora)
