#!/usr/bin/env bash
# Copia para corpus/ os arquivos Markdown de relational-databases/ e t-sql/ do
# repositorio MicrosoftDocs/sql-docs, no commit usado no trabalho, com as
# licencas.
#
# Uso:
#   bash scripts/obter_corpus.sh                    baixa so esse commit, so com as duas pastas
#   bash scripts/obter_corpus.sh /caminho/sql-docs  usa um clone que ja existe, no mesmo commit
set -euo pipefail

COMMIT=928a038ddb0ec8229674c7d0dbe9e529cbd42024
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
DESTINO="$RAIZ/corpus"

if [ -e "$DESTINO" ]; then
    echo "ERRO: $DESTINO ja existe. Apague a pasta para obter o corpus de novo." >&2
    exit 1
fi

if [ $# -ge 1 ]; then
    CLONE="$1"
else
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    CLONE="$TMP/sql-docs"
    git init --quiet "$CLONE"
    git -C "$CLONE" remote add origin https://github.com/MicrosoftDocs/sql-docs.git
    git -C "$CLONE" sparse-checkout set docs/relational-databases docs/t-sql
    git -C "$CLONE" fetch --depth 1 --filter=blob:none origin "$COMMIT"
    git -C "$CLONE" checkout --quiet FETCH_HEAD
fi

ATUAL="$(git -C "$CLONE" rev-parse HEAD)"
if [ "$ATUAL" != "$COMMIT" ]; then
    echo "ERRO: o clone esta no commit $ATUAL. Rode: git -C \"$CLONE\" checkout $COMMIT" >&2
    exit 1
fi
if [ -n "$(git -C "$CLONE" status --porcelain -- docs/relational-databases docs/t-sql)" ]; then
    echo "ERRO: ha arquivos alterados em docs/relational-databases ou docs/t-sql no clone." >&2
    exit 1
fi

mkdir -p "$DESTINO"
(cd "$CLONE/docs" && find relational-databases t-sql -name '*.md' -print0 | tar --null -T - -cf -) \
    | (cd "$DESTINO" && tar -xf -)
cp "$CLONE/LICENSE" "$CLONE/LICENSE-CODE" "$DESTINO/"

TOTAL=$(find "$DESTINO" -name '*.md' | wc -l)
NO_GRAFO=$(find "$DESTINO" -name '*.md' -not -path '*/relational-databases/errors-events/includes/*' | wc -l)
echo "corpus/: $TOTAL arquivos .md, dos quais $NO_GRAFO entram no grafo"
if [ "$TOTAL" -ne 6276 ] || [ "$NO_GRAFO" -ne 6151 ]; then
    echo "ERRO: eram esperados 6276 arquivos, dos quais 6151 entram no grafo." >&2
    exit 1
fi
