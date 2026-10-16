# Validação — versão 0.2.11

## URLs Unicode e diagnóstico de workers — 16/10/2026

- O usuário forneceu o traceback do VPS: ao resolver a página
  `https://www.salvus.me/melhor-sistema-para-gest%C3%A3o-assistencial-do-home-care`,
  uma inspeção interna de About chegou a `http.client._encode_request` com `ç`
  sem codificação. Resultado: `UnicodeEncodeError` ao produzir o request ASCII.
  Não foi uma falha reportada pela Apify. O link interno exato e o HTML não foram
  fornecidos; a reprodução usa um caminho sintético com o mesmo tipo de caractere.
- A reprodução offline usa `HTTPConnection.putrequest` sem abrir socket. A
  normalização antiga preservava Unicode cru; a nova codifica path em UTF-8 sem
  duplicar `%C3%A7`, `%2F` ou outros escapes existentes. A navegação sintética
  passa da URL da fonte para a raiz e um About com acentos/espaço.
- O erro genérico escondia a causa. A correção distingue resolução, persistência
  e progresso e registra contexto limitado em `worker_error.json`. Regressões
  verificam preservação de decisões e outputs parciais, origem do erro, redação
  de segredos, ausência de mensagens/HTML/locals e recheck sem alterar o original.
- Esquema SQLite, regras de classificação, concorrência e recuperação Apify
  permanecem iguais. O run `OJ7ojac4xpncS1Dim` é contexto fornecido pelo usuário;
  nenhuma chamada a esse run ou ao site foi realizada no desenvolvimento.
- 322 testes passaram localmente em Windows/Python 3.12.14, incluindo 14 novos
  casos para paths Unicode/espaços, escapes preservados, navegação interna real
  pelo cliente HTTP sem socket, diagnósticos por etapa, outputs parciais e
  proteção do estado original no recheck. Ruff aprovado. Conferir os checks
  remotos associados ao commit.

## Sobreposição da busca e verificação — 15/10/2026

- Decisão: remover a drenagem entre lotes Apify e a janela de 4N links da 0.2.9.
  Ambas podiam deixar workers ociosos com trabalho em outras consultas. A busca
  paralela agora alterna páginas entre consultas disponíveis e antecipa um único
  lote remoto. Todo SQLite continua no coordenador; o backlog usa arquivo
  temporário privado. Cada slot liberado é reposto durante a coleta de conclusões.
- Quinze novos casos cobrem sites antigos sobrepostos ao próximo lote, consulta
  salva posterior processada durante espera remota, gravação antes do término do
  Actor, backlog de host ocupado tanto na busca quanto no recheck e 100 workers
  simultâneos alcançando outra consulta após 500 links de um host.
- Também cobrem um lote remoto por vez, limites cumulativos/por Actor,
  equivalência com o caminho sequencial, interrupção durante POST e polling,
  preservação do ID e de decisões persistidas, retomada sem novo POST, resposta
  de criação perdida sem retry, validação atômica e consultas salvas após acabar
  o orçamento. Cancelar o polling local não aborta o Actor remoto.
- O progresso mostra hosts livres na fila e uma linha Apify independente dos
  sites. A versão do pacote passa a 0.2.10; esquema SQLite, políticas de decisão,
  cache, handoff e arquivos de revisão humana não mudam.
- Testes usam exclusivamente transporte e sites sintéticos. Nenhuma busca paga
  ou visita real é necessária para validar a sobreposição. O tempo no VPS e a
  utilização em corpus real ainda precisam ser observados.
- 308 testes passaram localmente em Windows/Python 3.12.14. Ruff lint/formatação,
  instalação editável 0.2.10, dependências e ajuda da CLI aprovados. Conferir os
  checks remotos associados ao commit.

## Alimentação contínua dos workers — 15/10/2026

- O teste no VPS mostrou até poucos workers ativos com `--workers 100`. A 0.2.8
  tinha uma barreira ao final de cada página, limitando concorrência ao número de
  links da página. A 0.2.9 mantém uma fila limitada entre páginas e consultas.
- 293 testes locais passaram em Windows/Python 3.12.14. Dez novos casos incluem
  sincronização de 20 e 100 resoluções reais em threads, distribuídas entre
  múltiplas páginas/queries, tanto na busca quanto no recheck. Cada teste usa hosts
  sintéticos distintos e exige que todos os workers entrem antes de liberar o grupo.
- Também são verificados conclusão fora de ordem, SQLite no coordenador, query
  mantida `running` até seus sites terminarem, espera por host fora do executor,
  interrupção/retomada com várias queries em andamento, atribuição de erro à query
  original, persistência antes de novo Actor e progresso global sem resets.
