#!/bin/sh

# 1. Avvia il server Ollama in background
echo "Starting Ollama server..."
ollama serve > /app/logs/ollama.log 2>&1 &

# Diamo 5 secondi al server per avviarsi
sleep 5

# 2. Esegui il comando principale (IL PULL È STATO RIMOSSO)
echo "Ollama server started. Running the main command..."
exec "$@"