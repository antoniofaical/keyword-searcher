# Arquitetura e contratos — 0.2.0

## Responsabilidade

Entrada: texto UTF-8, uma query por linha. Saída: `CompanyName;URL;SearchQuery`.
Apify fornece resultados orgânicos do Google. Ads, notícias, diretórios e
comparativos não alimentam descoberta indireta. O resolvedor institucional e a
normalização de URLs mantêm os critérios da versão anterior.

## Componentes

| Arquivo | Responsabilidade |
|---|---|
| bootstrap.ps1 / bootstrap.sh | Ambiente virtual, instalação e checks sem buscas |
| models.py | Contratos de busca, resolução e cobertura terminal |
| cli.py | Configuração, diretórios explícitos, composição e códigos de saída |
| apify.py | Actor, reserva de páginas, recuperação, dataset e evidência de término |
| search.py | Leitura de payloads SerpApi antigos; nenhuma integração remota |
| http.py / urls.py | HTTP limitado, normalização e verificação de destinos |
| resolve.py | Identidade declarada e seleção limitada do entrypoint |
| store.py | SQLite, esquema versionado, migração e dados para exportação |
| export.py | Validação de destino externo e escrita atômica por arquivo |
| pipeline.py | Iteração, retomada e rechecagem das páginas salvas |

O transporte é injetável. Testes de contrato usam dados sintéticos e não precisam
de credenciais nem de buscas reais.

## Persistência e migração

`--run-dir/state.sqlite` é a referência da execução. Queries, idioma, país,
localização e profundidade identificam seu contexto. A versão do software pode
mudar; `meta.schema_version` identifica a compatibilidade do banco.

A migração 1→2 ocorre numa transação após conferir a compatibilidade da configuração.
A configuração antiga fica em `meta.legacy_config`; `meta.migration` registra a
razão e as versões. IDs de lotes/runs, respostas, resoluções, progresso e contadores
são preservados. O provider da configuração passa a `apify-google`, enquanto a
origem de cada página continua explícita no payload. Esquemas futuros e mudanças
de contexto são rejeitados.

`search_requests` continua sendo histórico SerpApi. O método de reserva legado
permanece disponível para leitura/manutenção de bancos, sem uso na CLI nova.
Novas buscas reservam queries × profundidade em `apify_batches`, antes da criação
remota. `apify_reserved_pages` é conservador e cumulativo; não é custo monetário.

A criação de Actor é enviada uma vez. GETs de status/dataset podem receber dois
retries para rede, 429 e 5xx. Após confirmação, o run ID é persistido. Se a criação
ficar incerta, só um ID recuperado explicitamente permite continuar. Não existe
garantia de exactly-once na rede.

## Paginação e evidência de término

O dataset deve conter uma sequência contígua de páginas por query, sem duplicatas,
e deve representar todas as queries do lote. Toda a validação precede uma transação
que importa as páginas e grava cobertura, evidência do run e conclusão do lote.

Uma página com dez links não cria uma continuação. A próxima página precisa
existir no dataset. O offset local Apify é uma chave derivada do número de página,
não uma promessa sobre a quantidade de resultados.

Para classificar uma query como completa, a política exige run `SUCCEEDED`,
exit code zero, custo finito abaixo do teto, fila sem pendências e contagem de
requisições tratadas igual à quantidade de registros importados. A API é relida
dez segundos após o primeiro sucesso, pois custos e stats podem ser preliminares.
É uma política conservadora: alterações na implementação do Actor podem produzir
`apify_completion_unverified`, mesmo com resultados úteis. Essa situação não
inicia outro Actor e não é convertida em sucesso.

Um custo que atingiu o teto gera `apify_cost_limit`. A profundidade atingida gera
`limited/max_pages`; isso não comprova a existência de outra página. A evidência
fica em `apify_run_results`; o resumo por query fica em `apify_coverage` e no
relatório. O limite de custo vale por Actor, não por todo o diretório.

O leitor legado respeita offsets fornecidos pela SerpApi. Uma continuação sem
cache não pode ser traduzida automaticamente para números de páginas Apify:
mantém cobertura incompleta e exige novo diretório para uma coleta nova.
Bancos mistos e bancos com localização legada podem usar `--recheck-sites`
sem token de busca. A rechecagem modifica decisões de resolução; mantém o
progresso de busca anteriormente registrado.

## Diretórios e exportação

`--output-dir` é obrigatório e recebe companies.csv, pending.json e report.json.
O caminho é resolvido antes do uso, incluindo links e `~`. Checkouts identificados
a partir do pacote ou da pasta de trabalho, e o próprio pacote instalado, não
podem receber exportações. Em instalação sem checkout não se inventa uma raiz
de repositório. Destinos explícitos externos são criados sob demanda.

O destino não faz parte da identidade da execução. Uma saída nova pode ser
regenerada sem repetir buscas. Um banco antigo pode ficar no diretório original;
o layout recomendado mantém também o SQLite numa pasta externa.

CSV mantém BOM UTF-8, ponto e vírgula, escaping e deduplicação por URL/query.
Cada arquivo é escrito num temporário e substituído com `os.replace`; não há
transação conjunta entre os três arquivos. O SQLite permite regenerar a saída
após uma interrupção. Um processo por diretório de estado e por pasta de saída.

## Limites deliberados

Não há filtro de relevância, maturidade, atividade, propriedade ou geografia da
empresa. País e idioma configuram a busca. Não há identidade jurídica,
resolução de marcas/subsidiárias, crawling recursivo nem execução de JavaScript.

A identidade declarada em JSON-LD ou metadata não é uma verificação independente.
Fontes intermediárias desconhecidas ainda podem gerar falsos positivos; o piloto
deve medir precisão e omissões. A proteção DNS não substitui isolamento de rede
contra servidores maliciosos ou DNS rebinding.

## Referências verificadas em 07/10/2026

- [Input do Actor](https://apify.com/apify/google-search-scraper/input-schema):
  queries, countryCode, languageCode, localização UULE e complementos.
- [Output do Actor](https://apify.com/apify/google-search-scraper):
  registros por página, searchQuery e organicResults.
- [Criação de run](https://docs.apify.com/api/v2/actors-runs-post):
  criação assíncrona e maxTotalChargeUsd.
- [Estado do run](https://docs.apify.com/api/v2/actor-run-get):
  estados transitórios/terminais e estabilização das informações.
- [Fila de requests](https://docs.apify.com/api/v2/request-queue-get):
  pendingRequestCount e handledRequestCount.

A interpretação dos offsets SerpApi permanece compatível com o formato antigo
já documentado na versão 0.1; nenhum endpoint de busca SerpApi é chamado.
