# Fluxo de contribuição

Crie branches de trabalho a partir da `origin/main` atualizada e abra PRs contra
`main`. Evite encadear PRs: mesclar uma mudança na branch de outro PR não entrega
essa mudança à branch principal.

Antes de concluir uma implementação autorizada:

1. Revise o diff completo contra `main` e execute o bootstrap/checks aplicáveis.
2. Aguarde os checks do GitHub no commit atual do PR. Corrija falhas antes do merge.
3. Use **Squash and merge**, conforme a preferência do mantenedor; conclua o PR
   deste trabalho quando as verificações permitirem. Não contorne proteções nem
   mescle trabalho de terceiros para simplesmente zerar a lista de PRs.
4. Confira a atualização de `main`, a versão e o conteúdo entregue. Um PR marcado
   como `merged` comprova integração apenas na sua branch de destino.

Para auditorias, registre problemas reproduzidos com arquivo, linha, impacto e
recomendação. Distinga achados ainda abertos de correções realizadas. Não execute
buscas pagas nem altere dados operacionais para validar erros de software.

Validações locais usuais, dentro da venv:

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m pip check
git diff --check
```

Os scripts `bootstrap.sh` e `bootstrap.ps1` também verificam instalação e ajuda da
CLI, sem executar buscas. Use transporte e HTML sintéticos para reproduções.
