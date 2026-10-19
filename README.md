# Meu Pierre: painel de finanças pessoais

Um painel web com as suas contas reais, no navegador do computador ou do celular:
saldos, gastos por categoria, orçamentos, assinaturas, parcelas, faturas e investimentos.

- **Dados:** Open Finance pelo **Meu Pluggy**, gratuito para uso pessoal (até 5 conexões).
- **Na nuvem:** roda na **Vercel**, com banco **Neon** (Postgres) e atualização diária automática.
- **Privado:** o painel só abre com a sua senha.
- **Somente leitura:** não existe nenhuma função de PIX, pagamento ou transferência no código.

## O que o painel mostra

| Seção | O que tem |
|---|---|
| Patrimônio | Saldo em conta, investimentos, faturas em aberto e patrimônio líquido |
| Mês | Receitas, gastos (vs mês anterior), sobra, taxa de poupança e projeção até o fim do mês |
| Gráficos | Receitas x gastos dos últimos 12 meses e gastos por categoria (vs mês anterior) |
| Alertas | Orçamento estourando, saldo baixo, fatura vencendo, gasto fora do padrão, autorização expirando |
| Orçamentos | Limite por categoria, quanto já usou, projeção e quanto ainda pode gastar por dia |
| Transações | Lista do mês com busca e filtros; clique na categoria para corrigir |
| Assinaturas e contas fixas | Detectadas automaticamente, com custo mensal e anual |
| Parcelas, cartões, investimentos | Quanto falta pagar, limite usado, rentabilidade |
| Excel | Botão que baixa uma planilha dos últimos 6 meses |

Escolha o mês no topo (ou clique numa barra do gráfico) para ver meses anteriores.
O botão **Atualizar** busca dados novos na Pluggy.

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

## Passo 2 — Crie as senhas

Gere uma chave aleatória para cada item abaixo. Com Python instalado, rode duas vezes:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- **MEU_PIERRE_TOKEN:** chave secreta do painel (mínimo 32 caracteres). Assina o seu login.
- **CRON_SECRET:** senha da atualização diária (valor diferente do token).
- **SENHA_PAINEL** (opcional): a senha que você digita para entrar, com pelo menos 10 caracteres.
  Se não criar, a senha para entrar é o próprio `MEU_PIERRE_TOKEN`.

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
   (e, se quiser, `SENHA_PAINEL` e `SALDO_MINIMO`). O formato está no `.env.exemplo`.
   **Não crie `DATABASE_URL` à mão**: ela é criada sozinha no item 5.
4. Clique em **Deploy**. O site já sobe, mas o painel só funciona depois do banco, no próximo item.
5. No projeto, abra a aba **Storage** → crie um banco **Neon** (Postgres, plano gratuito) e conecte ao projeto.
   Isso cria a variável `DATABASE_URL` sozinha.
6. Em **Deployments**, clique em **Redeploy**.
7. Abra `https://SEU-PROJETO.vercel.app`. Deve aparecer a tela de login do **Meu Pierre**.

A partir daqui, todo `git push` para o GitHub publica uma versão nova automaticamente.
A Vercel também roda a sincronização todo dia por volta das 8h (horário de Brasília), pelo `vercel.json`.

## Passo 5 — Entre no painel e carregue os dados

1. Abra `https://SEU-PROJETO.vercel.app` e entre com a sua senha (`SENHA_PAINEL`, ou o `MEU_PIERRE_TOKEN`).
   O login fica salvo por 30 dias nesse navegador.
2. Na primeira vez o painel tenta buscar os últimos 90 dias sozinho. Se aparecer vazio, clique em **Atualizar**.

Para trazer 12 meses de histórico de uma vez, rode no seu computador (opcional):

```bash
pip install -r requirements.txt
```

Crie um `.env` (copie do `.env.exemplo`) com as credenciais da Pluggy, o `PLUGGY_ITEMS` e o `DATABASE_URL`
do Neon (copie em Vercel > Storage > seu banco > `.env.local`). Depois:

```bash
python desktop.py --sync 365
```

Dica: no celular, use "Adicionar à tela inicial" no navegador para abrir o painel como um app.

---

## Rodar no computador, sem nuvem (opcional)

Também funciona 100% local, com SQLite. Deixe `DATABASE_URL` vazio no `.env`, preencha o `MEU_PIERRE_TOKEN` e rode:

```bash
python desktop.py
```

O painel abre em http://localhost:8000.
Para testar sem banco nenhum, com dados fictícios: `MEU_PIERRE_DEMO=1 python desktop.py`.

---

## Segurança

- Sem login, o painel e a API respondem só com a tela de login (ou erro 401). Uma senha errada espera 1 segundo antes de responder.
- O login é um cookie assinado, `HttpOnly` e `Secure`, válido por 30 dias. O botão **Sair** apaga o cookie.
- Se a senha vazar: troque `SENHA_PAINEL` (ou o `MEU_PIERRE_TOKEN`) em Vercel > Environment Variables e faça Redeploy.
  Trocar o `MEU_PIERRE_TOKEN` também desconecta todos os navegadores logados.
- O repositório deve ser **privado**. Credenciais ficam só nas variáveis da Vercel.

## Bom saber

- **A autorização do Meu Pluggy dura 1 ano.** O painel avisa quando faltar 30 dias.
  Para renovar, repita o Passo 1.4. Confira se o itemId mudou e atualize `PLUGGY_ITEMS` na Vercel.
- **Categorias:** vêm da Pluggy e são traduzidas. Se algo vier errado, clique na categoria da transação e crie uma regra
  (ex.: "PIX JOAO SILVA" → Aluguel). Vale para todo o histórico.
  Para transferências entre suas contas que não foram detectadas, use **"Transferência entre contas próprias"**.
- **Investimentos e faturas** dependem do que cada banco compartilha no Open Finance; alguns podem vir vazios.

## Arquivos

| Arquivo | Função |
|---|---|
| `pierre.py` | Sincronização e cálculos do painel |
| `store.py` | Banco de dados (SQLite local ou Postgres na nuvem) e categorias |
| `pluggy_client.py` | Cliente da API Pluggy, somente leitura |
| `app.py` | App web da Vercel: login, API do painel, cron e download do Excel |
| `painel.html`, `login.html` | As telas do painel |
| `desktop.py` | Uso local e carga inicial do histórico |
| `vercel.json` | Agenda do cron diário |
