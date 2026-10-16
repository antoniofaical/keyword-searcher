# Arquitetura e contratos — 0.2.12

## Responsabilidade

Entrada: texto UTF-8, uma query por linha. Saída: `CompanyName;URL;SearchQuery`.
Apify fornece resultados orgânicos do Google. Ads, notícias, diretórios e
comparativos não alimentam descoberta indireta. A versão 0.2.3 distingue oferta
própria, fonte editorial e conflitos de identidade; a normalização de URLs é preservada.

## Componentes

| Arquivo | Responsabilidade |
|---|---|
| bootstrap.ps1 / bootstrap.sh | Ambiente virtual, instalação e checks sem buscas |
| models.py | Contratos de busca, resolução e cobertura terminal |
| cli.py | Configuração, diretórios explícitos, composição e códigos de saída |
| apify.py | Actor, reserva de páginas, recuperação, dataset e evidência de término |
| apify_errors.py | Associação de registros explícitos de falha à query/página |
| search.py | Leitura de payloads SerpApi antigos; nenhuma integração remota |
| http.py / urls.py | HTTP limitado, normalização e verificação de destinos |
| resolve.py | Identidade declarada e seleção limitada do entrypoint |
| identity.py / site_policy.py | Concordância de identidade, oferta própria e fontes |
| recheck.py | Reavaliação em cópia com backup, sem transporte Apify |
| store.py | SQLite, esquema versionado, migração e dados para exportação |
| export.py | Validação de destino externo e escrita atômica por arquivo |
| audit.py | Amostra offline, backup e correção explícita de estados editoriais |
| pipeline.py | Iteração, retomada e rechecagem das páginas salvas |
| workers.py | Resolução concorrente limitada e persistência no fluxo coordenador |
| parallel_pipeline.py | Leitura alternada de queries e alimentação global dos workers |
| search_ahead.py | Uma tarefa Apify em paralelo aos sites, sem SQLite na thread |
| job_queue.py | Backlog temporário em disco, com seleção por host elegível |
| worker_diagnostics.py | Contexto limitado de falhas fatais, sem mensagem bruta ou locals |
| pagination.py | Continuação segura de páginas repetidas já importadas da Apify |

O transporte é injetável. Testes de contrato usam dados sintéticos e não precisam
de credenciais nem de buscas reais.

## Concorrência dos sites

CLI e recheck usam `--workers 8` por padrão; `--workers 1` não cria executor.
As funções de baixo nível `run`/`recheck_sites` conservam o padrão sequencial para
compatibilidade; os pontos de entrada passam a quantidade explicitamente.
Threads de sites executam somente `resolver.resolve`. Desde a 0.2.10, uma thread
separada faz o POST e os GETs Apify; reserva de páginas, persistência do ID, validação
e importação do lote permanecem no coordenador, assim como todo acesso SQLite e
exportação. A leitura alterna entre consultas salvas, preservando a ordem das páginas
dentro de cada consulta. Cada resultado é persistido sem esperar os demais.
Exportações conservam a ordenação query/URL do SQLite.

Há no máximo N futures no executor. Os demais jobs ficam num SQLite temporário
privado, excluído ao fechar. Um índice de cabeças por host permite escolher trabalho
fora dos hosts ocupados sem percorrer seu backlog inteiro. A antiga janela de 4N
jobs foi removida porque podia esconder consultas posteriores. Uma resolução por
host de origem ocupa o pool; o limitador HTTP também cobre destinos de redirects.
Os payloads em espera ficam em disco; cursores guardam URLs vistas por consulta.
Worker count é teto, não promessa de ocupação constante: é necessário haver hosts
independentes prontos; cache, CPU, latência e fim do corpus também afetam atividade.

