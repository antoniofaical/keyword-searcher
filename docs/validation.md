# Validação — versão 0.2.1

A versão 0.2.0, de 07/10/2026, concluiu a migração Apify e introduziu saída externa.
Os checks dessa entrega permanecem registrados abaixo da correção 0.2.1.

## Correção de idioma e diagnóstico — 08/10/2026

- O schema público do Actor `apify/google-search-scraper`, build `0.0.462`
  (`j1KHroHbaMQXFmDBG`), aceita `pt-BR`/`pt-PT` e rejeita `pt` em
  `languageCode`. A lista observada está em `tests/fixtures/apify_language_contract.json`.
- No VPS, uma chamada autenticada a `/validate-input` com `languageCode=pt`
  retornou HTTP 400, `invalid-input`. O endpoint de autenticação retornou 200,
  e o usuário não encontrou run correspondente no console. Isso confirma a
  incompatibilidade de entrada; não constitui uma busca bem-sucedida.
- A conversão ocorre somente no transporte: `pt` vira `pt-BR`, ou `pt-PT` quando
  o país é `pt`. Configuração, IDs e dados do banco mantêm seus valores anteriores.
- Erros HTTP agora preservam código, tipo e mensagem da API, com tamanho limitado,
  remoção de controles do terminal e ocultação de tokens. A mensagem de criação
  incerta inclui essa causa e continua impedindo retry automático de POST.
- 107 testes passaram localmente, incluindo a recuperação sintética de um lote
  rejeitado de 19 queries, sem alterar o idioma armazenado, seguida de retomada
  sem outro POST. As reservas cumulativas permanecem: 95 + 95 = 190 páginas.
- Ruff lint/formatação aprovados. Não houve execução autenticada/paga de Actor
  neste ambiente; a validação HTTP 400 foi fornecida pelo usuário.

Fontes do contrato: [build público](https://api.apify.com/v2/actor-builds/j1KHroHbaMQXFmDBG),
[entrada do Actor](https://apify.com/apify/google-search-scraper/input-schema) e
[validação de entrada](https://docs.apify.com/api/v2/actor-validate-input-post).

## Executado localmente

Ambiente: Windows/Python 3.14, pytest 8.4.2, Ruff 0.16.10 na venv criada.

- 94 testes passaram.
- Ruff lint e formatação aprovados.
- Instalação editável numa venv e pip check aprovados.
- Bootstrap PowerShell executou instalação, checks e ajuda da CLI.
- Bootstrap Bash com sintaxe verificada e fluxo completo aprovado no Git Bash
  do Windows. Bash no Linux é exercitado pelo CI.
- Testes de falha do bootstrap preservam venv inválida e propagam erro do Python.
- A matriz CI executa instalação limpa e repetida em caminhos com espaços,
  invocando os scripts de outra pasta: Linux/Python 3.11 e Windows/Python 3.13.

Os testes mantêm os casos anteriores de resolução, normalização, UTF-8, escaping,
progresso, interrupção e retomada. Os testes da integração remota removida foram
substituídos por contratos Apify.

## Novos cenários cobertos

- Apify pela CLI, retomada e troca de pasta de exportação sem outras buscas.
- Rechecagem de páginas Apify, SerpApi e banco misto sem token.
- Migração com preservação de páginas, decisões, IDs, reservas e contador antigo;
  configuração incompatível não efetua a migração; esquema futuro é rejeitado.
- Página única com dez resultados sem segundo Actor; zero resultados, página final
  curta, limite de profundidade, custo atingido e evidência incompleta.
- Dataset com lacunas, duplicatas ou dados inválidos conserva run recuperável e
  não importa páginas parcialmente.
- Autenticação sem retry; rede/429/5xx em GET; criação POST sem retry; dataset
  paginado; estados transitórios; interrupção do polling conserva o run ID.
- Destino externo inexistente e com espaços; pasta do pacote ou checkout rejeitada;
  falha de substituição preserva o CSV anterior e remove o temporário.

## Limites

Nenhuma consulta autenticada/paga Apify foi executada. Não há piloto de mercado,
medição de precisão/cobertura ou comparação de custo entre provedores.

Os metadados de fila/custo usados para provar término dependem do Actor. Quando
não permitem confirmar o encerramento, a ferramenta exporta os resultados úteis
com cobertura incompleta, sem criar outra execução paga. Esse comportamento
conservador precisa ser observado no piloto.

O estado remoto do CI deve ser conferido na execução vinculada ao commit/PR.
Os resultados locais não são prova de aprovação remota.

## Próximo checkpoint operacional

Configurar APIFY_API_TOKEN no ambiente, selecionar uma amostra de queries e
definir orçamento. Usar diretórios externos de estado e saída, limites pequenos
de páginas e teto por Actor. Conferir companies.csv, pendências, cobertura e
evidência do run, contando falsos positivos e empresas omitidas.

O teto por Actor não representa aprovação do orçamento total. Manter a amostra
e a avaliação humana antes de qualquer mudança na política de identificação.
