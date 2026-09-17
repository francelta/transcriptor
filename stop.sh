#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  TranscriberStudio Pro — STOP
#  Detiene el backend Django, el frontend Vue y cualquier
#  túnel SSH abierto. Opcionalmente termina el pod RunPod.
# ══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS_FILE="$SCRIPT_DIR/.pids"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

echo ""
echo -e "${BOLD}${RED}🛑  TranscriberStudio Pro — Deteniendo...${RESET}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

STOPPED=0

# ── Leer PIDs guardados ────────────────────────────────────────
if [ -f "$PIDS_FILE" ]; then
  source "$PIDS_FILE"

  # Detener Backend Django
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null
    sleep 0.5
    kill -9 "$BACKEND_PID" 2>/dev/null || true
    echo -e "   ${GREEN}✅  Backend detenido  (PID $BACKEND_PID)${RESET}"
    STOPPED=1
  fi

  # Detener Frontend Vite
  if [ -n "$FRONTEND_PID" ] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null
    sleep 0.5
    kill -9 "$FRONTEND_PID" 2>/dev/null || true
    echo -e "   ${GREEN}✅  Frontend detenido (PID $FRONTEND_PID)${RESET}"
    STOPPED=1
  fi

  rm -f "$PIDS_FILE"
fi

# ── Matar procesos huérfanos por puerto / nombre ───────────────
# Backend Django (puerto 8002)
DJANGO_PIDS=$(lsof -ti tcp:8002 2>/dev/null)
if [ -n "$DJANGO_PIDS" ]; then
  echo "$DJANGO_PIDS" | xargs kill -9 2>/dev/null || true
  echo -e "   ${GREEN}✅  Proceso en :8002 eliminado (PID $DJANGO_PIDS)${RESET}"
  STOPPED=1
fi

# Frontend Vite (puerto 5174)
VITE_PIDS=$(lsof -ti tcp:5174 2>/dev/null)
if [ -n "$VITE_PIDS" ]; then
  echo "$VITE_PIDS" | xargs kill -9 2>/dev/null || true
  echo -e "   ${GREEN}✅  Proceso en :5173 eliminado (PID $VITE_PIDS)${RESET}"
  STOPPED=1
fi

# Proxy local SSHTunnel (puerto 8005)
PROXY_PIDS=$(lsof -ti tcp:8005 2>/dev/null)
if [ -n "$PROXY_PIDS" ]; then
  echo "$PROXY_PIDS" | xargs kill -9 2>/dev/null || true
  echo -e "   ${YELLOW}⚡  Túnel SSH local :8005 cerrado (PID $PROXY_PIDS)${RESET}"
  STOPPED=1
fi

if [ "$STOPPED" -eq 0 ]; then
  echo -e "   ${YELLOW}⚠️   No se encontraron procesos activos de TranscriberStudio.${RESET}"
fi

echo ""
echo -e "${GREEN}${BOLD}✅  Todos los servicios detenidos correctamente.${RESET}"
echo -e "${CYAN}   Para volver a arrancar: ${BOLD}./start.sh${RESET}"
echo ""