- Simulação offline comparou o commit 0.2.8 `3fc6647` com a fila contínua: 100
  workers configurados, dez queries, dez páginas/query, um site independente/página
  e latência de 100 ms. A versão anterior mediu 10,860 s e pico de 1 ativo; a nova
  mediu 0,640 s e pico de 100, com as mesmas 100 decisões. O ganho de 16,97× é
  específico desse cenário sintético; não é previsão para o VPS.
- Ruff lint/formatação aprovados. Nenhuma busca Apify ou visita real foi feita.
  As regressões de cache, cobertura parcial e handoff continuam passando.
- O estado remoto dos checks deve ser conferido no PR associado ao commit.

## Registros de erro no dataset Apify — 15/10/2026

- O usuário forneceu uma contagem de 337 registros no dataset do run
  `8ThDrgX2YAu1H37Vo`, dos quais um era `#error: true` sem `searchQuery`.
  `#debug.url` identificava a página 19 de “startup brasileira diabetes saúde digital”.
  As mensagens indicavam páginas vazias nas tentativas e um erro 500 do proxy.
  O banco completo e as 336 páginas não foram recebidos neste ambiente.
- 283 testes locais passaram em Windows/Python 3.12.14. Os 24 novos casos incluem
  uma reconstrução sintética com 336 páginas e um erro no formato recebido:
  336 páginas importadas, 19 queries completas e uma incompleta, marcador excluído
  da contagem de páginas e retomada do run existente sem POST.
- Também são verificados erro inicial/intermediário/final, query só com erro,
  lote inteiro com erro, associação inválida/contraditória, domínio falso,
  offsets e parâmetros ambíguos, lacuna inexplicada, sucesso e erro na mesma página,
  preservação da incerteza das contagens da fila e recheck em cópia.
- A amostra sintética contém um token falso nos debug traces para verificar sua
  exclusão do relatório. Falhas mapeadas não viram resultados vazios completos;
  formatos desconhecidos continuam bloqueando a transação inteira.
- Ruff lint/formatação aprovados. Nenhuma chamada Apify ou visita a sites foi feita.
- O estado remoto dos checks deve ser conferido no PR associado ao commit.

## Workers de resolução — 14/10/2026

- 259 testes passaram localmente em Windows/Python 3.12.14. Os 19 novos casos
  verificam sobreposição real, persistência de resultados rápidos antes dos lentos,
  SQLite no coordenador, fila limitada, interrupção e retomada sem repetir buscas,
  equivalência de decisões/CSV/JSON/handoff entre 1 e 8 workers, retry de pendências,
  recheck em cópia, cache simultâneo de páginas/erros e configuração da CLI.
- O transporte HTTP foi verificado com dois hosts em paralelo, alias `www`
  serializado, redirecionamentos cruzados sem deadlock, cancelamento de espera
  por host e openers separados por thread. Não houve requisições reais.
- Uma simulação offline com 80 hosts, dez queries e latência de 50 ms por GET
  mediu 4,765 s com 1 worker e 1,141 s com 8 workers (4,18×). Ambas fizeram 80 GETs
  sintéticos e confirmaram as mesmas 80 decisões. Isso demonstra sobreposição,
  não estima o tempo de uma coleta real no VPS.
- Ruff lint/formatação, instalação editável 0.2.7, dependências e ajuda da CLI/recheck
  aprovados. Os limites de Apify, classificação e esquema
  SQLite permanecem preservados; a versão do pacote é 0.2.7.
- O estado remoto dos checks deve ser conferido no PR associado ao commit.

## Limite automático de páginas — 14/10/2026

- O padrão fixo de 900 páginas foi substituído por queries únicas × profundidade.
  A mudança permite cobrir todas as queries sem calcular um limite manual; um
  limite explícito menor continua restringindo a coleta.
- 240 testes passaram localmente em Windows/Python 3.12.14. Os sete novos casos
  cobrem 26 queries × 10 páginas, uma coleta acima do antigo padrão de 900,
  profundidade padrão, BOM/linhas vazias/repetidas, retomada de 100 para 260 páginas,
  reserva insuficiente para uma query e rejeição de limites zero/negativo.
- As buscas de teste usam transporte sintético. A retomada conserva reservas
  anteriores e não recria Actors para queries já salvas. Teto monetário por Actor
  e tamanho máximo dos lotes são preservados.
- Ruff lint/formatação, instalação editável 0.2.6, verificação das dependências
  e ajuda da CLI passaram. Nenhuma busca Apify ou requisição a sites foi realizada.
- O estado remoto dos checks deve ser conferido no PR associado ao commit.

## Correções a partir da reavaliação real — 08/10/2026

