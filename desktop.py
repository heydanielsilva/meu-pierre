"""Roda o Meu Pierre no seu computador, ligado ao Claude Desktop (MCP via stdio).

Uso:
  python desktop.py               -> servidor para o Claude Desktop
  python desktop.py --teste       -> imprime o panorama no terminal
  python desktop.py --sync 365    -> sincroniza N dias de histórico (útil para a carga inicial
                                     no banco da nuvem: coloque o DATABASE_URL do Neon no .env)
"""
import sys

import pierre

if __name__ == "__main__":
    if "--teste" in sys.argv:
        print(pierre.visao_geral())
    elif "--sync" in sys.argv:
        i = sys.argv.index("--sync")
        dias = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 365
        destino = "Postgres (nuvem)" if pierre.store.postgres else "SQLite (local)"
        print(f"Sincronizando {dias} dias no {destino}...")
        print(pierre.sincronizar(dias))
    else:
        pierre.mcp.run()
