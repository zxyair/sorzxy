#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv"
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  VENV_DIR="$ROOT_DIR/venv"
fi
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "venv not found at $ROOT_DIR/.venv or $ROOT_DIR/venv"
  exit 1
fi

source "$VENV_DIR/bin/activate"

ETCD_ENDPOINT="${ETCD_ENDPOINT:-http://127.0.0.1:2379}"
TARGET_SAR="${TARGET_SAR:-8.8.8.8}"
SAR_IP="${SAR_IP:-8.8.8.8}"
SERVICE_PORT="${SERVICE_PORT:-53}"
RULE_TTL_S="${RULE_TTL_S:-30}"
MAX_VOICE_DELAY_MS="${MAX_VOICE_DELAY_MS:-300}"
MAX_LOSS_RATE="${MAX_LOSS_RATE:-0.5}"
MAX_JITTER_MS="${MAX_JITTER_MS:-100}"
DS_PORT="${DS_PORT:-50052}"
DS_ADDR="127.0.0.1:${DS_PORT}"

mkdir -p .smoke

cleanup() {
  for f in .smoke/*.pid; do
    [[ -f "$f" ]] || continue
    pid="$(cat "$f" || true)"
    if [[ -n "${pid:-}" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT

echo "[smoke] clearing etcd prefix /network/"
etcdctl --endpoints="$ETCD_ENDPOINT" del --prefix /network/ >/dev/null || true

echo "[smoke] starting DS"
(
  export RULE_TTL_S MAX_VOICE_DELAY_MS
  export MAX_LOSS_RATE MAX_JITTER_MS
  export DS_GRPC_BIND="[::]:${DS_PORT}"
  python -u ds_server.py
) 2>&1 | while IFS= read -r line; do printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"; done >> .smoke/ds.log &
echo $! > .smoke/ds.pid

echo "[smoke] starting SAR register (for target: $SAR_IP:$SERVICE_PORT)"
(
  export SAR_IP SERVICE_PORT
  python -u sar_register.py
) 2>&1 | while IFS= read -r line; do printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"; done >> .smoke/sar.log &
echo $! > .smoke/sar.pid

echo "[smoke] starting one SOR agent"
python -u agent.py 2>&1 | while IFS= read -r line; do printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"; done >> .smoke/agent.log &
echo $! > .smoke/agent.pid

echo "[smoke] waiting for watchers/telemetry..."
sleep 6

echo "[smoke] verifying status schema contains bw/jitter/loss (polling up to 30s)"
python - <<PY
import json, time
import etcd3
etcd = etcd3.client(host="127.0.0.1", port=2379)

deadline = time.time() + 30
while time.time() < deadline:
    found = False
    for value, meta in etcd.get_prefix("/network/status/"):
        data = json.loads(value.decode("utf-8"))
        links = data.get("links", {}) or {}
        for nb, info in links.items():
            if not isinstance(info, dict):
                continue
            if "bw" in info and "jitter" in info and "loss" in info:
                found = True
                break
        if found:
            break
    if found:
        print("[smoke] OK: bw/jitter/loss present in status links")
        raise SystemExit(0)
    time.sleep(2)

raise SystemExit("[smoke] FAIL: bw/jitter/loss fields did not appear in time")
PY

echo "[smoke] waiting until at least one link to target is UP (polling up to 40s)"
python - <<PY
import json, time
import etcd3
etcd = etcd3.client(host="127.0.0.1", port=2379)
target = "$TARGET_SAR"
deadline = time.time() + 40
while time.time() < deadline:
    for value, meta in etcd.get_prefix("/network/status/"):
        data = json.loads(value.decode("utf-8"))
        links = data.get("links", {}) or {}
        info = links.get(target)
        if isinstance(info, dict) and info.get("status") == "UP":
            print("[smoke] OK: target link is UP")
            raise SystemExit(0)
    time.sleep(2)
raise SystemExit("[smoke] FAIL: target link did not become UP in time")
PY

echo "[smoke] sending RequestTunnel to DS (target: $TARGET_SAR)"
python - <<PY
import grpc
import control_pb2, control_pb2_grpc
ch = grpc.insecure_channel("${DS_ADDR}")
stub = control_pb2_grpc.DirectoryServerStub(ch)
resp = stub.RequestTunnel(control_pb2.TunnelReq(target_sar_ip="$TARGET_SAR", req_bandwidth=1))
print(resp)
PY

echo "[smoke] checking rules were written"
RULES="$(etcdctl --endpoints="$ETCD_ENDPOINT" get --prefix /network/rules/ || true)"
if [[ -z "$RULES" ]]; then
  echo "[smoke] FAIL: no /network/rules/* keys found"
  exit 2
fi

echo "[smoke] OK: rules present"
echo "[smoke] (optional) wait TTL $RULE_TTL_S seconds to observe auto-delete"

echo "[smoke] negative test: req_bandwidth=100000 should reject"
(
  python - <<PY
import grpc
import control_pb2, control_pb2_grpc
ch = grpc.insecure_channel("${DS_ADDR}")
stub = control_pb2_grpc.DirectoryServerStub(ch)
resp = stub.RequestTunnel(control_pb2.TunnelReq(target_sar_ip="$TARGET_SAR", req_bandwidth=100000))
print(resp)
if resp.success:
    raise SystemExit("[smoke] FAIL: expected rejection under req_bandwidth=100000 but got success")
print("[smoke] OK: rejection under strict req_bandwidth")
PY
)

