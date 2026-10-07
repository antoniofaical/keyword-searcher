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
`--language en --country us`. Idioma de interface não é filtro de idioma dos
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
Se aparecer `Apify batch is unconfirmed`, confira Runs no console Apify e vincule
o ID correto com `--apify-recover-run-id ID`. Se um run não puder ser recuperado,
confira-o e use `--apify-abandon-batch` para permitir uma nova tentativa.
Isso não devolve créditos nem reduz a reserva de páginas.

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
pelo próprio site (JSON-LD `Organization`/`WebSite` ou `og:site_name` na home).
O nome não é adivinhado a partir do domínio ou do título de busca.

1. Inspeciona a página encontrada, exceto fontes intermediárias reconhecidas e
   caminhos editoriais identificados.
2. Procura uma única organização com nome e URL no mesmo host (aceitando `www`).
3. Verifica a home do mesmo host ou um link explícito de home/logo; um URL de
   produto que declara a si mesmo não se torna automaticamente um entrypoint.
4. Compara a identidade da home com a declarada na página encontrada, se houver.
5. Exporta o endereço final após redirecionamentos. Ausência ou conflito de
   evidências produz pendência, nunca um nome inventado.

As páginas inspecionadas por resultado são limitadas à origem, home declarada,
link explícito de home/logo e raiz do host. Não há crawling recursivo ou execução
de JavaScript. Sites dependentes de JavaScript, sem identidade verificável na
home, protegidos por CAPTCHA ou com identidades complexas
podem ficar pendentes. Não é uma verificação jurídica da empresa: metadata pode
estar errada. O reconhecimento de fontes intermediárias também é heurístico;
diretórios desconhecidos podem produzir falsos positivos. É necessário medir
precisão e cobertura numa amostra real antes do uso em escala.

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

## Desenvolvimento

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Veja [arquitetura e contratos](docs/architecture.md) e
[estado da validação](docs/validation.md). Testes usam empresas/domínios fictícios
explicitamente sintéticos. Não representam um piloto de mercado.