O coordenador contabiliza jobs por query. Após ler sua última página, registra
o resultado esperado; só encerra a query no SQLite/progresso quando o contador
chega a zero. Interrupções identificam queries ainda sem decisões; queries já
concluídas mantêm seus estados. Uma falha inesperada do worker é atribuída à
query original, mesmo se a fonte já avançou para outra, e interrompe o fluxo com
`site_worker_failed`. O recheck usa os mesmos callbacks sem alterar progresso de
busca da cópia. O pool não é drenado nas transições de lote. O coordenador continua
alimentando/verificando sites e persistindo decisões enquanto uma tarefa aguarda a
Apify. O próximo lote é planejado assim que uma consulta sem páginas é encontrada,
inclusive se ainda houver trabalho local de lotes anteriores. Só há uma tarefa
remota: POST, registro do ID no SQLite, polling/fetch e importação atômica precedem
o próximo POST. Reservas cumulativas e teto por Actor continuam iguais.

Na interrupção durante um POST já enviado, aguarda-se sua resposta para preservar
o ID, ou mantém-se o lote incerto sem retry automático. Na espera por um run, o
cancelamento interrompe apenas os GETs locais, sem abortar o Actor. A retomada usa
o run salvo. Uma chamada de rede bloqueante ainda pode atrasar a parada até seu
timeout (60 s por chamada Apify). O caminho `--workers 1` continua sequencial.

Desde a 0.2.11, falhas do worker paralelo identificam a etapa de resolução, persistência ou
progresso. A CLI de busca e a de recheck gravam `worker_error.json` atomicamente
junto dos outputs, com tipo, query, localização do site, SHA-256 da URL exata e
até 12 frames de arquivo/linha/função. Mensagens brutas, HTML, source lines, locals,
userinfo, parâmetros e fragmentos não são incluídos. Tokens Apify conhecidos são
redigidos. Erros de gravação do diagnóstico mantêm a falha original visível. O
arquivo é o último erro fatal e pode permanecer após uma retomada bem-sucedida.

Normalização de URLs converte o path IRI em percent-encoding UTF-8 antes do cliente
HTTP, preservando os escapes existentes e delimitadores de caminho. Isso cobre
fontes, About/contato e redirects sem degradar identidade ou falhas inesperadas
para uma decisão de classificação.

Cada thread HTTP usa seu próprio opener. Uma Condition protege os slots ativos
por hostname, normalizado e sem `www`; não é um limite por domínio registrável.
O slot cobre validação DNS, abertura e leitura. Redirecionamentos liberam o slot
anterior antes de disputar o destino, preservando validação de URL/DNS e o limite
de seis operações. Hosts inativos não ficam guardados no limitador.

O cache LRU e suas atualizações são protegidos por RLock. Uma Future por URL em
carregamento compartilha a página ou o motivo de erro entre concorrentes. Futures
são removidas após o carregamento; erros retêm somente texto, sem traceback.
Os limites de 128 entradas/8 MiB continuam iguais. HTMLs BeautifulSoup publicados
no cache são somente leitura para as políticas; futuras alterações não devem
mutá-los. Resoluções completas não são compartilhadas entre fontes diferentes.

Em interrupções, o cancelamento cooperativo impede novas inspeções/GETs e desperta
esperas por host. O coordenador cancela futures não iniciadas, aguarda operações
ativas e persiste decisões bem-sucedidas antes de exportar. Cancelamento não vira
`pending` ou `skipped`; resultados sem decisão são retomados pelas buscas salvas.
Não há encerramento forçado de threads: DNS e operações bloqueantes podem atrasar
a parada. O timeout de 20 s é por operação HTTP, não um deadline de resolução.

O progresso usa seu próprio RLock; callbacks dos workers apenas atualizam o
terminal. Mostra contagem ativa, fila aguardando, hosts elegíveis, estado da Apify,
decisões contabilizadas e o tempo
da resolução mais antiga, separado do tempo total. Em paralelo, os contadores de
páginas e resultados são globais; queries concluídas podem terminar fora da ordem
de entrada. Quantidade de workers não entra na identidade
do banco. A reavaliação continua usando cópia/backup e preserva o estado original.

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
Quando `--max-apify-pages` é omitido, a CLI calcula queries únicas × `--max-pages`.
O limite explícito prevalece. Esse orçamento não identifica a configuração do
banco, então pode mudar na retomada sem repetir páginas salvas; reservas anteriores,
inclusive lotes abandonados, continuam contando.

A criação de Actor é enviada uma vez. GETs de status/dataset podem receber dois
retries para rede, 429 e 5xx. Após confirmação, o run ID é persistido. Se a criação
ficar incerta, só um ID recuperado explicitamente permite continuar. Não existe
garantia de exactly-once na rede.

