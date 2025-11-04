# GDPR_cookies_extractor

## Setup

## Local

poetry run main https://www.apple.com/

## Docker

### Enable current user to run Docker (avoiding root permissions)

sudo usermod -aG docker $USER

### Login Github registry

docker login ghcr.io

### Setup output dirs

mkdir -p gdpr_extractor/output
mkdir -p gdpr_extractor/logs
cd gdpr_extractor

### Run reading from file (sites.csv)

docker run -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main

### Run with GPU reading from file (sites.csv)

docker run --gpus all -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main

### Run for a specific site

docker run -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com

### Run with GPU for a specific site

docker run --gpus all -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com
