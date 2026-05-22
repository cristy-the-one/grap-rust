FROM python:3.11-slim

# Install build dependencies required for Rust + PyO3
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Install Python dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the template
COPY . .

# Build the pygrap Python bindings (this is the slow step)
RUN maturin develop --release

# Default command runs the query interface
CMD ["python", "query.py"]
