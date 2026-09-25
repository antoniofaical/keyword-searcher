# Arquitetura e contratos

## Responsabilidade

Entrada: texto UTF-8, uma query por linha. Saída: `CompanyName;URL;SearchQuery`.
Busca apenas resultados orgânicos do Google. Ads, knowledge graphs, notícias,
diretórios e comparativos não alimentam descoberta indireta.

## Componentes

| Módulo | Responsabilidade |
|---|---|
| `models.py` | Contratos `SearchProvider`, `EntrypointResolver` e modelos |
| `cli.py` | Configuração, composição de dependências e códigos de saída |
| `search.py` | Adaptador SerpApi, retries e interpretação de paginação |
| `apify.py` | Busca Apify em lotes, recuperação do Actor, importação dos resultados orgânicos |
| `http.py` | HTTP com timeout, tamanho e redirecionamentos limitados |
| `urls.py` | Normalização e rejeição de destinos privados conhecidos via DNS |
| `resolve.py` | Inspeção limitada de identidade e seleção do entrypoint |
| `store.py` | SQLite, reserva de chamadas e exportação atômica por arquivo |
| `pipeline.py` | Iteração e retomada, sem dependência de um fornecedor concreto |

O transporte e as dependências são injetados; não é necessário consultar a rede
para testar contratos e falhas. A busca Apify aceita várias queries por Actor;
os resultados de ambas as fontes alimentam a mesma resolução local.

## Persistência

Cada diretório representa uma execução e fixa suas queries, idioma, país,
localização, profundidade e versão. Por compatibilidade com execuções anteriores,
o campo `provider` da configuração permanece com o valor legado `serpapi-google`:
o provedor efetivo de uma página Apify é registrado no próprio payload. A chave
local de uma página é `(query, start)` dentro desse contexto. As respostas são preservadas com a chave
substituída por `[REDACTED]`. A resolução armazena nome, URL, motivo e evidência
extraída; HTML completo não é arquivado. Revalidar uma identidade exige revisitar
o site. A busca é salva antes da resolução; cada resolução é salva individualmente.

Chamadas são reservadas numa transação antes do envio. Esse contador é um teto
operacional de tentativas, não uma estimativa monetária. É possível que conte uma
tentativa que não chegou ao provedor. Não há promessa de exactly-once na rede.
Lotes Apify reservam antecipadamente o máximo de páginas, persistem o ID do Actor
e permitem retomada sem nova execução paga. Se a criação ficar incerta, a
retomada para até receber explicitamente o ID da execução. A reserva de páginas
é conservadora e não equivale a uma cobrança real.

CSV e relatórios são regeneráveis a partir do SQLite. Exportações usam arquivo
temporário e `os.replace`; não há transação conjunta entre os três arquivos.
O banco é a referência em caso de interrupção. Não executar dois processos no
mesmo diretório; isolamento paralelo é por diretório de execução.

## Limites deliberados

Não há filtro de relevância, maturidade, geografia da empresa, atividade ou
propriedade. País e idioma configuram a busca, não uma triagem empresarial.
Não há resolução de subsidiárias, marcas ou identidade jurídica.
A evidência JSON-LD confirma coerência declarada, não verdade independente.
A política de fontes não elimina todos os possíveis intermediários desconhecidos.
URLs com autenticação, esquemas não HTTP(S), portas não usuais e endereços privados
são rejeitados. A verificação DNS não substitui isolamento de rede contra servidores
maliciosos ou DNS rebinding; use um ambiente sem acesso a infraestrutura sensível.

## Referências verificadas em 2026-09-24

- https://serpapi.com/search-api — parâmetros da busca Google.
- https://serpapi.com/pagination — metadados de próxima página e offsets.
- https://serpapi.com/organic-results — resultados orgânicos estruturados.

Paginação segue `start` informado por `serpapi_pagination`, sem presumir dez
resultados por página. Resposta ausente/malformada é falha, não zero resultados.
