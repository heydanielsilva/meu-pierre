# Meu Pierre: assistente de finanças pessoais no Claude

Converse com o Claude sobre as suas contas reais, como no Pierre: no navegador, no celular ou no Claude Desktop.
"Quanto gastei com iFood este mês?", "quais assinaturas eu pago?", "vou estourar o orçamento de supermercado?".

- **Dados:** Open Finance pelo **Meu Pluggy**, gratuito para uso pessoal (até 5 conexões).
- **Na nuvem:** roda na **Vercel** como conector do claude.ai, com banco **Neon** (Postgres) e atualização diária automática.
- **Somente leitura:** não existe nenhuma função de PIX, pagamento ou transferência no código.

## O que ele faz

| Pergunte algo como… | Ferramenta |
|---|---|
| "Como estão minhas finanças?" | `visao_geral`: saldos, investimentos, patrimônio, projeção do mês |
| "Resumo de setembro" | `resumo_mensal`: receitas, gastos, sobra, taxa de poupança, categorias vs mês anterior |
| "Quanto gastei com Uber em agosto?" | `buscar_transacoes` |
| "Onde gastei mais este mês?" | `gastos_por_categoria`, `maiores_gastos` |
| "Compara setembro com outubro" | `comparar_meses` |
| "Como evoluíram meus gastos?" | `evolucao_mensal` |
| "Quais assinaturas eu pago?" | `assinaturas_recorrentes` (separa assinaturas e contas fixas) |
| "Quanto falta de parcelas?" | `parcelas_futuras` |
| "Como está meu cartão?" | `faturas_cartao` |
| "Como estão meus investimentos?" | `investimentos` |
| "Limite de R$ 600 para delivery" | `definir_orcamento`, `acompanhar_orcamentos` (com quanto ainda pode gastar por dia) |
| "Algo que eu deva saber?" | `alertas`: orçamento estourando, saldo baixo, fatura vencendo, gasto fora do padrão |
| "Pix para fulano é aluguel" | `criar_regra_categoria` (corrige categorias para todo o histórico) |
| "Exporta para Excel" | `exportar_excel`: na nuvem devolve um link de download válido por 24h |

Prompts prontos: **checkup_financeiro** e **fechamento_do_mes**.

Pagamento de fatura, transferência entre contas próprias e aplicação em investimento são **neutros**:
não contam como gasto, para a compra no cartão não aparecer duas vezes. Estornos abatem dos gastos.

---

## Passo 1 — Conecte seus bancos no Meu Pluggy

