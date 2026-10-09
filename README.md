# keyword-searcher

Converte queries em nomes de empresas e entrypoints institucionais via resultados
orgânicos do Google, usando Apify. Saída UTF-8 com BOM e ponto e vírgula:

```csv
CompanyName;URL;SearchQuery
```

Não faz classificação temática, enriquecimento, análise de atividade, aquisições
ou idade da empresa. Não explora notícias nem diretórios para extrair empresas.

## Instalação e bootstrap (Python 3.11+)

```powershell
git clone https://github.com/antoniofaical/keyword-searcher.git
cd keyword-searcher
.\bootstrap.ps1
.\.venv\Scripts\Activate.ps1
```

Em Linux/macOS:

```bash
git clone https://github.com/antoniofaical/keyword-searcher.git
cd keyword-searcher
bash bootstrap.sh
source .venv/bin/activate
```

Os scripts criam ou reutilizam `.venv`, instalam `.[dev]` e executam
`pip check`, lint, formatação, pytest e ajuda da CLI. Para indicar o Python no PowerShell:
`.\bootstrap.ps1 -PythonExecutable 'C:\caminho\python.exe'`; no Bash:
`bash bootstrap.sh /caminho/python3`.
Funcionam a partir de outra pasta e com espaços no caminho. Uma venv inválida
é preservada e gera erro; repare-a antes de repetir. Qualquer etapa com falha
encerra o bootstrap. Os scripts não iniciam buscas e não exigem token.

## Buscar e escolher os diretórios

Crie `queries.txt` em UTF-8, com uma query por linha. Linhas vazias são ignoradas;
queries exatamente iguais são executadas uma vez. Operadores são preservados.
`examples/queries.txt` contém apenas exemplos.

Configure `APIFY_API_TOKEN` no ambiente local. A ferramenta não carrega `.env`
automaticamente; não salve o token no Git ou no chat.

A versão 0.2 exige **`--output-dir` fora do repositório**. `--run-dir` identifica
o banco de retomada; `--output-dir` recebe CSV e relatórios. Para uma execução
nova, recomendamos manter ambos numa pasta irmã:

```text
workspace/
  keyword-searcher/
  keyword-searcher-data/
    runs/pilot/state.sqlite
    outputs/pilot/companies.csv
    outputs/pilot/pending.json
    outputs/pilot/report.json
```

```powershell
keyword-searcher --queries .\queries.txt --run-dir ..\keyword-searcher-data\runs\pilot --output-dir ..\keyword-searcher-data\outputs\pilot --max-pages 2 --max-apify-pages 20 --apify-run-cost-limit-usd 1
```

Em Bash, os mesmos argumentos aceitam caminhos com `/`. Também funciona com
`python -m keyword_searcher`. A pasta de saída é criada automaticamente; os
destinos completos aparecem no terminal. Um checkout identificado ou o diretório
do pacote instalado não pode receber a exportação. Em instalação sem checkout,
a pasta de saída continua explícita. Trocar apenas `--output-dir` não invalida
o banco nem gera outra busca.

Idioma de interface e país padrão: `pt` e `br`; altere com
`--language en --country us`. Na chamada Apify, `pt` é convertido em `pt-BR`,
ou em `pt-PT` quando `--country pt`. Valores explícitos `pt-BR` e `pt-PT`
também são aceitos. A conversão conserva `pt` na configuração do banco para
permitir retomada de execuções existentes. Idioma de interface não é filtro de idioma dos
resultados. `--location` legado não é convertido para o UULE da Apify e só pode
ser usado em `--recheck-sites`, para manter a configuração de um banco antigo.

## Apify, limites e retomada

Apify é o único provedor de buscas novas. `--provider apify` continua aceito por
compatibilidade. `--provider serpapi`, `--max-search-requests` e a integração
remota SerpApi foram removidos; `SERPAPI_API_KEY` não é necessária.

O Actor `apify/google-search-scraper` busca em lotes de até 20 queries por padrão.
Ajuste `--apify-batch-size` quando necessário. Complementos pagos de conteúdo,
anúncios, leads e respostas de IA ficam desativados; a seleção de empresas continua
no resolvedor local.

`--max-apify-pages` limita a reserva cumulativa de páginas no diretório. Cada
lote reserva antecipadamente queries × `--max-pages`; o tamanho do lote se ajusta
ao orçamento restante de páginas. A reserva pode ser maior
que o número de páginas efetivamente recebido. O padrão é 900 páginas.
`--apify-run-cost-limit-usd` envia um teto de cobrança por Actor à Apify,
com padrão de US$ 1. **Esse teto não limita o gasto total de vários lotes.**
Os limites dos exemplos não aprovam um orçamento.

