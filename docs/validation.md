# Validação — versão 0.1.0

## Executado localmente

- 38 testes passaram (`python -m pytest -q`).
- Ruff: lint e formatação passaram.
- Instalação editável do pacote concluída com dependências já disponíveis.
- CLI exercitada de ponta a ponta com transporte simulado, incluindo exportação,
  duas queries e retomada sem novas chamadas.
- Testados: paginação variável, resultados repetidos, limite de páginas, contador
  de chamadas persistente, retries, autenticação inválida, respostas malformadas,
  interrupção entre busca e resolução, redirecionamentos, destinos privados,
  ambiguidade, divergência de identidade, fontes não institucionais, UTF-8 e
  escaping de delimitadores no CSV. O feedback visual foi exercitado com
  terminal simulado, saída redirecionada, retomada e `--no-progress`.

As entradas de teste são sintéticas e não comprovam qualidade em empresas reais.
O workflow incluído verifica Linux/Python 3.11 e Windows/Python 3.13; seu resultado
remoto deve ser consultado no GitHub, não inferido deste relatório local.

## Não executado

- Nenhuma consulta paga ou autenticada à SerpApi.
- Nenhum piloto com a lista real de queries do usuário.
- Não foram medidas precisão e cobertura de identificação em sites reais.
- Não foi feita comparação de custo/qualidade entre provedores.

## Próximo checkpoint operacional

Disponibilizar a chave `SERPAPI_API_KEY` no ambiente de execução e selecionar uma
amostra das queries reais. Iniciar com teto explícito, por exemplo, dez tentativas
de busca. Conferir os entrypoints exportados e as pendências. Contabilizar falsos
positivos e sites institucionais omitidos pela exigência de JSON-LD antes de decidir
se a política de identificação precisa ser ampliada.

O teto de chamadas não representa aprovação de orçamento monetário. Executar o
piloto somente com créditos/orçamento autorizados pelo usuário.