- O pacote da reavaliação confirmou 853 decisões: 60 confirmadas, 426 pendentes,
  367 descartadas e 46 linhas exportadas. Das 391 alterações, 336 mudaram estado
  e 55 alteraram outros campos de decisão. Só sete confirmações antigas permaneceram
  confirmadas; 53 decisões antes pendentes/descartadas passaram a confirmadas.
- Os 115 conflitos incluíam diferenças de sufixos jurídicos, slogans e textos
  que não identificavam o proprietário. Seis fornecedores antes confirmados
  ficaram pendentes por esses padrões: 3Brain, Cambridge NeuroTech, CelVivo,
  MaxWell, Fluigent e Sartorius. São 16 resoluções antigas, não 16 empresas.
- Portais de pesquisa e publicação passaram indevidamente por sinais genéricos
  de produtos ou por “our service” em aviso de cookies. A política 3 prioriza
  esse tipo de fonte e restringe o contexto das evidências de oferta/copyright.
- 210 testes locais passaram, incluindo os novos padrões e negativas contra
  fusão por substring, truncamento de `Organization` e conflito real de proprietário.
  Os 13 HTMLs capturados anteriormente continuam passando no replay offline.
- A reconstrução sintética dos nomes recebidos de oito hosts tornou compatíveis
  os seis fornecedores acima e preservou conflitos de Organoid Grid e OrganoidTest.
  Isso não verifica HTTP, oferta ou o DOM original dessas oito páginas.

O efeito completo da 0.2.4 exige nova reavaliação do banco copiado no VPS.
Nenhum total agregado dessa versão foi produzido a partir do SQLite do usuário.
O estado remoto dos checks deve ser conferido no PR associado ao commit.

## Regras de fonte e identidade — 08/10/2026

- 174 testes passaram localmente em Windows/Python 3.14. Ruff lint e
  formatação aprovados; instalação editável 0.2.3 e `pip check` aprovados.
- Regressões sintéticas cobrem artigos próprios, fontes reconhecidas antes do
  fetch, institutos com serviços, conflitos de marca/agência, aliases explícitos,
  identidade visível, About do mesmo host, HTTP 403, interstitials, parâmetros
  funcionais, limite de cinco inspeções e limites de tamanho do cache.
- A reavaliação em cópia cobre os três estados anteriores, preservação byte a
  byte do original, backup consistente, tabelas de busca/IDs/progresso intactas,
  ausência de transporte Apify, recusa de saída existente e interrupção recuperável.
- Também foram reavaliados offline 13 HTMLs públicos capturados na investigação
  do piloto, sem novas requisições. Os 12 casos de seis fornecedores resolveram
  para suas homes; Organoid Grid ficou pendente por conflito com identidade de
  agência. São exemplos selecionados para regressão, não estimativa de precisão.
- A auditoria recebida confirmou 67 linhas/43 hosts, 71 casos, 364 pendências e
  419 descartes. O ZIP não contém SQLite ou HTML histórico. As regras novas
  ainda precisam ser aplicadas ao banco completo do VPS e revisadas pelo usuário.

As escolhas e seus limites estão em [política de resolução](resolver-policy.md).
O status remoto dos checks deve ser conferido no PR associado ao commit.
Os registros abaixo descrevem entregas anteriores; não são os resultados da 0.2.3.

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

## Handoff — versão 0.2.5 (09/10/2026)

233 testes passaram localmente em Windows/Python 3.14; Ruff lint/format e
git diff --check passaram. Instalação editável com build isolado, versão instalada
0.2.5 e pip check aprovados. A venv não contém setuptools como dependência de
runtime; a instalação precisa do backend de build isolado definido no pyproject.

O novo contrato usa somente funções de entrada do receptor em snapshot do
commit dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b. Os testes comparam a seleção do
builder à seleção desse código, sem instalar/invocar crawler ou APIs do receptor.
Cobrem nome/URL equivalentes, aliases, alternativas, subdomínios e caminhos,
Unicode, colisões de nomes de pastas, zero confirmados, cobertura incompleta,
Ctrl+C e retomada, recheck/audit, falha de escrita e preservação de banco/revisão.

O CSV recebido da v3 foi usado num replay de compatibilidade separado: 50 linhas
produzem as mesmas 22 entradas selecionadas pelo receptor. Esse replay usa
somente o CSV: não reconstrói as 64 resoluções confirmadas ou todas as URLs
descobertas originais, nem demonstra uma exportação do SQLite completo do VPS.
O comando offline deve usar o banco real para produzir o manifesto completo.

Nenhuma chamada Apify, Jev, DeepL, crawl ou mudança de decisão foi executada
para a integração. As regras do piloto continuam na política 3; não há novo
filtro de aderência ou alteração no CSV/SQLite. A matriz CI continua verificando
bootstrap limpo e repetido em Linux/Python 3.11 e Windows/Python 3.13; conferir
seu resultado no commit/PR desta versão.