Repita o comando para retomar. Páginas e resoluções armazenadas são reutilizadas.
O ID de um run confirmado permanece no banco; uma interrupção durante polling
ou leitura do dataset recupera o mesmo run. GETs com falha de rede, 429 ou 5xx
recebem até duas novas tentativas; autenticação não recebe retry.

A criação de um Actor nunca é repetida automaticamente após uma resposta incerta.
Erros HTTP mostram o código, o tipo e a mensagem da API, com token ocultado.
Mesmo com essa informação, uma tentativa sem ID confirmado continua bloqueada
até a verificação no console; GET de autenticação bem-sucedido não comprova
permissão para executar o Actor nem que seus parâmetros foram aceitos.
Se aparecer `Apify batch is unconfirmed`, confira Runs no console Apify e vincule
o ID correto com `--apify-recover-run-id ID`. Se um run não puder ser recuperado,
confira-o e use `--apify-abandon-batch` para permitir uma nova tentativa.
Isso não devolve créditos nem reduz a reserva de páginas.
Por exemplo, 19 queries com `--max-pages 5` reservam 95 páginas. Se o console
confirmar que nenhuma execução foi criada, abandonar essa tentativa e reservar
o mesmo lote novamente exige pelo menos `--max-apify-pages 190`. Isso é um limite
cumulativo local; o teto monetário por Actor permanece o valor configurado.

A importação valida todo o dataset e salva páginas e cobertura numa transação.
Uma página com dez links não comprova continuação. Não há outro Actor automático
para procurar uma página ausente. Cobertura sem evidência suficiente fica
`incomplete`: isso pode ocorrer mesmo após um run `SUCCEEDED`, quando custos
ou contagens da fila não permitem verificar o encerramento.

Use um processo por diretório de estado **e** por diretório de saída.
`--retry-pending` revisita somente resoluções pendentes e reutiliza as buscas salvas.

## Bancos antigos e rechecagem

A migração do esquema 1 para 2 preserva IDs, páginas, resoluções, contadores e
reservas; registra a configuração original e a razão da migração em `meta`.
Novas configurações usam `provider=apify-google`. A versão do software deixa
de definir a identidade de uma execução; queries, país, idioma, localização e
profundidade continuam fixos. Uma mudança nesses campos exige outro `--run-dir`.

Para retomar um banco antigo, conserve seu `--run-dir` e acrescente uma saída
externa ao comando. Não mova o SQLite enquanto houver um processo usando-o.
O leitor legado interpreta respostas SerpApi, inclusive offsets variáveis, sem
fazer chamadas a esse serviço. Bancos mistos também podem ser rechecados.

```powershell
keyword-searcher --queries .\queries.txt --run-dir .\runs\pilot --output-dir ..\keyword-searcher-data\outputs\pilot --max-pages 2 --recheck-sites
```

Mantenha o arquivo e os parâmetros originais do banco. Se a execução antiga tinha
`--location`, acrescente o mesmo valor à rechecagem. Esse comando revisita os
sites, substitui as decisões anteriores e recria a saída; não usa token nem
inicia buscas, mas faz requisições HTTP aos sites.

Uma continuação legada sem página armazenada não é convertida automaticamente
em uma nova busca Apify: offsets antigos não têm correspondência garantida.
O relatório registra `cached_continuation_unverified_use_new_run`. Reutilize os
resultados existentes e, para uma nova coleta, escolha outro diretório de estado.
Páginas Apify antigas sem evidência de encerramento também podem ficar incompletas.

Durante a execução, o terminal interativo mostra barras coloridas de queries,
páginas da query atual e links da página atual, além da operação em andamento e
do tempo decorrido. A barra de páginas indica uso do limite configurado, não
quantas páginas o Google ainda oferecerá. Em redirecionamento de saída, os mesmos
eventos aparecem como linhas simples; `--no-progress` os desativa. O progresso
é somente visual: `state.sqlite` continua sendo a fonte para retomada. Se você
atualizar o código durante uma execução, as barras aparecerão na próxima
invocação, que poderá reutilizar `--run-dir`.

| Arquivo | Conteúdo |
|---|---|
| `--output-dir/companies.csv` | Somente as três colunas solicitadas |
| `--run-dir/state.sqlite` | Configuração, respostas do provedor com chave ocultada, evidências, resoluções e progresso |
| `--output-dir/pending.json` | Casos pendentes e resultados ignorados, com motivos |
| `--output-dir/report.json` | Cobertura por query, totais por estado, limites, falhas, contagem de chamadas e linhas |

