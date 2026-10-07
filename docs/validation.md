# Validação — versão 0.2.0

Data: 07/10/2026. Esta versão conclui a migração Apify e introduz saída externa.

## Executado localmente

Ambiente: Windows/Python 3.14, pytest 8.4.2, Ruff 0.16.10 na venv criada.

- 94 testes passaram.
- Ruff lint e formatação aprovados.
- Instalação editável numa venv e pip check aprovados.
- Bootstrap PowerShell executou instalação, checks e ajuda da CLI.
- Bootstrap Bash com sintaxe verificada e fluxo completo aprovado no Git Bash
  do Windows. Bash no Linux é exercitado pelo CI.
- Testes de falha do bootstrap preservam venv inválida e propagam erro do Python.
- A matriz CI executa instalação limpa e repetida em caminhos com espaços,
  invocando os scripts de outra pasta: Linux/Python 3.11 e Windows/Python 3.13.

Os testes mantêm os casos anteriores de resolução, normalização, UTF-8, escaping,
progresso, interrupção e retomada. Os testes da integração remota removida foram
substituídos por contratos Apify.

## Novos cenários cobertos

- Apify pela CLI, retomada e troca de pasta de exportação sem outras buscas.
- Rechecagem de páginas Apify, SerpApi e banco misto sem token.
- Migração com preservação de páginas, decisões, IDs, reservas e contador antigo;
  configuração incompatível não efetua a migração; esquema futuro é rejeitado.
- Página única com dez resultados sem segundo Actor; zero resultados, página final
  curta, limite de profundidade, custo atingido e evidência incompleta.
- Dataset com lacunas, duplicatas ou dados inválidos conserva run recuperável e
  não importa páginas parcialmente.
- Autenticação sem retry; rede/429/5xx em GET; criação POST sem retry; dataset
  paginado; estados transitórios; interrupção do polling conserva o run ID.
- Destino externo inexistente e com espaços; pasta do pacote ou checkout rejeitada;
  falha de substituição preserva o CSV anterior e remove o temporário.

## Limites

Nenhuma consulta autenticada/paga Apify foi executada. Não há piloto de mercado,
medição de precisão/cobertura ou comparação de custo entre provedores.

Os metadados de fila/custo usados para provar término dependem do Actor. Quando
não permitem confirmar o encerramento, a ferramenta exporta os resultados úteis
com cobertura incompleta, sem criar outra execução paga. Esse comportamento
conservador precisa ser observado no piloto.

O estado remoto do CI deve ser conferido na execução vinculada ao commit/PR.
Os resultados locais não são prova de aprovação remota.

## Próximo checkpoint operacional

Configurar APIFY_API_TOKEN no ambiente, selecionar uma amostra de queries e
definir orçamento. Usar diretórios externos de estado e saída, limites pequenos
de páginas e teto por Actor. Conferir companies.csv, pendências, cobertura e
evidência do run, contando falsos positivos e empresas omitidas.

O teto por Actor não representa aprovação do orçamento total. Manter a amostra
e a avaliação humana antes de qualquer mudança na política de identificação.