## Paginação e evidência de término

Na 0.2.12, novidade de URLs e término da busca são conceitos separados. Uma página
Apify repetida pode ser ignorada na resolução e a leitura avança se a sucessora já
está salva. A página terminal, mesmo repetida, conserva `end_status`/`end_reason`
importados; não há promoção automática de cobertura incompleta para completa.
Paginação legada e continuações repetidas sem sucessora salva mantêm a proteção
`repeated_results`. Nenhuma busca nova é iniciada para contornar essa proteção.

`--saved-searches-only` impede qualquer chamada ao provider para payload ausente,
nos caminhos sequencial e paralelo. Consultas ausentes ficam `not_started` e
continuações ausentes ficam `incomplete`, com `saved_search_page_missing`.
Outras consultas salvas continuam sendo processadas. O modo não modifica o estado
de recuperação Apify, dispensa token e não entra na identidade semântica do banco.
Decisões existentes são reutilizadas salvo opção explícita de retry/recheck.

O dataset deve conter uma sequência contígua de posições por query, sem duplicatas
de páginas válidas, e deve representar todas as queries do lote por dados ou erro
explícito reconhecido. Toda a validação precede uma transação
que importa as páginas e grava cobertura, evidência do run e conclusão do lote.

A versão 0.2.8 reconhece `#error: true` com `#debug` de GET para `/search` em
host Google reconhecido. `q` precisa identificar exatamente uma query do lote;
`start` deve ser não negativo, múltiplo de dez e resultar em página dentro do
limite. Parâmetros duplicados, credenciais, origem estranha e divergência com
`searchQuery` são rejeitados. Mensagens/stack traces arbitrários não são guardados.
Persistem somente query, página, motivo, retry count e request ID validado.

Uma falha mapeada preenche a posição ausente com um marcador `search_error`,
com lista de resultados vazia, motivo explícito e cobertura incompleta. Esse
marcador não representa uma página coletada: fica fora de `apify_pages` e não
vira resolução de empresa. Ele permite atravessar uma falha intermediária e
processar as páginas posteriores já entregues. A cobertura expõe `pages` como
quantidade de páginas válidas, `last_page` como última posição e `failed_pages`.
Erro e sucesso na mesma posição preservam os dados válidos e divulgam o erro;
erros repetidos não criam outra posição. A query continua conservadoramente
incompleta. Erros de outra query/origem e lacunas sem erro explícito bloqueiam
tudo antes da transação. Um lote somente com erros nunca é declarado completo.

Evidência do run adiciona `dataset_records`, `search_errors` e `completion_reason`.
O número de registros do dataset inclui erros, enquanto o número de páginas
válidas não inclui marcadores. Lotes são concluídos localmente após essa importação
validada, preservando ID/reservas. Repetir a execução não procura páginas faltantes
nem recria Actors; recheck/export leem também os marcadores sem novas buscas.
O esquema SQLite continua 2 e os formatos CSV/handoff não mudam.

Uma página com dez links não cria uma continuação. A próxima página precisa
existir no dataset. O offset local Apify é uma chave derivada do número de página,
não uma promessa sobre a quantidade de resultados.

Para classificar uma query como completa, a política exige run `SUCCEEDED`,
exit code zero, custo finito abaixo do teto, fila sem pendências e contagem de
requisições tratadas igual à quantidade de registros do dataset, incluindo erros.
Uma query com registro de falha nunca é completa, mesmo com essa evidência válida.
A API é relida
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

`--output-dir` é obrigatório e recebe companies.csv, pending.json, report.json,
handoff.json e handoff.metadata.json. `handoff.py` prepara o contrato do consumidor
e a rastreabilidade sem acessar rede; export.py escreve os cinco arquivos.
O caminho é resolvido antes do uso, incluindo links e `~`. Checkouts identificados
a partir do pacote ou da pasta de trabalho, e o próprio pacote instalado, não
podem receber exportações. Em instalação sem checkout não se inventa uma raiz
de repositório. Destinos explícitos externos são criados sob demanda.

