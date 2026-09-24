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
| `http.py` | HTTP com timeout, tamanho e redirecionamentos limitados |
| `urls.py` | Normalização e rejeição de destinos privados conhecidos via DNS |
| `resolve.py` | Inspeção limitada de identidade e seleção do entrypoint |
| `store.py` | SQLite, reserva de chamadas e exportação atômica por arquivo |
| `pipeline.py` | Iteração e retomada, sem dependência de um fornecedor concreto |

O transporte e as dependências são injetados; não é necessário consultar a rede
para testar contratos e falhas. Trocar o provedor requer um novo adaptador e sua
seleção na composição da CLI. A primeira versão tem execução sequencial: uma
requisição simultânea. Não foram introduzidos serviços, filas ou microserviços.

## Persistência

Cada diretório representa uma execução e fixa sua configuração. A chave local de
uma página é `(query, start)` dentro desse contexto, que inclui fornecedor, idioma,
país, localização e versão. As respostas do provedor são preservadas com a chave
substituída por `[REDACTED]`. A resolução armazena nome, URL, motivo e evidência
extraída; HTML completo não é arquivado. Revalidar uma identidade exige revisitar
o site. A busca é salva antes da resolução; cada resolução é salva individualmente.

Chamadas são reservadas numa transação antes do envio. Esse contador é um teto
operacional de tentativas, não uma estimativa monetária. É possível que conte uma
tentativa que não chegou ao provedor. Não há promessa de exactly-once na rede.

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
