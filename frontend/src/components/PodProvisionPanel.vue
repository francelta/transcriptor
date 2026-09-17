<template>
  <!-- ═══════════════════════════════════════════════════════════════
       PodProvisionPanel — Aprovisionamiento Automático RunPod 1-clic
       ═══════════════════════════════════════════════════════════════ -->
  <Teleport to="body">
    <transition name="panel-fade">
      <div v-if="show" class="pp-backdrop" @click.self="tryClose">
        <div class="pp-modal" role="dialog" aria-label="Panel de Aprovisionamiento GPU">

          <!-- HEADER -->
          <div class="pp-header">
            <div class="pp-title-row">
              <div class="pp-gpu-icon">⚡</div>
              <div>
                <h2 class="pp-title">GPU On-Demand</h2>
                <p class="pp-subtitle">Aprovisionamiento automático RunPod · 1 clic</p>
              </div>
            </div>
            <button class="pp-close" @click="tryClose" :disabled="isProvisioning" aria-label="Cerrar">✕</button>
          </div>

          <!-- ESTADO IDLE: Formulario de inicio -->
          <div v-if="provisionStatus === 'idle'" class="pp-body">
            <div class="pp-info-card">
              <div class="pp-info-icon">ℹ️</div>
              <div>
                <p class="pp-info-text">
                  El sistema buscará la GPU más económica disponible
                  (<strong>&lt; 0.40 $/h</strong>) en RunPod, creará el pod,
                  instalará las dependencias de ML y lo dejará listo para transcribir.
                </p>
                <p class="pp-info-text" style="margin-top:6px; font-size: 0.78rem; opacity:0.7;">
                  Prioridad: RTX 2000 Ada → RTX 3080 → RTX 3090 → RTX 4000 → L4
                </p>
              </div>
            </div>

            <div class="pp-field-group" v-if="!hasApiKey">
              <label class="pp-label">RunPod API Key <span class="pp-req">*</span></label>
              <input
                v-model="localApiKey"
                type="password"
                class="pp-input"
                placeholder="rp_xxxxxxxxxxxxxxxxxxxx"
                autocomplete="off"
              />
              <p class="pp-field-hint">Obtenla en RunPod → Settings → API Keys</p>
            </div>

            <div class="pp-field-row" v-if="hasApiKey">
              <div class="pp-check-badge">✅ API Key configurada</div>
            </div>

            <div class="pp-ssh-note">
              <div class="pp-ssh-icon">🔑</div>
              <p>Asegúrate de que tu clave pública <code>~/.ssh/id_rsa.pub</code>
              está añadida en <strong>RunPod → Settings → SSH Public Keys</strong>.</p>
            </div>

            <button
              class="pp-btn-provision"
              @click="startProvision"
              :disabled="!canStart"
              id="btn-provision-gpu"
            >
              <span class="pp-btn-icon">🚀</span>
              Aprovisionar GPU Automáticamente
            </button>

            <div v-if="configuredPodId" class="pp-terminate-idle">
              <button class="pp-btn-terminate" @click="terminatePod" id="btn-terminate-idle">
                🗑️ Eliminar pod activo ({{ configuredPodId }})
              </button>
            </div>
          </div>

          <!-- ESTADO PROVISIONING / CONNECTING: Stepper en tiempo real -->
          <div v-if="isProvisioning" class="pp-body">
            <div class="pp-stepper">
              <div
                v-for="step in STEPS"
                :key="step.key"
                class="pp-step"
                :class="getStepClass(step.key)"
              >
                <div class="pp-step-indicator">
                  <span v-if="getStepClass(step.key) === 'done'" class="pp-step-check">✓</span>
                  <span v-else-if="getStepClass(step.key) === 'active'" class="pp-step-spinner">⟳</span>
                  <span v-else class="pp-step-num">{{ step.num }}</span>
                </div>
                <div class="pp-step-info">
                  <p class="pp-step-label">{{ step.label }}</p>
                  <p v-if="getStepClass(step.key) === 'active' && lastMessageByStep[step.key]" class="pp-step-msg">
                    {{ lastMessageByStep[step.key] }}
                  </p>
                </div>
              </div>
            </div>

            <div class="pp-log-container" ref="logContainer">
              <div
                v-for="(log, idx) in logs"
                :key="idx"
                class="pp-log-entry"
                :class="'log-' + log.level"
              >
                <span class="pp-log-time">{{ log.time }}</span>
                <span class="pp-log-msg">{{ log.message }}</span>
              </div>
            </div>
          </div>

          <!-- ESTADO READY: GPU lista -->
          <div v-if="provisionStatus === 'ready'" class="pp-body">
            <div class="pp-success-banner">
              <div class="pp-success-glow"></div>
              <div class="pp-success-content">
                <div class="pp-success-icon">🎉</div>
                <h3 class="pp-success-title">¡GPU lista para transcribir!</h3>
                <p v-if="gpuInfo.displayName" class="pp-success-gpu">
                  {{ gpuInfo.displayName }}
                  <span class="pp-success-price">@ {{ gpuInfo.price?.toFixed(3) }} $/h</span>
                  · {{ gpuInfo.memoryInGb }} GB VRAM
                </p>
                <p class="pp-success-pod">Pod ID: <code>{{ activePodId }}</code></p>
              </div>
            </div>

            <div class="pp-ready-actions">
              <button class="pp-btn-close-ready" @click="$emit('close')" id="btn-close-ready">
                ✅ Usar GPU para Transcribir
              </button>
              <button class="pp-btn-terminate" @click="terminatePod" id="btn-terminate-ready">
                🗑️ Terminar Pod (congelar facturación)
              </button>
            </div>
          </div>

          <!-- ESTADO ERROR -->
          <div v-if="provisionStatus === 'error'" class="pp-body">
            <div class="pp-error-banner">
              <div class="pp-error-icon">⚠️</div>
              <div>
                <h3 class="pp-error-title">Error en el aprovisionamiento</h3>
                <p class="pp-error-last">{{ lastErrorMessage }}</p>
              </div>
            </div>

            <div class="pp-log-container pp-log-error-mode" ref="errorLogContainer">
              <div
                v-for="(log, idx) in logs"
                :key="idx"
                class="pp-log-entry"
                :class="'log-' + log.level"
              >
                <span class="pp-log-time">{{ log.time }}</span>
                <span class="pp-log-msg">{{ log.message }}</span>
              </div>
            </div>

            <div class="pp-error-actions">
              <button class="pp-btn-retry" @click="retryProvision" id="btn-retry-provision">
                🔄 Reintentar aprovisionamiento
              </button>
              <button class="pp-btn-terminate" @click="terminatePod" v-if="activePodId" id="btn-terminate-error">
                🗑️ Limpiar pod fallido ({{ activePodId }})
              </button>
            </div>
          </div>

        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'

