# Auditoria de código e integração — 16/10/2026

## Escopo e conclusão

Revisão do código Python, persistência SQLite, HTTP/URLs, concorrência,
exportação/handoff, bootstrap, testes e CI. Base operacional auditada:
`07f5bcd4e4711c2f7523d9fa8fa4214fbb7bceb1` (0.2.11). Base de integração:
`01f84ae583b2da4a65c7d88ee26c03b62d82498f` (`main`, 0.2.1).

A separação de responsabilidades, transporte injetável, SQLite no coordenador,
importação transacional e testes offline são pontos fortes. Há três defeitos
reproduzidos de tratamento de entradas/compatibilidade, descritos abaixo. Eles
permanecem abertos: esta entrega audita o produto e consolida implementações já
existentes; não altera as políticas nem aplica essas três correções.

Não houve acesso ao VPS, coleta Apify ou requisição aos sites candidatos.
Reproduções usaram apenas HTML e bancos temporários sintéticos.

## Achados reproduzidos

### A1 — P2: links internos malformados escapam do tratamento esperado

Local principal: `src/keyword_searcher/resolve.py:124`; o mesmo padrão existe em
`resolve.py:101`, `resolve.py:349`, `identity.py:99`, `site_policy.py:128` e
`http.py:96`.

`normalize_url` converte erros de parsing em `DiscoveryError`, mas o argumento
`urljoin(...)` é avaliado antes de entrar nessa função. Um `href="http://["`
produz `ValueError: Invalid IPv6 URL`; capturar somente `DiscoveryError` não basta.
Na resolução paralela, a exceção inesperada interrompe o lote com `WorkerFailure`;
na sequencial, pode escapar diretamente da CLI. As decisões já persistidas são
preservadas, mas esse HTML impede a continuidade normal.

Reprodução: um HTTP falso retorna status 200, `text/html` e este documento para
`https://acme.example/`:

```html
<title>Acme</title><meta property="og:site_name" content="Acme">
<a href="http://[">Home</a><a href="/products">Products</a>
```

`WebsiteResolver(http_falso).resolve(SearchResult("Acme", url))` lança `ValueError`.

Recomendação: centralizar resolução de referências e normalização numa função
que traduza erros de parsing esperados. Ignorar referências HTML inválidas quando
possível; redirects inválidos devem gerar uma pendência explícita. Não converter
exceções arbitrárias em decisões. Validar os casos Home, JSON-LD, About, oferta e
redirect sem rede e com equivalência entre os modos de execução.

### A2 — P2: o scheduler interpreta URLs antes da validação do resolvedor

Locais: `src/keyword_searcher/workers.py:17` e `workers.py:33`.
O progresso também interpreta URLs cruas em `progress.py:69` e `progress.py:137`.

O parser Apify aceita URLs que sejam strings. Ao distribuir os resultados entre
hosts, `interleave_hosts` usa `urlsplit` sem tratar `ValueError`. Uma URL
malformada pode abortar o coordenador antes de produzir a decisão conservadora
`pending/invalid_url` e antes do diagnóstico `WorkerFailure`.

Reprodução sem progresso, com Store temporário e o resolvedor real:

```python
with ResolutionWorkers(resolver, workers=workers) as pool:
    pool.process("q", [SearchResult("bad URL", "http://[")], store)
```

| Workers | Resultado observado |
|---|---|
| 1 | Decisão persistida: `pending`, motivo `invalid_url` |
| 8 | `ValueError: Invalid IPv6 URL`; nenhuma decisão para esse resultado |

O teste sem progresso isola o scheduler. Na CLI, o parsing da mesma URL nos
métodos de progresso pode falhar também no caminho sequencial, inclusive com
`--no-progress`, pois o objeto de progresso ainda é chamado.

Recomendação: usar uma chave segura de host para scheduling/exibição e deixar o
resolvedor registrar a URL inválida como pendência. Preservar a URL original como
chave do estado, sem descartar silenciosamente o resultado. Testar retomada e
progresso habilitado/desabilitado, além da equivalência entre 1 e N workers.

### A3 — P2: rejeição de esquema futuro ocorre depois de modificar o banco

Local: `src/keyword_searcher/store.py:31`, antes da validação em `store.py:51`.

O construtor executa `CREATE TABLE IF NOT EXISTS` antes de conferir o esquema.
Os DDLs fora da transação de migração permanecem gravados quando a versão é
rejeitada. Isso viola a expectativa de abrir/rejeitar um banco incompatível sem
modificá-lo. O teste atual usa um banco que já contém todas essas tabelas e não
detecta essa situação.

Reprodução: criar um SQLite contendo somente a tabela `meta` com
`schema_version=999`, fechar e chamar `Store(path, {})`. A chamada é rejeitada,
porém a lista de tabelas muda de `[meta]` para
`[meta, pages, resolutions, progress, apify_batches, sqlite_sequence]`.
Não foi demonstrada perda de linhas; a alteração de esquema foi confirmada.

Recomendação: inspecionar metadados de bancos existentes antes de qualquer DDL e
executar criação/migração autorizada em transação. Acrescentar teste de rejeição
com esquema futuro diferente, verificando ausência de alterações persistidas.

