# # STEP 1: USA L'IMMAGINE UFFICIALE DI OLLAMA COME BASE
# # La chiamiamo 'ollama_builder'
# FROM ollama/ollama:latest AS ollama_builder

# # STEP 2: LA TUA IMMAGINE PYTHON
# FROM python:3.12-slim

# # Copia il "motore" di Ollama dallo stage precedente
# # Questo è velocissimo e non richiede download!
# COPY --from=ollama_builder /bin/ollama /usr/local/bin/ollama

# # ---------- TUTTO IL RESTO È COME PRIMA ----------

# # 2. INSTALLA POETRY E PLAYWRIGHT
# # (Abbiamo bisogno di 'curl' solo per l'entrypoint, ma non per installare ollama)
# RUN apt-get update && apt-get install -y curl && \
#     rm -rf /var/lib/apt/lists/*
    
# RUN pip install poetry
# RUN poetry config virtualenvs.create false
# WORKDIR /app
# ENV PYTHONPATH=/app/src
# COPY pyproject.toml poetry.lock* ./
# RUN poetry install --only main --no-root && \
#     poetry run playwright install --with-deps

# # 3. COPIA IL CODICE
# COPY ./src ./src
# COPY config.json .
# COPY sites.csv .

# # 4. PREPARA L'AMBIENTE
# RUN mkdir -p /app/output /app/logs
# COPY entrypoint.sh /entrypoint.sh
# RUN chmod +x /entrypoint.sh

# # 5. ESECUZIONE
# ENTRYPOINT ["/entrypoint.sh"]
# CMD ["poetry", "run", "main"]


# STEP 1: USA L'IMMAGINE UFFICIALE DI OLLAMA COME BASE
FROM ollama/ollama:latest AS ollama_builder

# STEP 2: LA TUA IMMAGINE PYTHON
FROM python:3.12-slim

# Copia il "motore" di Ollama dallo stage precedente
COPY --from=ollama_builder /bin/ollama /usr/local/bin/ollama

# ---------- SETUP ----------
RUN apt-get update && apt-get install -y curl && \
    rm -rf /var/lib/apt/lists/*
    
RUN pip install poetry
RUN poetry config virtualenvs.create false
WORKDIR /app

# ----- FIX PER PYTHON (aggiungi questa riga) -----
ENV PYTHONPATH=/app/src

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root && \
    poetry run playwright install --with-deps

# 3. COPIA IL CODICE (rimettilo com'era)
COPY ./src ./src
COPY config.json .
COPY sites.csv .

# 4. SCARICA E "CUOCI" IL MODELLO
RUN (ollama serve > /dev/null 2>&1 &) ; sleep 5 ; echo "--- Pulling llama3 model... ---" ; ollama pull llama3 ; pkill ollama

# 5. PREPARA L'AMBIENTE
RUN mkdir -p /app/output /app/logs
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 6. ESECUZIONE
ENTRYPOINT ["/entrypoint.sh"]
CMD ["poetry", "run", "main"]