// ──────────────────────────────────────────────────────────────
// Props & Emits
// ──────────────────────────────────────────────────────────────
const props = defineProps({
  show: { type: Boolean, default: false },
  hasApiKey: { type: Boolean, default: false },
  configuredPodId: { type: String, default: '' },
  initialStatus: { type: String, default: 'idle' },
  apiBase: { type: String, default: 'http://127.0.0.1:8002' },
})

const emit = defineEmits(['close', 'provisioned', 'terminated'])

// ──────────────────────────────────────────────────────────────
// Definición de pasos del stepper
// ──────────────────────────────────────────────────────────────
const STEPS = [
  { num: 1, key: 'searching_gpu',    label: 'Buscar GPU económica (<0.40 $/h)' },
  { num: 2, key: 'creating_pod',     label: 'Crear Pod On-Demand en RunPod' },
  { num: 3, key: 'waiting_ip',       label: 'Esperar IP pública (polling)' },
  { num: 4, key: 'ssh_connect',      label: 'Conexión SSH al pod' },
  { num: 5, key: 'installing_deps',  label: 'Instalar dependencias ML' },
  { num: 6, key: 'uploading_script', label: 'Subir script de inferencia' },
  { num: 7, key: 'starting_worker',  label: 'Iniciar worker GPU (tmux)' },
  { num: 8, key: 'opening_tunnel',   label: 'Abrir túnel SSH seguro' },
  { num: 9, key: 'healthcheck',      label: 'Verificar respuesta del worker' },
  { num: 10, key: 'ready',           label: 'GPU lista ✅' },
]

