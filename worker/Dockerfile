# Slim ComfyUI worker for RunPod serverless.
# Model weights are not copied into this image. Mount a network volume at
# /runpod-volume and seed it once with seed_weights.py.
FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    COMFY_ROOT=/opt/ComfyUI \
    RUNPOD_VOLUME_PATH=/runpod-volume

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
    && pip install --no-cache-dir 'runpod>=1.7,<2' \
    && git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git custom_nodes/ComfyUI_IPAdapter_plus \
    && git -C custom_nodes/ComfyUI_IPAdapter_plus checkout a0f451a5113cf9becb0847b92884cb10cbdec0ef \
    && if [ -f custom_nodes/ComfyUI_IPAdapter_plus/requirements.txt ]; then pip install --no-cache-dir -r custom_nodes/ComfyUI_IPAdapter_plus/requirements.txt || true; fi

COPY instance/download_models.py /opt/worker/download_models.py
COPY config/models.manifest.json /opt/worker/models.manifest.json
COPY worker/handler.py /opt/worker/handler.py
COPY worker/seed_weights.py /opt/worker/seed_weights.py

WORKDIR /opt/worker
CMD ["python", "-u", "/opt/worker/handler.py"]
