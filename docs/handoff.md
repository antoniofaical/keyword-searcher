# Contrato de handoff — versão 1

O receptor é `antoniofaical/startup-theme-adherence-classifier-jev`, pacote
startup-theme-adherence 0.2.0. Contrato verificado em
[`dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b`](https://github.com/antoniofaical/startup-theme-adherence-classifier-jev/blob/dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b/src/startup_adherence/cli.py#L141-L199).
Somente keyword-searcher foi alterado para a integração.

## Arquivos

`handoff.json`: lista de objetos com `name` e `url`, ambos strings não vazias.
URLs são os entrypoints já resolvidos, com HTTP/HTTPS. UTF-8 sem BOM. Não é um
objeto com chave `sites`, um perfil de pontuação ou texto de evidência. O parser
real do receptor é a autoridade; não há schema de sites no snapshot verificado.

`handoff.metadata.json` contém:

| Campo | Significado |
| --- | --- |
| handoff_schema_version | Versão do manifesto, atualmente 1 |
| producer | Nome e versão do keyword-searcher |
| receiver | Repositório e commit do contrato de referência |
| counts.sites | Candidatos no handoff |
| counts.confirmed_resolutions | Ocorrências confirmadas no banco |
| counts.duplicates | Confirmadas menos candidatos; inclui repetições na mesma query |
| counts.pending_resolutions / skipped_resolutions | Ocorrências não enviadas |
| coverage.search_complete | Há queries e todas têm status complete |
| coverage.interrupted | A operação que chamou o exportador foi interrompida |
| coverage.partial | Interrupção, cobertura não complete ou resoluções pendentes |
| coverage.queries | Queries, status e motivos; sem progresso salvo usa unknown |
| sites | Lista de candidatos com rastreabilidade |

Cada item em `sites` tem `name`, `url`, `alternatives` e `sources`. Alternatives
guarda pares name/URL descartados pela deduplicação. Cada source tem `query`,
`discovered_url` e `resolution` com todos os campos salvos (`status`, `name`,
`url`, `reason`, `evidence`). Evidência conserva a string original; não há nova
interpretação de critérios, cópia de configuração com tokens ou execução de APIs.
Queries/URLs continuam sendo a chave existente; nenhum ID de caso de auditoria é
recriado ou campo humano preenchido. Não há timestamp variável no manifesto.

O manifesto reflete o snapshot do estado e a interrupção da chamada de exportação
atual. A exportação offline não reconstrói eventos passados de terminal nem
afirma que uma reavaliação de sites terminou só porque as queries de busca eram
completas; o resumo do recheck é o registro da operação anterior.

## Seleção determinística

Todas as resoluções são percorridas na ordenação `(query, discovered_url)` usada
pelo store. Somente confirmed entra. Nome exato ou URL equivalente conserva a
primeira ocorrência; todas as fontes ficam associadas ao item selecionado.
Aliases de nome seguem a seleção da primeira entrada, exatamente como
`select_sites`. Uma URL alternativa descartada por repetição do nome não vira
uma nova chave de equivalência de URL: isso evita divergência do receptor.

Equivalência usa host sem `www`, porta explícita e caminho normalizado sem barras
finais. Esquema HTTP/HTTPS, fragmentos, parâmetros de URL e query strings não
distinguem entradas. Caminhos e subdomínios diferentes distinguem. Não há regra
por domínio registrável, aproximação de marcas ou inferência por temática.

O receptor deriva nomes de pastas via NFKD/ASCII e normalização de caracteres,
com comparação case-insensitive. Colisões entre candidatos distintos são erro,
sem renomear ou escolher marca silenciosamente. Nomes/URLs inválidos também são
erro. Essa validação precede a escrita de qualquer output; o banco permanece
recuperável para revisão. Zero candidatos é `[]`, que exige não iniciar o
receptor (`No sites selected`).

## Limites e consumo

O arquivo é seleção de candidatos, não garantia de aderência, ausência de falsos
positivos ou cobertura temática completa. Mesma marca em URLs/subdomínios
diferentes conserva apenas a primeira. O crawler do receptor usa
include_subdomains=False por padrão: preservar alternativas não implica que
essas páginas serão visitadas. Revisão pode selecionar outra URL de entrada.

Perfil é outro JSON, passado por --profile. Seu contrato exige id/version,
perguntas/criteria e agregações; o gerador não inventa perfil a partir das queries.
Sem perfil explícito, o receptor usa digital_twin. Gerar o handoff não dispara
nenhuma chamada Jev/DeepL, crawl ou busca Apify. O CSV mantém suas três colunas
e deduplicação por URL/query. O report só recebe `handoff`, um objeto aditivo
com sites, duplicates, partial e nomes dos dois arquivos.

Escrita atômica por arquivo via temporário/replace, sem transação conjunta do
conjunto. Em erro de escrita, regere a saída a partir do banco salvo. Um processo
por destino. O audit ZIP inclui os dois JSONs e continua excluindo SQLite,
backups e segredos.

## Verificação

`tests/fixtures/adherence_contract.py` guarda as funções de entrada do receptor
do commit citado, sem código de crawl/classificação. `tests/test_handoff.py`
compara o builder com esse validador em execução offline e verifica variantes
de URL, caminhos/subdomínios, Unicode, aliases, colisões, preservação de fontes,
outputs vazios/parciais, interrupção/retomada e falha de escrita. Testes de
recheck e audit verificam integração, originais preservados e inclusão no ZIP.
Atualizar o snapshot do contrato exige revisar o comportamento de seleção,
não apenas trocar o SHA. Não há dependência de runtime no pacote do receptor.
