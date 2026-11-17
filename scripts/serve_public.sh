#!/bin/sh
# Keep NavEasy public: the API serving the built web app on one port, shared through a Cloudflare tunnel.
# Restarts the API or the tunnel when either stops, replaces a tunnel that stops answering, keeps the Mac awake.
#
#   (cd frontend && VITE_API_BASE= npm run build)              # the web app, calling its own origin
#   nohup scripts/serve_public.sh > output/live/run.log 2>&1 &
#   cat output/live/public_url.txt                             # the address to share
#   kill $(cat output/live/supervisor.pid)                     # stop everything
#
# Your own domain: make a tunnel in the Cloudflare dashboard (Zero Trust > Networks > Tunnels) with the domain as its
# public hostname pointing at http://localhost:8080, and put TUNNEL_TOKEN=... and PUBLIC_URL=https://your.domain in
# .env. Without a token a quick tunnel gives a random https://*.trycloudflare.com address, which changes whenever the
# tunnel has to be replaced.
cd "$(dirname "$0")/.." || exit 1
PORT=${PORT:-8080}
MPORT=${MPORT:-20241}            # cloudflared's metrics: /ready answers 200 while the tunnel is connected
LIVE=output/live
mkdir -p "$LIVE"
echo $$ > "$LIVE/supervisor.pid"
[ -z "$TUNNEL_TOKEN" ] && TUNNEL_TOKEN=$(grep -E '^TUNNEL_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
[ -z "$PUBLIC_URL" ] && PUBLIC_URL=$(grep -E '^PUBLIC_URL=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

api_pid=; tun_pid=; caf_pid=
cleanup() { kill $api_pid $tun_pid $caf_pid 2>/dev/null; rm -f "$LIVE/supervisor.pid"; exit 0; }
trap cleanup INT TERM

caffeinate -dimsu -w $$ &        # no sleep while this runs (on battery the lid must stay open)
caf_pid=$!

start_api() {
  .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" >> "$LIVE/api.log" 2>&1 &
  api_pid=$!
}
start_tunnel() {
  : > "$LIVE/tunnel.log"
  if [ -n "$TUNNEL_TOKEN" ]; then
    cloudflared tunnel --no-autoupdate --metrics "127.0.0.1:$MPORT" run --token "$TUNNEL_TOKEN" >> "$LIVE/tunnel.log" 2>&1 &
  else
    cloudflared tunnel --no-autoupdate --metrics "127.0.0.1:$MPORT" --url "http://localhost:$PORT" >> "$LIVE/tunnel.log" 2>&1 &
  fi
  tun_pid=$!
}

start_api
start_tunnel
fails=0
while true; do
  sleep 20
  if ! kill -0 "$api_pid" 2>/dev/null; then echo "$(date) API stopped: restarting"; start_api; fi
  if ! kill -0 "$tun_pid" 2>/dev/null; then echo "$(date) tunnel stopped: restarting"; start_tunnel; fails=0; fi
  url=${PUBLIC_URL:-$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LIVE/tunnel.log" | head -1)}
  [ -n "$url" ] && echo "$url" > "$LIVE/public_url.txt"
  if [ "$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://127.0.0.1:$MPORT/ready")" = 200 ]; then
    fails=0
  else
    fails=$((fails + 1))
  fi
  if [ "$fails" -ge 6 ]; then                     # 2 minutes without a connection (a quick tunnel can expire)
    echo "$(date) tunnel not connected for 2 minutes: starting a new one"
    kill "$tun_pid" 2>/dev/null
    sleep 2
    start_tunnel
    fails=0
  fi
done
