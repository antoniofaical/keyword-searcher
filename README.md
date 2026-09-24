# keyword-searcher

Converte queries em nomes de empresas e entrypoints institucionais via resultados
orgânicos do Google. Saída UTF-8 com BOM, separada por ponto e vírgula:

```csv
CompanyName;URL;SearchQuery
```

Não faz classificação temática, enriquecimento, análise de atividade, aquisições
ou idade da empresa. Não explora notícias nem diretórios para extrair empresas.

## Instalação (PowerShell, Python 3.11+)

```powershell
git clone https://github.com/antoniofaical/keyword-searcher.git
cd keyword-searcher
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Crie `queries.txt` em UTF-8, com uma query por linha. Linhas vazias são ignoradas;
queries exatamente iguais são executadas uma vez. Operadores são preservados.
`examples/queries.txt` contém apenas exemplos, não a lista do projeto.

Configure `SERPAPI_API_KEY` no ambiente local (não inclua a chave no Git ou no chat).
A ferramenta lê essa variável; não carrega `.env` automaticamente.

```powershell
keyword-searcher --queries .\queries.txt --run-dir .\runs\pilot --max-pages 2 --max-search-requests 10
```

Também funciona com `python -m keyword_searcher` e os mesmos argumentos.
O comando faz chamadas ao provedor que podem consumir créditos. O limite é de
**tentativas de requisição**, incluindo retries, não de reais/dólares.
Idioma e país padrão: `pt` e `br`. Configure `--language en --country us` ou
`--location "Sao Paulo,State of Sao Paulo,Brazil"` quando apropriado.

## Retomada e resultados

Repita o mesmo comando para retomar. Respostas de busca já salvas são reutilizadas
sem nova consulta ao Google. O teto de requisições é cumulativo no diretório:
aumentar `--max-search-requests` libera mais tentativas, não zera o contador.
Falhas de rede/429/5xx recebem até duas novas tentativas; erros de autenticação não.
Uma interrupção entre a resposta remota e sua gravação pode exigir nova chamada.

Use `--retry-pending` para tentar resolver novamente sites pendentes. Isso não
repete buscas já armazenadas. Cada diretório fixa queries, idioma, país,
localização, profundidade e versão da ferramenta; alterações exigem outro
`--run-dir`. Use um processo por diretório.

Após atualizar a ferramenta, reavalie todos os resultados já salvos sem gastar
novas consultas de busca (a chave da SerpApi não é necessária nesse modo):

```powershell
keyword-searcher --queries .\queries.txt --run-dir .\runs\pilot --max-pages 2 --max-search-requests 10 --recheck-sites
```

Esse comando revisita os sites dos resultados armazenados, substitui as decisões
anteriores e recria os arquivos de saída. Pode fazer requisições HTTP para os
sites das empresas; não faz novas buscas no Google. Execute-o depois que a
execução anterior terminar, usando o mesmo diretório e a mesma configuração.

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
| `companies.csv` | Somente as três colunas solicitadas |
| `state.sqlite` | Configuração, respostas do provedor com chave ocultada, evidências, resoluções e progresso |
| `pending.json` | Casos pendentes e resultados ignorados, com motivos |
| `report.json` | Cobertura por query, totais por estado, limites, falhas, contagem de chamadas e linhas |

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

O estado `complete` significa que o provedor não indicou uma próxima página.
`limited` significa que havia continuação, mas a profundidade configurada acabou.
`not_started` significa que a query ainda não teve página de busca salva;
`incomplete` significa que uma query iniciada ficou sem cobertura completa.
`failed` e `interrupted` também não são conclusão. O terminal mostra apenas os
totais; `report.json` inclui os detalhes das queries.
Uma query completa ainda pode conter resoluções pendentes.

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
