# Revisão das regras após o piloto de organoides — 08/10/2026

## Evidência e decisão

O pacote `audit_bundle.zip` fornecido pelo usuário (SHA-256
`4db155316df1c5283c22719770fdc56eeb2d0e2c233febe03e3365947f060a50`)
continha 67 linhas exportadas, 71 casos de
auditoria e contagens de 70 confirmações, 364 pendências e 419 descartes após
a correção offline. Não continha HTML histórico nem o banco SQLite.
Por isso, inspeções atuais de páginas não demonstram o conteúdo disponível
durante a coleta original nem permitem estimar a precisão total do piloto.

Padrões observados motivaram regressões sintéticas:

- FinalSpark e Creative Biolabs: marcação `Article` em produto/home própria.
- Beonchip: artigo técnico próprio e marca/legal name compatíveis.
- Cortical Labs, Molecular Devices e STEMCELL: identidade visível sem JSON-LD.
- Organoid Grid: `WebSite`/marca em conflito com `Organization` de agência.
- PubMed: fonte acadêmica conhecida anteriormente pendente por HTTP 203.
- Springer: URL de erro de cookies anteriormente exportada como entrypoint.
- Editoriais e diretórios: identidade da plataforma confundida com fornecedor.

A política anterior vetava `Article` e caminhos editoriais antes de verificar
a home. Agora separa o tipo da página do tipo da fonte e exige oferta própria.
A consequência esperada é recuperar alguns fornecedores omitidos e retirar
alguns intermediários confirmados. O efeito real em todas as decisões depende
de executar a reavaliação do banco no VPS e revisar os novos resultados.

## Contratos preservados

CSV de três colunas; normalização de URLs e proteção de destinos HTTP; esquema
SQLite 2; queries, páginas, IDs, configurações, reservas e progresso do piloto.
O comando novo trabalha numa cópia e não edita campos de decisão humana.
Não há busca indireta de empresas citadas em artigos, filtro de aderência,
validação jurídica, ranking de startups ou tratamento geral de HTTP 203 como sucesso.

## Critérios e limites

Fontes conhecidas são classificadas por host exato ou subdomínio, não por
sufixos `.org`/`.edu`. Fontes desconhecidas usam evidência da página. Oferta
própria é heurística: menus e chamadas explícitas de produtos/serviços podem
ser insuficientes ou enganosos. Uma oferta permite considerar institutos e
prestadores sem afirmar que sejam empresas privadas ou aderentes à demanda.

Identidade pode vir de metadata institucional ou de concordância de dois
contextos visíveis. `alternateName` explícito e sufixos jurídicos comuns permitem
comparações restritas. Não há fusão de marcas diferentes, aproximação por
substring, inferência de nomes por domínio ou escolha entre proprietários
conflitantes. Conflitos permanecem pendentes.

Cada resolução inspeciona até cinco URLs; About/contato é apoio limitado,
nunca crawling. Cache de 128 entradas e 8 MiB de conteúdo inclui falhas da sessão
sem guardar tracebacks (o HTML analisado ocupa memória adicional). Não há JavaScript,
bypass de bloqueios ou navegador autenticado. URLs com erro e interstitials
ficam pendentes. Links funcionais comuns e banners de cookies são preservados.

## Operação

Use `python -m keyword_searcher.recheck` com o `--run-dir` original e uma pasta
`--output-dir` externa e nova. O backup e a cópia ficam nessa pasta junto das
exportações e das diferenças. Não é necessário token Apify. O retorno 0 informa
reavaliação concluída, não ausência de pendências. O retorno 130 conserva saída
parcial, que pode ser reavaliada em outra pasta.

As decisões antigas permanecem recuperáveis no banco original e no backup.
A revisão humana deve conferir confirmações recuperadas, confirmações removidas
e motivos de pendência. Campos humanos da auditoria anterior permanecem sob
controle do usuário. A nova avaliação não coleta páginas de Google ausentes.