// ──────────────────────────────────────────────────────────────
// Estado reactivo
// ──────────────────────────────────────────────────────────────
const provisionStatus = ref(props.initialStatus)
const logs = ref([])
const completedSteps = ref(new Set())
const activeStep = ref('')
const gpuInfo = ref({})
const activePodId = ref(props.configuredPodId || '')
const localApiKey = ref('')
const lastMessageByStep = ref({})
const logContainer = ref(null)

let sseSource = null

// ──────────────────────────────────────────────────────────────
// Computadas
// ──────────────────────────────────────────────────────────────
const isProvisioning = computed(() =>
  ['provisioning', 'connecting'].includes(provisionStatus.value)
)

const canStart = computed(() =>
  props.hasApiKey || localApiKey.value.trim().length > 10
)

const lastErrorMessage = computed(() => {
  const errorLogs = logs.value.filter(l => l.level === 'error')
  return errorLogs.length ? errorLogs[errorLogs.length - 1].message : 'Se produjo un error inesperado.'
})

// ──────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────
function getStepClass(stepKey) {
  if (completedSteps.value.has(stepKey)) return 'done'
  if (activeStep.value === stepKey) return 'active'
  return 'pending'
}

function scrollLogsToBottom() {
  nextTick(() => {
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  })
}

function computeActiveStep(allLogs) {
  // El último log de cualquier step "info" que no haya completado determina el step activo
  const doneSteps = new Set()
  let lastInfoStep = ''

  for (const log of allLogs) {
    if (log.level === 'success') {
      doneSteps.add(log.step)
    } else if (log.level === 'info') {
      lastInfoStep = log.step
    }
  }
  return { doneSteps, activeStep: lastInfoStep }
}

// ──────────────────────────────────────────────────────────────
// SSE: Suscripción al stream de eventos de aprovisionamiento
// ──────────────────────────────────────────────────────────────
function connectSSE() {
  if (sseSource) {
    sseSource.close()
    sseSource = null
  }

  sseSource = new EventSource(`${props.apiBase}/api/pod/provision/events/`)

  sseSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)

      if (data.type === 'provision_log') {
        const entry = {
          step: data.step,
          message: data.message,
          level: data.level,
          time: data.time,
        }
        logs.value.push(entry)
        lastMessageByStep.value[data.step] = data.message

        const { doneSteps, activeStep: as } = computeActiveStep(logs.value)
        completedSteps.value = doneSteps
        activeStep.value = as

        provisionStatus.value = data.provision_status
        if (data.gpu_info && data.gpu_info.displayName) {
          gpuInfo.value = data.gpu_info
        }

        scrollLogsToBottom()
      }

      if (data.type === 'provision_complete') {
        provisionStatus.value = data.provision_status
        if (data.provisioned_pod_id) activePodId.value = data.provisioned_pod_id
        if (data.gpu_info && data.gpu_info.displayName) gpuInfo.value = data.gpu_info

        if (data.provision_status === 'ready') {
          // Marcar todos los steps como completados
          completedSteps.value = new Set(STEPS.map(s => s.key))
          activeStep.value = ''
          emit('provisioned', {
            podHost: data.pod_host,
            podSshPort: data.pod_ssh_port,
            podId: data.provisioned_pod_id,
            gpuInfo: data.gpu_info,
          })
        }
        sseSource.close()
        sseSource = null
      }
    } catch (err) {
      console.error('[PodProvisionPanel] SSE parse error:', err)
    }
  }

  sseSource.onerror = () => {
    console.warn('[PodProvisionPanel] SSE connection lost.')
    if (sseSource) {
      sseSource.close()
      sseSource = null
    }
  }
}

