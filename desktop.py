"""Roda o Meu Pierre no seu computador.

Uso:
  python desktop.py               -> abre o painel em http://localhost:8000
  python desktop.py --teste       -> imprime o panorama no terminal
  python desktop.py --sync 365    -> sincroniza N dias de histórico (útil para a carga inicial
                                     no banco da nuvem: coloque o DATABASE_URL do Neon no .env)
"""
import sys

import pierre

if __name__ == "__main__":
    if "--teste" in sys.argv:
        p, r = pierre.patrimonio(), pierre.resumo_mes()
        print(f"Patrimônio líquido: {pierre.brl(p['liquido'])} (contas {pierre.brl(p['saldo'])}, "
              f"investimentos {pierre.brl(p['investimentos'])}, faturas {pierre.brl(p['faturas'])})")
        print(f"{r['mes']}: receitas {pierre.brl(r['receitas'])} · gastos {pierre.brl(r['gastos'])} · "
              f"sobra {pierre.brl(r['sobra'])}")
        for a in pierre.alertas():
            print(f"- {a['texto']}")
    elif "--sync" in sys.argv:
        i = sys.argv.index("--sync")
        dias = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 365
        destino = "Postgres (nuvem)" if pierre.store.postgres else "SQLite (local)"
        print(f"Sincronizando {dias} dias no {destino}...")
        print(pierre.sincronizar(dias))
    else:
        import uvicorn

        print("Painel em http://localhost:8000")
        uvicorn.run("app:app", host="127.0.0.1", port=8000)
