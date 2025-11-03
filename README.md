# GDPR_cookies_extractor

## Commands

poetry run main https://www.apple.com/

## Docker

sudo docker run -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com

## Docker with GPU

sudo docker run --gpus all -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com