// ──────────────────────────────────────────────────────────────
// Acciones
// ──────────────────────────────────────────────────────────────
async function startProvision() {
  logs.value = []
  completedSteps.value = new Set()
  activeStep.value = 'searching_gpu'
  provisionStatus.value = 'provisioning'

  const body = {}
  if (localApiKey.value.trim()) {
    body.runpod_api_key = localApiKey.value.trim()
  }

  try {
    const resp = await fetch(`${props.apiBase}/api/pod/provision/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    if (!resp.ok) {
      let errMsg = `Error del servidor (HTTP ${resp.status})`
      try {
        const err = await resp.json()
        errMsg = err.error || errMsg
      } catch (_) {
        // Si la respuesta no es JSON (ej. 500 HTML de Django)
      }
      throw new Error(errMsg)
    }

    connectSSE()
  } catch (err) {
    provisionStatus.value = 'error'
    logs.value.push({
      step: 'error',
      message: `❌ Error iniciando aprovisionamiento: ${err.message}`,
      level: 'error',
      time: new Date().toLocaleTimeString(),
    })
  }
}

async function retryProvision() {
  provisionStatus.value = 'idle'
  logs.value = []
  completedSteps.value = new Set()
  activeStep.value = ''
  await nextTick()
  await startProvision()
}

async function terminatePod() {
  const podId = activePodId.value || props.configuredPodId
  if (!podId) return

  if (!confirm(`¿Eliminar definitivamente el pod ${podId}? Esto CONGELARÁ la facturación de GPU.`)) return

  try {
    const resp = await fetch(`${props.apiBase}/api/pod/terminate/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pod_id: podId }),
    })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`)

    activePodId.value = ''
    provisionStatus.value = 'idle'
    logs.value = []
    completedSteps.value = new Set()
    emit('terminated')
  } catch (err) {
    alert(`Error al terminar el pod: ${err.message}`)
  }
}

function tryClose() {
  if (!isProvisioning.value) {
    emit('close')
  }
}

// ──────────────────────────────────────────────────────────────
// Watch show: al abrir el panel, sincronizar estado inicial
// ──────────────────────────────────────────────────────────────
watch(() => props.show, (visible) => {
  if (visible) {
    provisionStatus.value = props.initialStatus || 'idle'
    activePodId.value = props.configuredPodId || ''
    // Si llegamos al panel ya en estado provisioning, reconectar SSE
    if (provisionStatus.value === 'provisioning') {
      connectSSE()
    }
  } else {
    if (sseSource) {
      sseSource.close()
      sseSource = null
    }
  }
})

onUnmounted(() => {
  if (sseSource) {
    sseSource.close()
    sseSource = null
  }
})
</script>

<style scoped>
/* ═══════════════════════════════════════════════════════════
   Variables y reset
   ═══════════════════════════════════════════════════════════ */
:root {
  --pp-bg: #0d1117;
  --pp-surface: #161b22;
  --pp-border: #30363d;
  --pp-accent: #a855f7;
  --pp-accent-glow: rgba(168, 85, 247, 0.3);
  --pp-success: #22c55e;
  --pp-error: #ef4444;
  --pp-warn: #f59e0b;
  --pp-text: #e6edf3;
  --pp-muted: #8b949e;
}

/* ── Backdrop & Modal ─────────────────────────────────────── */
.pp-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.pp-modal {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 16px;
  width: 100%;
  max-width: 620px;
  max-height: 90vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow:
    0 25px 80px rgba(0, 0, 0, 0.6),
    0 0 0 1px rgba(168, 85, 247, 0.15);
}

/* ── Header ─────────────────────────────────────────────── */
.pp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  border-bottom: 1px solid #21262d;
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.08), transparent);
}

.pp-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.pp-gpu-icon {
  font-size: 2rem;
  filter: drop-shadow(0 0 12px rgba(168, 85, 247, 0.6));
}

.pp-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
  color: #e6edf3;
  letter-spacing: -0.3px;
}

.pp-subtitle {
  margin: 2px 0 0;
  font-size: 0.78rem;
  color: #8b949e;
}

.pp-close {
  background: transparent;
  border: 1px solid #30363d;
  border-radius: 8px;
  color: #8b949e;
  cursor: pointer;
  font-size: 1rem;
  padding: 4px 10px;
  transition: all 0.15s;
}
.pp-close:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.1);
  border-color: #ef4444;
  color: #ef4444;
}
.pp-close:disabled { opacity: 0.3; cursor: not-allowed; }

/* ── Body ─────────────────────────────────────────────────── */
.pp-body {
  padding: 20px 24px 24px;
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Info Card ────────────────────────────────────────────── */
.pp-info-card {
  display: flex;
  gap: 12px;
  background: rgba(30, 215, 96, 0.05);
  border: 1px solid rgba(34, 197, 94, 0.2);
  border-radius: 10px;
  padding: 14px 16px;
}
.pp-info-icon { font-size: 1.3rem; flex-shrink: 0; }
.pp-info-text { margin: 0; font-size: 0.85rem; color: #c9d1d9; line-height: 1.5; }

/* ── SSH Note ─────────────────────────────────────────────── */
.pp-ssh-note {
  display: flex;
  gap: 10px;
  background: rgba(245, 158, 11, 0.06);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: 10px;
  padding: 12px 14px;
  align-items: flex-start;
}
.pp-ssh-icon { font-size: 1.1rem; flex-shrink: 0; padding-top: 2px; }
.pp-ssh-note p { margin: 0; font-size: 0.8rem; color: #c9d1d9; }
.pp-ssh-note code { background: rgba(255,255,255,0.08); border-radius: 4px; padding: 1px 5px; font-size: 0.75rem; }

/* ── Field ────────────────────────────────────────────────── */
.pp-field-group { display: flex; flex-direction: column; gap: 6px; }
.pp-label { font-size: 0.82rem; font-weight: 600; color: #c9d1d9; }
.pp-req { color: #ef4444; }
.pp-input {
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 8px;
  color: #e6edf3;
  font-size: 0.9rem;
  padding: 10px 14px;
  outline: none;
  transition: border-color 0.2s;
}
.pp-input:focus { border-color: #a855f7; }
.pp-field-hint { margin: 0; font-size: 0.75rem; color: #8b949e; }

.pp-check-badge {
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  border-radius: 8px;
  color: #22c55e;
  font-size: 0.82rem;
  padding: 8px 14px;
  display: inline-block;
}

/* ── Buttons ─────────────────────────────────────────────── */
.pp-btn-provision {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 14px 20px;
  background: linear-gradient(135deg, #a855f7, #7c3aed);
  border: none;
  border-radius: 12px;
  color: white;
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: 0 4px 20px rgba(168, 85, 247, 0.35);
  margin-top: 4px;
}
.pp-btn-provision:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 6px 28px rgba(168, 85, 247, 0.5);
}
.pp-btn-provision:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
}
.pp-btn-icon { font-size: 1.2rem; }

.pp-btn-terminate {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  color: #ef4444;
  cursor: pointer;
  font-size: 0.82rem;
  padding: 8px 14px;
  transition: all 0.15s;
}
.pp-btn-terminate:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: #ef4444;
}

.pp-btn-retry {
  background: rgba(168, 85, 247, 0.1);
  border: 1px solid rgba(168, 85, 247, 0.3);
  border-radius: 8px;
  color: #a855f7;
  cursor: pointer;
  font-size: 0.88rem;
  font-weight: 600;
  padding: 10px 18px;
  transition: all 0.15s;
}
.pp-btn-retry:hover {
  background: rgba(168, 85, 247, 0.2);
}

.pp-btn-close-ready {
  flex: 1;
  background: linear-gradient(135deg, #22c55e, #16a34a);
  border: none;
  border-radius: 10px;
  color: white;
  cursor: pointer;
  font-size: 0.95rem;
  font-weight: 700;
  padding: 12px 20px;
  transition: all 0.2s;
  box-shadow: 0 4px 16px rgba(34, 197, 94, 0.3);
}
.pp-btn-close-ready:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 22px rgba(34, 197, 94, 0.45);
}

/* ── Terminate idle row ──────────────────────────────────── */
.pp-terminate-idle { display: flex; justify-content: center; }

/* ── Stepper ─────────────────────────────────────────────── */
.pp-stepper {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pp-step {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 8px;
  transition: background 0.2s;
}
.pp-step.active {
  background: rgba(168, 85, 247, 0.08);
  border: 1px solid rgba(168, 85, 247, 0.2);
}
.pp-step.done { opacity: 0.6; }
.pp-step.pending { opacity: 0.35; }

.pp-step-indicator {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 700;
  flex-shrink: 0;
  border: 2px solid;
}
.pp-step.done .pp-step-indicator {
  background: rgba(34, 197, 94, 0.15);
  border-color: #22c55e;
  color: #22c55e;
}
.pp-step.active .pp-step-indicator {
  background: rgba(168, 85, 247, 0.15);
  border-color: #a855f7;
  color: #a855f7;
}
.pp-step.pending .pp-step-indicator {
  background: rgba(139, 148, 158, 0.08);
  border-color: #30363d;
  color: #8b949e;
}

.pp-step-spinner {
  display: inline-block;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.pp-step-info { flex: 1; }
.pp-step-label { margin: 0; font-size: 0.82rem; font-weight: 600; color: #c9d1d9; }
.pp-step-msg { margin: 2px 0 0; font-size: 0.74rem; color: #8b949e; }

/* ── Log Container ───────────────────────────────────────── */
.pp-log-container {
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 10px;
  padding: 12px 14px;
  max-height: 160px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: 'SFMono-Regular', Consolas, monospace;
}
.pp-log-container.pp-log-error-mode { max-height: 200px; }

.pp-log-entry {
  display: flex;
  gap: 8px;
  font-size: 0.75rem;
  line-height: 1.4;
}
.pp-log-time { color: #484f58; flex-shrink: 0; }
.pp-log-msg { color: #8b949e; }
.log-success .pp-log-msg { color: #22c55e; }
.log-error .pp-log-msg { color: #ef4444; }
.log-info .pp-log-msg { color: #c9d1d9; }

/* ── Success Banner ──────────────────────────────────────── */
.pp-success-banner {
  position: relative;
  overflow: hidden;
  background: rgba(34, 197, 94, 0.06);
  border: 1px solid rgba(34, 197, 94, 0.25);
  border-radius: 14px;
  padding: 24px 20px;
  text-align: center;
}
.pp-success-glow {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 50% 0%, rgba(34,197,94,0.12) 0%, transparent 70%);
  pointer-events: none;
}
.pp-success-icon { font-size: 2.5rem; margin-bottom: 10px; }
.pp-success-title { margin: 0 0 6px; font-size: 1.15rem; font-weight: 700; color: #22c55e; }
.pp-success-gpu { margin: 0 0 4px; font-size: 0.85rem; color: #c9d1d9; }
.pp-success-price { color: #8b949e; }
.pp-success-pod { margin: 0; font-size: 0.78rem; color: #8b949e; }
.pp-success-pod code { background: rgba(255,255,255,0.08); border-radius: 4px; padding: 2px 6px; }

.pp-ready-actions { display: flex; flex-direction: column; gap: 10px; }

/* ── Error Banner ────────────────────────────────────────── */
.pp-error-banner {
  display: flex;
  gap: 14px;
  background: rgba(239, 68, 68, 0.07);
  border: 1px solid rgba(239, 68, 68, 0.25);
  border-radius: 12px;
  padding: 16px 18px;
  align-items: flex-start;
}
.pp-error-icon { font-size: 1.5rem; flex-shrink: 0; }
.pp-error-title { margin: 0 0 4px; font-size: 0.95rem; font-weight: 700; color: #ef4444; }
.pp-error-last { margin: 0; font-size: 0.8rem; color: #8b949e; }
.pp-error-actions { display: flex; gap: 10px; flex-wrap: wrap; }

/* ── Transition ──────────────────────────────────────────── */
.panel-fade-enter-active,
.panel-fade-leave-active { transition: all 0.25s ease; }
.panel-fade-enter-from,
.panel-fade-leave-to { opacity: 0; transform: scale(0.96); }
</style>
