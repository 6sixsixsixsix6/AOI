FROM nvidia/cuda:12.4.1-base-ubuntu22.04

RUN apt-get update && \
    apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        python-is-python3 \
        ca-certificates \
        curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["bash"]