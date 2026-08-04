#!/usr/bin/env bash
# ase-env.sh — lifecycle for the dockerized environment.
#
#   ./ase-env.sh start            dashboard only (build on first run)
#   ./ase-env.sh start --all      dashboard + both workload services
#   ./ase-env.sh stop             stop everything (state volumes survive)
#   ./ase-env.sh restart [--all]  stop + start
#   ./ase-env.sh status           service status + health
#   ./ase-env.sh logs [service]   follow logs (default: dashboard)
#   ./ase-env.sh demo             run the three governed scenarios in the container
#   ./ase-env.sh build            (re)build images
#   ./ase-env.sh clean            stop and DELETE state volumes (fresh slate)

set -euo pipefail
cd "$(dirname "$0")"

compose() { docker compose "$@"; }

profiles() {
  if [[ "${1:-}" == "--all" ]]; then
    echo "--profile workload"
  fi
}

case "${1:-help}" in
  start)
    # shellcheck disable=SC2046
    compose $(profiles "${2:-}") up -d --build
    echo
    echo "  dashboard     http://localhost:8787"
    if [[ "${2:-}" == "--all" ]]; then
      echo "  shortener-py  http://localhost:8000  (docs at /docs)"
      echo "  shortener-rs  http://localhost:8788"
    fi
    echo
    echo "  seed it with runs:  ./ase-env.sh demo"
    ;;
  stop)
    compose --profile workload --profile demo down
    ;;
  restart)
    "$0" stop
    "$0" start "${2:-}"
    ;;
  status)
    compose --profile workload ps
    ;;
  logs)
    compose logs -f "${2:-dashboard}"
    ;;
  demo)
    compose run --rm demo
    echo
    echo "  inspect the runs -> http://localhost:8787"
    ;;
  build)
    compose --profile workload --profile demo build
    ;;
  clean)
    compose --profile workload --profile demo down -v
    echo "state volumes removed — next start is a fresh slate"
    ;;
  *)
    sed -n '2,13p' "$0"
    ;;
esac
