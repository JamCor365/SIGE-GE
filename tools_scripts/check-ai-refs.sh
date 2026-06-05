#!/bin/bash
# Verifica que no haya referencias a IA en archivos rastreados por git ni en commits nuevos.
# Uso standalone: bash tools_scripts/check-ai-refs.sh
# También llamado por .git/hooks/pre-push (instalado con tools_scripts/install-hooks.sh).

PATTERN="claude|anthropic|chatgpt|openai|copilot|ai-generated|co-authored-by|noreply@anthropic\.com|agentes ia"

echo "Verificando referencias a IA en archivos del repo..."
result=$(git ls-files | xargs grep -ilE "$PATTERN" 2>/dev/null)
if [ -n "$result" ]; then
    echo "BLOQUEADO — referencias encontradas en:"
    echo "$result"
    exit 1
fi

echo "OK — archivos limpios."
exit 0
