# Slim ComfyUI runtime. Model weights are not copied into this image.
# Jobs sync them from the private S3 bucket at start, which is cheaper to
# keep idle than a snapshot or a 30 GB image, and just as fast to read.
FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COMFY_ROOT=/opt/ComfyUI

RUN apt-get update \
    && apt-get install -y --no-install-recommends git wget curl unzip ca-certificates \
    && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscliv2.zip /var/lib/apt/lists/*

WORKDIR /opt/ComfyUI
RUN git clone --depth 1 --branch v0.45.0 https://github.com/comfyanonymous/ComfyUI.git . \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu128 torch torchvision \
    && git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git custom_nodes/ComfyUI_IPAdapter_plus \
    && git -C custom_nodes/ComfyUI_IPAdapter_plus checkout a0f451a5113cf9becb0847b92884cb10cbdec0ef \
    && if [ -f custom_nodes/ComfyUI_IPAdapter_plus/requirements.txt ]; then pip install --no-cache-dir -r custom_nodes/ComfyUI_IPAdapter_plus/requirements.txt || true; fi

COPY instance/download_models.py /opt/worker/download_models.py
COPY config/models.manifest.json /opt/worker/models.manifest.json
COPY worker/entrypoint.py /opt/worker/entrypoint.py

WORKDIR /opt/worker
CMD ["python", "/opt/worker/entrypoint.py"]