1. Crie conta em **[meu.pluggy.ai](https://meu.pluggy.ai)** e conecte cada banco (cada um é autorizado separadamente, pelo app do banco).
2. Entre no **[dashboard.pluggy.ai](https://dashboard.pluggy.ai)** com **a mesma conta**.
3. Crie uma aplicação em **Applications** e anote o **Client ID** e o **Client Secret** (o Secret só aparece uma vez).
4. Na aplicação, abra **Demo > Connect Account**, escolha o conector **MeuPluggy** (não o seu banco, nem o Sandbox) e faça login com a conta do Meu Pluggy.
5. Cada banco vira um **item**. Em **Connected Items**, copie o id de cada um.

> Se pedir CPF e aprovação do banco de novo, você escolheu o conector do banco. Volte e escolha **MeuPluggy**.

## Passo 2 — Gere duas senhas

No terminal (com Python instalado), rode **duas vezes** e guarde os dois valores:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- O primeiro é o **MEU_PIERRE_TOKEN** (senha do conector).
- O segundo é o **CRON_SECRET** (senha da atualização diária).

## Passo 3 — Suba o código para o GitHub

1. Em [github.com/new](https://github.com/new), crie um repositório **Private** chamado `meu-pierre`.
2. Envie os arquivos desta pasta. O jeito mais fácil é o [GitHub Desktop](https://desktop.github.com): *Add existing repository* → *Publish*.
   Pelo site também dá (*Add file > Upload files*), mas confira se o `.gitignore` e o `vercel.json` subiram.
3. **Nunca** envie o arquivo `.env` nem arquivos `.db`. O `.gitignore` já bloqueia os dois.

## Passo 4 — Publique na Vercel

1. Em [vercel.com](https://vercel.com), entre com a conta do GitHub.
2. **Add New > Project** → importe o repositório `meu-pierre`. A Vercel detecta o app Python sozinha.
3. Antes de clicar em *Deploy*, abra **Environment Variables** e cadastre:
   `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET`, `PLUGGY_ITEMS`, `MEU_PIERRE_TOKEN`, `CRON_SECRET`
   (e, se quiser, `SALDO_MINIMO`). O formato está no `.env.exemplo`.
4. Clique em **Deploy**. O site já sobe, mas as ferramentas só funcionam depois do banco, no próximo passo.
5. No projeto, abra a aba **Storage** → crie um banco **Neon** (Postgres, plano gratuito) e conecte ao projeto.
   Isso cria a variável `DATABASE_URL` sozinha.
6. Em **Deployments**, clique em **Redeploy**.
7. Abra `https://SEU-PROJETO.vercel.app`. Deve aparecer **"Meu Pierre está no ar."**

A partir daqui, todo `git push` para o GitHub publica uma versão nova automaticamente.
A Vercel também roda a sincronização todo dia por volta das 8h (horário de Brasília), pelo `vercel.json`.

## Passo 5 — Carregue o histórico (uma vez)

O cron diário busca só os últimos 30 dias. Para trazer 12 meses de uma vez, rode no seu computador:

```bash
pip install -r requirements.txt
```

Crie um `.env` (copie do `.env.exemplo`) com as credenciais da Pluggy, o `PLUGGY_ITEMS` e o `DATABASE_URL`
do Neon (copie em Vercel > Storage > seu banco > `.env.local`). Depois:

```bash
python desktop.py --sync 365
```

Sem computador à mão, dá para pedir ao Claude "sincronize os últimos 180 dias". Pode demorar, e se passar do tempo
limite da Vercel é só pedir de novo com menos dias.

## Passo 6 — Adicione o conector no Claude

No claude.ai: **Customize > Connectors > + Add > Add custom connector**.

- **Nome:** Meu Pierre
- **URL:** `https://SEU-PROJETO.vercel.app/mcp`
- **Authentication:** *No sign in*
- **Request headers:** nome `Authorization`, valor `Bearer SEU_MEU_PIERRE_TOKEN`

Depois, numa conversa, ative o conector em **+ > Connectors** e pergunte:
**"Faça um check-up das minhas finanças."** O conector também fica disponível no app do Claude no celular.

> Alternativa, se o seu plano não mostrar o campo de cabeçalhos: use a URL
> `https://SEU-PROJETO.vercel.app/mcp/SEU_MEU_PIERRE_TOKEN`, sem cabeçalho.
> Funciona igual, mas o token fica visível na URL; prefira o cabeçalho.

---

## Usar no Claude Desktop, sem nuvem (opcional)

Também funciona 100% local, com SQLite. Deixe `DATABASE_URL` vazio no `.env` e, em
**Configurações > Desenvolvedor > Editar configuração** do Claude Desktop, adicione:

```json
{
  "mcpServers": {
    "meu-pierre": {
      "command": "C:\\caminho\\para\\python.exe",
      "args": ["C:\\caminho\\para\\meu-pierre\\desktop.py"]
    }
  }
}
```

Para testar sem banco nenhum, com dados fictícios: `MEU_PIERRE_DEMO=1 python desktop.py --teste`.

---

## Segurança

- O endpoint só responde com o token certo. Sem ele, devolve 404, como se não existisse.
- Os links de download do Excel são assinados e expiram em 24h. Eles não revelam o token.
- Se o token vazar: gere outro, troque em Vercel > Environment Variables, faça Redeploy e atualize o conector no Claude
  (para editar um conector é preciso remover e adicionar de novo).
- O repositório deve ser **privado**. Credenciais ficam só nas variáveis da Vercel.

## Bom saber

- **A autorização do Meu Pluggy dura 1 ano.** `status_conexoes` e `alertas` avisam quando faltar 30 dias.
  Para renovar, repita o Passo 1.4. Confira se o itemId mudou e atualize `PLUGGY_ITEMS` na Vercel.
- **Categorias:** vêm da Pluggy e são traduzidas. Se algo vier errado, peça: *"crie uma regra: 'PIX JOAO SILVA' é Aluguel"*.
  Para transferências entre suas contas que não foram detectadas, use **"Transferência entre contas próprias"**.
- **Investimentos e faturas** dependem do que cada banco compartilha no Open Finance; alguns podem vir vazios.

## Arquivos

| Arquivo | Função |
|---|---|
| `pierre.py` | As 21 ferramentas e a lógica de análise |
| `store.py` | Banco de dados (SQLite local ou Postgres na nuvem) e categorias |
| `pluggy_client.py` | Cliente da API Pluggy, somente leitura |
| `app.py` | App web da Vercel: endpoint MCP, cron e download do Excel |
| `desktop.py` | Uso local no Claude Desktop e carga inicial do histórico |
| `vercel.json` | Agenda do cron diário |