Uma URL aparece uma vez por query. A mesma URL pode aparecer em queries diferentes.
Não há fusão de marcas por domínio. Subdomínios, paths e parâmetros funcionais são
preservados; fragmentos e parâmetros conhecidos de rastreamento são removidos.

## O que significa confirmado nesta versão

A resolução é **heurística e conservadora**, baseada na identidade declarada
pelo próprio site e em sinais de oferta própria de produtos ou serviços.
O nome não é adivinhado a partir do domínio ou do título de busca.

1. Classifica plataformas reconhecidas de publicação, pesquisa, financiamento e
   diretórios antes de acessá-las. Um domínio `.org` ou `.edu` não é veto.
2. Inspeciona a página encontrada. Marcação `Article`, `Review` ou `ItemList` e
   caminho de blog não descartam automaticamente um fornecedor com home própria.
3. Verifica a home do mesmo host ou um link explícito de home/logo; um URL de
   produto que declara a si mesmo não se torna automaticamente um entrypoint.
4. Compara `Organization`, `WebSite`, `og:site_name` e copyright. Conflitos entre
   marca e agência geram pendência. Sufixos jurídicos usuais e `alternateName`
   explícito permitem comparar nomes sem consolidar marcas por domínio.
   Na 0.2.4, isso inclui AG, ApS, GmbH e outras formas usuais. Slogans delimitados
   em `WebSite`/`og:site_name` ou copyright só são removidos para comparação se o
   prefixo completo concordar com outro nome declarado; `Organization` não é truncado.
   Copyright é lido em avisos de propriedade no rodapé, excluindo SVGs, créditos
   em artigos, links de política e licenças de fontes.
5. Na ausência de metadata, exige concordância entre contextos diferentes:
   título, logo, copyright ou autodescrição visível. Pode consultar uma página
   institucional About/contato do mesmo host para apoiar a identidade da home.
6. Exige sinais de oferta própria, como menu de produtos/serviços no mesmo host,
   planos de assinatura ou solicitação de demonstração. Pesquisa acadêmica,
   publicação ou identidade isolada não bastam. Um instituto com serviços
   próprios pode ser confirmado; isso não afirma que seja startup ou empresa privada.
   Frases como “our service” em cookies/rodapés não contam como oferta. Identidade
   explícita de portal de pesquisa ou fonte editorial prevalece sobre menus genéricos.
7. Exporta o endereço institucional final. Erros funcionais na URL, páginas de
   login, bloqueio e desafios ficam pendentes, mesmo com metadata de marca.

As páginas inspecionadas por resultado são limitadas a cinco, incluindo origem,
homes e uma página de apoio por home, dentro desse limite. Um cache de até 128
páginas/erros e 8 MiB de conteúdo HTML evita repetir acessos na mesma execução
(o HTML analisado ocupa memória adicional). Não há crawling recursivo ou execução
de JavaScript. Sites dependentes de JavaScript, sem identidade verificável na
home, protegidos por CAPTCHA ou com identidades complexas
podem ficar pendentes. Não é uma verificação jurídica da empresa: metadata pode
estar errada. O reconhecimento de fontes intermediárias também é heurístico;
diretórios desconhecidos podem produzir falsos positivos. É necessário medir
precisão e cobertura numa amostra real antes do uso em escala. Um HTTP 403 na
página profunda permite tentar a home do mesmo host; a falha original permanece
na evidência. Não há bypass de CAPTCHA ou autenticação. HTTP 202/203 não são
tratados como HTML válido; o reconhecimento prévio de PubMed evita confundi-lo
com fornecedor. Um banner comum de cookies não bloqueia uma página válida.

As decisões registram versão da política, URLs inspecionadas, sinais de identidade,
oferta e falhas em `Resolution.evidence` no SQLite e em `pending.json` para decisões
não confirmadas. O CSV mantém exatamente `CompanyName;URL;SearchQuery`.

## Reavaliar o piloto com as novas regras

Com o processo anterior encerrado, execute:

```bash
python -m keyword_searcher.recheck --run-dir ../keyword-searcher-data/runs/pilot --output-dir ../keyword-searcher-data/rechecks/pilot_v2
```

A pasta de saída deve ser nova e externa ao repositório. O comando não exige
queries, token ou repetição das opções da busca. Valida as páginas salvas, faz um
backup consistente `state-before.sqlite` e reavalia uma cópia `state.sqlite`.
O banco original permanece intacto. Revisita resultados confirmados, pendentes
e descartados usando apenas GETs dos sites; não instancia transporte Apify,
não inicia Actor nem solicita novos resultados do Google.