## Mass merge: causa, evidência e consolidação

Os 13 PRs existentes estavam fechados e mesclados. O bloco recente #3–#13 foi
mesclado em 16/10/2026, mas cada PR #4–#13 apontava para a branch anterior. O
conteúdo não se propagou automaticamente quando os PRs posteriores foram
mesclados nessas branches já integradas. Os commits remotos confirmam a situação;
não se inferiu integração apenas pelo estado visual dos PRs.

| PR | Base do PR | Commit do squash |
|---|---|---|
| #3 | `main` | `01f84ae` |
| #4 | `fix/apify-portuguese-errors` | `a857651` |
| #5 | `fix/editorial-audit` | `1e9b65c` |
| #6 | `fix/resolver-source-identity` | `7586aa9` |
| #7 | `fix/identity-copyright-context` | `419980a` |
| #8 | `feat/startup-adherence-handoff` | `f8e28a4` |
| #9 | `fix/apify-dynamic-page-limit` | `64ecbca` |
| #10 | `feat/site-resolution-workers` | `06c1acb` |
| #11 | `fix/apify-error-records` | `2ed71f8` |
| #12 | `fix/continuous-site-workers` | `a5ae1b4` |
| #13 | `fix/overlap-search-site-workers` | `8c1e75d` |

O problema era a base e a ordem da integração. O método squash já era usado no
bloco recente. A branch `fix/site-worker-errors` conserva a implementação
cumulativa 0.2.11; os demais heads originais #3–#12 pertencem à sua ancestralidade.

A consolidação foi preparada numa branch nova baseada em `origin/main`, mesclando
o commit 0.2.11. Houve conflitos em versão, histórico de validação e adaptação
Apify, por causa do squash independente de #3. Após resolução, o índice completo
foi comparado com `07f5bcd`: nenhum diff, antes dos documentos desta auditoria e
do ajuste de isolamento do teste. Assim, nenhuma implementação anterior foi
descartada e nenhuma mudança de produto nova foi escondida na resolução.

O PR de consolidação tem destino `main` e deve ser concluído com squash após os
checks do commit atual. Não é necessário reabrir nem reexecutar #4–#13. O fluxo
de contribuição passa a documentar branches originadas de `main`, validação do
conteúdo final e squash merge dos trabalhos autorizados.

## Verificações e limitações

Validação local em Linux/Python 3.12.14:

- A primeira execução teve 321 testes aprovados e uma falha de isolamento: o teste
  de instalação sem checkout detectava o marcador `.git` de `/tmp` do ambiente.
  O teste foi ajustado para simular explicitamente a ausência de checkout. Os
  testes reais de rejeição de checkout e de pacote continuam ativos; o código de
  produção de validação de diretórios não foi modificado.
- Após esse ajuste, 322 testes aprovados. Bootstrap Bash executado duas vezes a
  partir de outra pasta, incluindo instalação editável 0.2.11, dependências, Ruff,
  formatação e ajuda da CLI. O CI do push também aprovou Linux e Windows.
- Na primeira execução do PR, o job Windows permaneceu no bootstrap por cerca de
  dez minutos, sem log completo disponível pela API; o equivalente do push no
  mesmo commit terminou com sucesso. A causa não foi determinada. O CI passa a
  ter limite de dez minutos por job, nomes de testes no log e dump de threads
  quando um teste demora mais de sessenta segundos. O dump é diagnóstico, não um
  cancelamento do teste. Os checks do commit final são registrados no PR.
- Consulta ao PyPI para as versões instaladas de `beautifulsoup4` 4.15.0,
  `soupsieve` 3.0.3 e `typing_extensions` 4.16.0 retornou listas vazias de
  vulnerabilidades. Isso é uma consulta às versões usadas, não uma garantia sobre
  todas as versões permitidas pelos intervalos de dependência.
- O endpoint de rulesets retornou lista vazia. A leitura de proteção clássica de
  `main` retornou 403 por limitação da integração; não se concluiu que essa
  proteção esteja ausente. Nenhuma configuração administrativa foi alterada.

Como melhorias de manutenção, o CI pode incluir Python 3.14 usado no VPS,
validação de wheel instalado fora do checkout e atualização automatizada de
dependências/Actions. Hoje cobre Linux 3.11 e Windows 3.13, instalação editável e
bootstrap repetido em caminhos com espaços; isso já é uma base útil. Fixar Actions
por SHA e manter uma resolução reproduzível das dependências de desenvolvimento
são melhorias opcionais, não defeitos funcionais comprovados nesta revisão.

Tipagem é parcial e alguns contratos usam `object`/dicionários. Evoluir os contratos
do scheduler e provider para tipos explícitos ajuda manutenção; não justifica uma
reescrita ou uma adoção indiscriminada de regras de lint. Exportação por arquivo,
um processo por estado/saída, limites HTTP por operação e a limitação contra DNS
rebinding já estão documentados e não foram apresentados como achados novos.

A auditoria não mede precisão comercial, latência no VPS, utilização sustentada
de 100 workers nem conclusão do corpus APS. Esses pontos exigem evidência
operacional que não foi fornecida nesta etapa.