O destino não faz parte da identidade da execução. Uma saída nova pode ser
regenerada sem repetir buscas. Um banco antigo pode ficar no diretório original;
o layout recomendado mantém também o SQLite numa pasta externa.

CSV mantém BOM UTF-8, ponto e vírgula, escaping e deduplicação por URL/query.
Cada arquivo é escrito num temporário e substituído com `os.replace`; não há
transação conjunta entre os cinco arquivos. O contrato de handoff é validado
antes de substituir qualquer output; falhas de escrita preservam o arquivo de
destino afetado e removem seu temporário. O SQLite permite regenerar a saída
após uma interrupção. Um processo por diretório de estado e por pasta de saída.

`python -m keyword_searcher.export` abre um estado de esquema 2 somente para
leitura e chama o mesmo exportador, sem migração, resolução, token ou provedor.
Report recebe um resumo aditivo de handoff; CSV, SQLite e regras de identificação
não mudam. Interrupções da CLI e do recheck são passadas explicitamente ao
manifesto. O contrato e as regras de deduplicação estão em [handoff.md](handoff.md).

## Auditoria e estados de resolução

Fontes intermediárias e instituições sem oferta própria recebem `skipped`.
Artigos próprios podem resolver para a home do fornecedor. `Article` isolado
não veta identidade institucional nem permite extrair empresas mencionadas.
Erros de acesso, oferta não verificável e identidade ambígua ficam `pending`.
O fallback de HTTP 403 tenta somente a home do mesmo host; desafios e erros
funcionais não permitem confirmação. A evidência guarda a falha original.

Identidade estruturada conflita quando os nomes não concordam nem têm alias
explícito. Sem metadata, dois contextos concordantes apoiam a identidade.
Na política 3, sufixos jurídicos comuns são comparados sem alterar nomes salvos.
Slogans delimitados de metadata de apresentação exigem um prefixo independente
concordante; nomes de `Organization` não são encurtados. Copyright deve ser um
aviso de proprietário em rodapé; créditos de artigos/SVGs e links não o substituem.
Portais de pesquisa explícitos e fontes editoriais prevalecem sobre menus de oferta;
frases sobre serviço em banners de cookies não constituem oferta própria.
Título e domínio de busca não identificam uma empresa. Não há filtro temático.
São visitadas no máximo cinco URLs por resultado, com cache de 128 entradas e
8 MiB de conteúdo HTML (o HTML analisado ocupa memória adicional).

`recheck.py` valida payloads salvos, abre o original em modo somente leitura e
usa SQLite backup para produzir `state-before.sqlite` e uma cópia de trabalho.
`ApifyGoogle.parse` é usado como leitor estático de formatos novos e legados;
nenhum transporte é criado. Só a tabela de resoluções da cópia é atualizada.
Resultados parciais são exportados em interrupções; cada nova saída recusa uma
pasta existente para preservar revisão humana. As diferenças de decisão são
registradas separadamente, sem alterar os IDs do material de auditoria.

`Store.open_existing` usa SQLite em modo `ro` por padrão, sem criar ou migrar
bancos. O comando offline exige esquema 2 e pasta de saída nova. Com a opção
explícita de correção, abre em modo `rw`, usa a API de backup do SQLite antes de
um UPDATE transacional e altera apenas `payload.status` das pendências com os
dois motivos acima. Os demais campos JSON e tabelas permanecem preservados.
Execute a auditoria sem outro processo usando o mesmo estado.

A seleção usa motivo/URL como chave, agrega queries, ordena a entrada e aplica
seed fixa, priorizando hosts distintos em cada estrato. Os IDs dos casos derivam
de SHA-256 de motivo/URL. Títulos e snippets vêm apenas das páginas armazenadas;
não há fetch de conteúdo. Os campos de decisão humana são deixados vazios e uma
pasta existente é rejeitada para evitar sobrescrita de revisões.

As exportações regeneradas vão para a pasta da auditoria. O ZIP contém somente
CSV/JSON, sem SQLite ou backup. Se a exportação falhar após o UPDATE, o backup
permanece disponível e a saída pode ser regenerada em outra pasta. A operação
de banco é atômica; a produção de todos os arquivos não constitui uma transação
conjunta.

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