Gera `companies.csv`, `pending.json`, `report.json`, `handoff.json`,
`handoff.metadata.json`, `recheck_summary.json` e
`recheck_changes.json`. O último registra decisões anteriores e atuais por
query/URL. Queries, páginas, progresso, configuração, IDs de runs e reservas são
copiados sem alterações. Auditorias e campos de revisão humana existentes não
são sobrescritos. Para continuar usando as novas decisões na CLI principal,
aponte `--run-dir` para essa nova pasta e conserve as opções originais de busca.

Ctrl+C exporta o estado parcial e retorna 130. Para repetir a reavaliação,
use a pasta da cópia parcial como `--run-dir` e escolha outra pasta de saída.
O comando reavalia novamente todos os sites salvos. Código 0 indica que a
reavaliação terminou, mesmo quando permanecem pendências ou buscas limitadas.
O banco continua contendo somente a cobertura coletada anteriormente.

O estado `complete` exige evidência de encerramento: run bem-sucedido,
saída zero, custo abaixo do teto e fila sem pendências, com contagem de requisições
tratadas igual à quantidade de páginas importadas. O custo é relido após dez
segundos para evitar valores preliminares. Essa política é conservadora e depende
dos metadados do Actor; não promete coletar todos os resultados do Google.
`limited` significa que o dataset atingiu a profundidade configurada; não afirma
que há mais páginas. `incomplete` inclui teto de custo, evidência insuficiente e
continuação não verificável. `not_started` não tem página salva; `failed` e
`interrupted` também não são conclusão. `report.json` inclui motivos, cobertura por
query e evidência dos runs. Uma query completa pode ter resoluções pendentes.
O contador `search_requests` é histórico da SerpApi; não cresce nas buscas novas.
`apify_pages` conta páginas salvas; `apify_reserved_pages` conta reservas cumulativas.

Códigos de saída: `0` sem consultas incompletas nem resoluções pendentes; `2` com
limites, falhas ou pendências; `130` interrupção; `1` erro de configuração/arquivo.
Resultados ignorados por tipo de fonte não são pendências de resolução.

## Auditoria offline e correção de pendências editoriais

Na versão 0.2.2, páginas reconhecidas como editoriais ou fontes intermediárias
recebem `skipped`. Falhas HTTP e identidade ausente continuam `pending`; os
critérios de identidade e relevância daquela versão não foram ampliados.
Na 0.2.3, prefira a reavaliação acima para aplicar a política nova; a correção
offline abaixo só altera o estado de motivos antigos e não reexamina evidências.

Com a execução principal encerrada, gere uma amostra dos resultados salvos:

```bash
python3 -m keyword_searcher.audit --run-dir ../keyword-searcher-data/runs/pilot --output-dir ../keyword-searcher-data/audits/pilot_v1 --sample-size 20
```

O modo padrão só lê o banco. Para corrigir decisões antigas já marcadas com
`editorial_or_listing_page` ou `non_institutional_source`, acrescente
`--reclassify-editorial`. Antes da atualização, a ferramenta cria um backup
consistente em `state-before.sqlite` na pasta da auditoria. Apenas o estado dessas
pendências muda para `skipped`; IDs, queries, páginas, reservas, progresso e
demais decisões são preservados. Esse comando exige banco de esquema 2 e não o migra.

Não há requisições a sites nem ao Apify, e não é necessário token. A pasta de
auditoria deve ser externa ao repositório e ainda não existir. Para gerar outra
amostra, escolha outra pasta; isso preserva revisões humanas anteriores.

São selecionadas até 20 URLs por motivo, agrupando queries que encontraram a
mesma URL e priorizando hosts distintos. O padrão cobre identidade ausente,
HTTP 403, páginas editoriais, HTTP 203 e fontes intermediárias ainda pendentes.
`--sample-size` altera o limite por motivo; `--seed` fixa a seleção reproduzível
(padrão 42). A amostra é diagnóstica, não uma estimativa de prevalência.

`audit_cases.csv` contém IDs estáveis, URLs, queries, títulos/snippets da busca e
estados antes/depois. `ReviewOutcome` e `ReviewerNotes` ficam vazios para revisão
humana. `audit_summary.json` informa as contagens e correções. A mesma pasta
recebe cópias atualizadas de `companies.csv`, `pending.json` e `report.json`;
as exportações antigas permanecem como estavam.

