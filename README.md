# GDPR_cookies_extractor

## Local

poetry run main https://www.apple.com/

## Docker

### Enable current user to run Docker (avoiding root permissions)

sudo usermod -aG docker $USER

### Run

docker run -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com

### Run with GPU

docker run --gpus all -it \
 -v ./output:/app/output \
 -v ./logs:/app/logs \
 gdpr_extractor \
 poetry run main microsoft.com
