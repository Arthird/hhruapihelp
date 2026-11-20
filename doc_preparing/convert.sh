mkdir -p output

# Очищаем output
find output -maxdepth 1 -type f -delete

# Конвертируем YAML в Markdown
for item in input/*.yml; do
    [ -f "$item" ] || continue

    filename=$(basename "$item" .yml)

    npx widdershins "$item" -o "output/$filename.md"
done
