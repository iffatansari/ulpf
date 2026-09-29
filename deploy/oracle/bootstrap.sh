#!/usr/bin/env bash
# Prepares an Oracle Always Free Ampere A1 instance (2 OCPU / 12 GB) to run the
# ULPF compose stack. Idempotent: safe to re-run.
#
#   sudo ./deploy/oracle/bootstrap.sh
#
# This script handles the OS. It cannot open the OCI cloud firewall -- ingress
# rules live in the VCN security list, which is configured in the web console.
# See DEPLOY_ORACLE.md.

set -euo pipefail

log() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }

TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
log "Host memory: ${TOTAL_MB} MB"

# ---------------------------------------------------------------------------
# 0. OCI's Ubuntu image is "Minimal" and ships without curl, gnupg or
#    ca-certificates, which the Docker install below needs. Install them first
#    or the apt repository step fails in a confusing way.
# ---------------------------------------------------------------------------
if ! command -v curl >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1; then
  log "Installing prerequisites"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg software-properties-common
fi

# ---------------------------------------------------------------------------
# 1. OpenSearch needs vm.max_map_count >= 262144 or its bootstrap check refuses
#    to start. This is the one thing Render cannot give you, because the sysctl
#    is not namespaced -- owning the host is the main reason to run here.
# ---------------------------------------------------------------------------
log "Setting vm.max_map_count=262144"
cat > /etc/sysctl.d/99-ulpf.conf <<'EOF'
vm.max_map_count = 262144
vm.swappiness = 1
EOF
sysctl --system >/dev/null
echo "vm.max_map_count = $(sysctl -n vm.max_map_count)"

# ---------------------------------------------------------------------------
# 2. Swap is not a substitute for memory limits, but it turns a sudden
#    allocation spike into a slow degradation instead of an OOM killer
#    terminating OpenSearch mid-write. 2 GB on the boot volume.
# ---------------------------------------------------------------------------
SWAP_FILE=/swapfile
if ! swapon --show | grep -q "$SWAP_FILE"; then
  log "Creating a 2 GB swap file"
  if [ ! -f "$SWAP_FILE" ]; then
    fallocate -l 2G "$SWAP_FILE" || dd if=/dev/zero of="$SWAP_FILE" bs=1M count=2048
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE" >/dev/null
  fi
  swapon "$SWAP_FILE"
  grep -q "^$SWAP_FILE" /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
fi
free -m

# ---------------------------------------------------------------------------
# 3. Docker from the official apt repository (not Ubuntu's docker.io, which
#    lags behind on compose v2 and buildx).
# ---------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list

  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
else
  log "Docker already installed"
fi

systemctl enable --now docker

if [ -n "${SUDO_USER:-}" ]; then
  log "Adding $SUDO_USER to the docker group"
  usermod -aG docker "$SUDO_USER"
  echo "Log out and back in for this to take effect."
fi

docker --version
docker compose version

# ---------------------------------------------------------------------------
# 4. Host firewall. The compose prod overlay already binds OpenSearch (9200),
#    Redpanda (9092) and the API (8000) to 127.0.0.1, so this is a second
#    layer rather than the only one.
# ---------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  log "Configuring ufw"
  ufw --force default deny incoming >/dev/null
  ufw --force default allow outgoing >/dev/null
  ufw allow 22/tcp   comment 'SSH'
  ufw allow 3000/tcp comment 'ULPF UI'
  # Ingestion. Drop these unless something is actually pushing to you.
  # ufw allow 8081/tcp  comment 'JSON ingest'
  # ufw allow 1514/udp  comment 'syslog UDP'
  # ufw allow 5151/tcp  comment 'syslog TCP'
  ufw --force enable >/dev/null
  ufw status verbose
else
  log "ufw not present -- relying on the OCI security list and compose loopback binds"
fi

log "Done"
cat <<'EOF'

Next:
  git clone <your fork> ~/ulpf
  cd ulpf
  docker compose --env-file deploy/oracle/env.oracle \
    -f docker-compose.yml -f docker-compose.prod.yml \
    -f docker-compose.oracle.yml up -d --build

EOF
