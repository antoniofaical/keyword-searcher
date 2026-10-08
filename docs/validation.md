# Validação — versão 0.2.2

A versão 0.2.0, de 07/10/2026, concluiu a migração Apify e introduziu saída externa.
Os checks dessa entrega e da correção 0.2.1 permanecem registrados abaixo.

## Auditoria offline e estados editoriais — 08/10/2026

- 117 testes passaram localmente. Os novos cenários verificam ausência de rede,
  leitura sem token/queries, amostra reproduzível com agregação de queries e
  preferência por hosts distintos, campos humanos vazios e conteúdo do ZIP.
- A correção tem backup consistente, preserva campos das decisões, metadados e
  progresso e é idempotente. Banco ausente não é criado, esquema futuro não é
  migrado e pasta com revisão humana não é sobrescrita.
- Fontes editoriais e redirecionamentos para fontes excluídas recebem `skipped`.
  HTTP 403/202/203 continuam `pending`; nenhuma política de identidade foi ampliada.
- Ruff lint/formatação aprovados. Os testes usam dados sintéticos; não comprovam
  a qualidade da identificação de empresas no piloto real.

## Piloto observado no VPS — 08/10/2026

Evidência: saída do terminal fornecida pelo usuário, sem acesso ao banco ou ao CSV.
Run Apify `GHzGpvpKXl8tXvV9n`, dataset `Su0OproYlVS2Sij0w`, 19 queries,
95 páginas importadas, custo registrado US$ 0,42755 e duração total local de
1.175 segundos. Todas as queries atingiram `limited/max_pages` (5 páginas).
Isso comprova execução com resultados, não exaustão do Google.

O relatório indicou 70 resoluções confirmadas, 67 linhas exportadas, 463 pendências
e 320 resultados ignorados. O usuário contou 143 pendências de identidade ausente,
133 HTTP 403 e 99 editoriais. Se o banco mantiver essas contagens, a correção dos
99 casos editoriais produzirá 364 pendências e 419 ignorados, preservando as
67 linhas do CSV. Essa previsão ainda precisa ser conferida no VPS.

A revisão dos nomes/URLs confirmados e das amostras de pendências aguarda os
arquivos do piloto. Não há medição de precisão, falsos positivos ou empresas omitidas.

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

Nenhuma consulta autenticada/paga Apify foi executada neste ambiente de
desenvolvimento. O piloto no VPS foi observado pelos logs fornecidos pelo usuário;
não há medição de precisão/cobertura nem comparação de custo entre provedores.

Os metadados de fila/custo usados para provar término dependem do Actor. Quando
não permitem confirmar o encerramento, a ferramenta exporta os resultados úteis
com cobertura incompleta, sem criar outra execução paga. Esse comportamento
conservador depende da evidência de cada execução.

O estado remoto do CI deve ser conferido na execução vinculada ao commit/PR.
Os resultados locais não são prova de aprovação remota.

## Próximo checkpoint operacional

Gerar o pacote offline do piloto, conferir as contagens após a correção e revisar
companies.csv e amostras de pendências. Identificar falsos positivos e possíveis
omissões antes de alterar os critérios de identidade ou tratamento de HTTP 203.

O teto por Actor não representa aprovação do orçamento total. Manter a amostra
e a avaliação humana antes de qualquer mudança na política de identificação.
