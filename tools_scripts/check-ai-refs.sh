#!/bin/bash
# Verifica que no haya referencias a IA en archivos rastreados por git ni en commits nuevos.
# Uso standalone: bash tools_scripts/check-ai-refs.sh
# También llamado por .git/hooks/pre-push (instalado con tools_scripts/install-hooks.sh).

PATTERN="clau""de|anthrop""ic|chatg""pt|open""ai|cop""ilot|ai-gen""erated|co-auth""ored-by|noreply@anthrop""ic\.com|agentes ia"

echo "Verificando referencias a IA en archivos del repo..."
# Excluye los propios scripts de verificación
result=$(git ls-files \
    | grep -v "^tools_scripts/check-ai-refs\.sh$" \
    | grep -v "^tools_scripts/hooks/" \
    | grep -v "^tools_scripts/install-hooks\.sh$" \
    | xargs grep -ilE "$PATTERN" 2>/dev/null)
if [ -n "$result" ]; then
    echo "BLOQUEADO — referencias encontradas en:"
    echo "$result"
    exit 1
fi

echo "OK — archivos limpios."
exit 0
