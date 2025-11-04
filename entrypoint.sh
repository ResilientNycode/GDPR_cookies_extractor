#!/bin/sh

echo "Starting Ollama server..."
ollama serve > /app/logs/ollama.log 2>&1 &

sleep 5

echo "Ollama server started. Running the main command..."
exec "$@"