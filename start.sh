#!/bin/bash
# ══════════════════════════════════════════════════════════════
#  TranscriberStudio Pro — START
#  Lanza el backend Django (puerto 8002) y el frontend Vue (Vite)
# ══════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PIDS_FILE="$SCRIPT_DIR/.pids"
LOG_DIR="$SCRIPT_DIR/.logs"

# Colores
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

echo ""
echo -e "${BOLD}${CYAN}⚡  TranscriberStudio Pro — Iniciando...${RESET}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

# Crear directorio de logs
mkdir -p "$LOG_DIR"

# ── Comprobar si ya está corriendo ────────────────────────────
if [ -f "$PIDS_FILE" ]; then
  source "$PIDS_FILE"
  if kill -0 "$BACKEND_PID" 2>/dev/null || kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Ya hay una instancia en ejecución.${RESET}"
    echo -e "   Usa ${BOLD}./stop.sh${RESET} para detenerla primero."
    echo ""
    exit 1
  fi
  rm -f "$PIDS_FILE"
fi

# ── Backend Django ─────────────────────────────────────────────
echo -e "${CYAN}🐍  Iniciando backend Django en http://127.0.0.1:8002 ...${RESET}"

if [ ! -f "$BACKEND_DIR/venv/bin/python" ]; then
  echo -e "${RED}❌  No se encontró el entorno virtual en backend/venv/${RESET}"
  echo "   Crea el entorno con: python -m venv backend/venv && source backend/venv/bin/activate && pip install -r backend/requirements.txt"
  exit 1
fi

"$BACKEND_DIR/venv/bin/python" "$BACKEND_DIR/manage.py" migrate --run-syncdb 2>&1 | grep -E "Apply|OK|Migrat" || true

"$BACKEND_DIR/venv/bin/python" "$BACKEND_DIR/manage.py" runserver 127.0.0.1:8002 \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

# Esperar a que Django arranque (máx 8 s)
echo -n "   Esperando respuesta del backend"
for i in $(seq 1 16); do
  sleep 0.5
  if curl -s http://127.0.0.1:8002/api/worker/health/ > /dev/null 2>&1 || \
     curl -s http://127.0.0.1:8002/api/pod/status/ > /dev/null 2>&1; then
    break
  fi
  echo -n "."
done
echo ""

if kill -0 "$BACKEND_PID" 2>/dev/null; then
  echo -e "   ${GREEN}✅  Backend activo  →  PID $BACKEND_PID${RESET}"
else
  echo -e "   ${RED}❌  El backend falló al arrancar. Revisa: $LOG_DIR/backend.log${RESET}"
  exit 1
fi

# ── Frontend Vue (Vite) ────────────────────────────────────────
echo -e "${CYAN}🖼️   Iniciando frontend Vue (Vite) en http://localhost:5174 ...${RESET}"

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "   Instalando dependencias Node.js..."
  (cd "$FRONTEND_DIR" && npm install --silent)
fi

(cd "$FRONTEND_DIR" && npm run dev) \
  > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

# Esperar a que Vite arranque (máx 8 s)
echo -n "   Esperando respuesta del frontend"
for i in $(seq 1 16); do
  sleep 0.5
  if curl -s http://localhost:5174 > /dev/null 2>&1; then
    break
  fi
  echo -n "."
done
echo ""

if kill -0 "$FRONTEND_PID" 2>/dev/null; then
  echo -e "   ${GREEN}✅  Frontend activo →  PID $FRONTEND_PID${RESET}"
else
  echo -e "   ${RED}❌  El frontend falló al arrancar. Revisa: $LOG_DIR/frontend.log${RESET}"
  kill "$BACKEND_PID" 2>/dev/null
  exit 1
fi

# ── Guardar PIDs ───────────────────────────────────────────────
cat > "$PIDS_FILE" << PIDS
BACKEND_PID=$BACKEND_PID
FRONTEND_PID=$FRONTEND_PID
PIDS

# ── Resumen ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}🚀  TranscriberStudio Pro está listo${RESET}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "   🌐  App         →  ${BOLD}http://localhost:5174${RESET}"
echo -e "   🐍  Backend API →  ${BOLD}http://127.0.0.1:8002${RESET}"
echo -e "   📋  Logs        →  ${BOLD}$LOG_DIR/${RESET}"
echo -e "   🛑  Para parar  →  ${BOLD}./stop.sh${RESET}"
echo ""

# Abrir el navegador automáticamente (macOS)
if command -v open &>/dev/null; then
  sleep 1
  open http://localhost:5174
fi