`audit_bundle.zip` reúne esses cinco arquivos CSV/JSON e os dois arquivos de
handoff para revisão. O SQLite e
seu backup ficam fora do ZIP. Uma auditoria bem-sucedida retorna código 0 mesmo
quando há pendências, pois esse código informa a geração do pacote, não a
conclusão da descoberta.

## Handoff para startup-adherence

A partir da versão 0.2.5, cada exportação gera automaticamente `handoff.json`
e `handoff.metadata.json` junto ao CSV e aos relatórios, fora do repositório.
Isso vale para execução normal, retomada, Ctrl+C, recheck e auditoria.
As regras de confirmação e o CSV de três colunas continuam os mesmos.

`handoff.json` é uma lista UTF-8 sem BOM, diretamente compatível com
`startup-adherence --sites-file`:

```json
[
  {"name": "Synthetic Supplier", "url": "https://supplier.example.org/"}
]
```

O exemplo é sintético. Só resoluções confirmadas entram na lista, usando o
entrypoint institucional salvo. Pendentes e descartadas ficam no output atual.
O handoff conserva a primeira ocorrência na ordenação por query/URL descoberta,
deduplicando por nome exato ou URL equivalente conforme o receptor. HTTP/HTTPS,
`www`, parâmetros e barras finais não distinguem URLs equivalentes; outros
caminhos e subdomínios distinguem, mas o mesmo nome ainda conserva apenas a
primeira URL. Não há escolha automática por aderência nem fusão por similaridade.

`handoff.metadata.json` registra versão de contrato, produtor, receptor,
contagens, estado das queries e, por candidato, todas as queries, URLs
descobertas, decisões/evidências salvas e URLs/nomes alternativos. Os campos
`coverage.search_complete`, `coverage.interrupted` e `coverage.partial`
distinguem encerramento da busca, interrupção da exportação atual e pendências
ou cobertura parcial. Limite de páginas não é conclusão exaustiva. O contador
`duplicates` usa ocorrências confirmadas do banco, não linhas do CSV.

O consumidor conserva uma única URL por nome e seu crawl não inclui outros
subdomínios por padrão. As alternativas do manifesto precisam de revisão
quando forem importantes para cobertura. Nomes diferentes que colidem no
diretório de evidência do receptor, nomes inválidos ou URLs incompatíveis geram
erro antes de substituir os outputs. O estado fica salvo para revisão e nova
exportação; nomes não são renomeados silenciosamente. Zero confirmados produz
`[]`; o consumidor deve verificar a contagem antes de iniciar (`No sites selected`).

Para gerar o handoff de um estado já existente, sem visitar sites ou fazer
buscas, use o exportador offline:

```bash
python -m keyword_searcher.export --run-dir ../keyword-searcher-data/rechecks/pilot_v3 --output-dir ../keyword-searcher-data/handoffs/pilot_v3
```

Ele abre `state.sqlite` somente para leitura, sem migrar o banco, e regenera os
cinco outputs. Pode substituir esses arquivos regeneráveis na pasta de saída;
arquivos de revisão humana, queries, páginas, IDs e decisões do banco não são
alterados. Código 0 informa exportação concluída, mesmo se o manifesto indicar
pendências/limites. Destino dentro do checkout é recusado.

O perfil de aderência é selecionado separadamente. O comando seguinte é
ilustrativo e exige um perfil aprovado já existente:

```bash
startup-adherence --mode crawl --sites-file ../keyword-searcher-data/handoffs/pilot_v3/handoff.json --profile /caminho/perfil-aprovado.json --evidence-root ../keyword-searcher-data/evidence/pilot_v3
```

Gerar o handoff não inicia crawl, Jev, DeepL nem outra busca Apify. A classificação
é uma etapa posterior. Sem `--profile`, o receptor usa digital twins; esse
padrão não deve ser adotado automaticamente para outro tema. O modo padrão do
receptor é `smoke`, que inclui classificação paga após confirmação; especifique
`--mode crawl` para apenas coletar evidência pública.

Contrato verificado no receptor em
[`dbd6c4c`, `select_sites`](https://github.com/antoniofaical/startup-theme-adherence-classifier-jev/blob/dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b/src/startup_adherence/cli.py#L141-L199).
Veja [o contrato de handoff](docs/handoff.md) para o manifesto e a validação offline.

## Desenvolvimento

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Veja [arquitetura e contratos](docs/architecture.md) e
[estado da validação](docs/validation.md). Testes usam empresas/domínios fictícios
explicitamente sintéticos. Não representam um piloto de mercado.
