<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false
  },
  apiBase: {
    type: String,
    default: 'http://127.0.0.1:8002'
  }
})

const emit = defineEmits(['close', 'config-saved', 'worker-connected'])

const form = ref({
  runpod_api_key: '',
  pod_id: '',
  pod_host: '',
  pod_ssh_port: 22,
  ssh_key_path: '~/.ssh/id_rsa',
  hf_token: '',
  local_proxy_port: 8005
})

const isLoading = ref(false)
const isDetecting = ref(false)
const isConnecting = ref(false)
const connectionStep = ref('') // 'ssh' | 'env' | 'gpu' | 'done' | ''
const message = ref({ text: '', type: 'info' })
const showApiKey = ref(false)

function setMessage(text, type = 'info') {
  message.value = { text, type }
}

async function loadConfig() {
  isLoading.value = true
  message.value = { text: '', type: 'info' }
  connectionStep.value = ''
  try {
    const res = await fetch(`${props.apiBase}/api/worker/config/`)
    if (res.ok) {
      const data = await res.json()
      form.value = {
        runpod_api_key: data.runpod_api_key || '',
        pod_id: data.pod_id || '',
        pod_host: data.pod_host || '',
        pod_ssh_port: data.pod_ssh_port || 22,
        ssh_key_path: data.ssh_key_path || '~/.ssh/id_rsa',
        hf_token: data.hf_token || '',
        local_proxy_port: data.local_proxy_port || 8005
      }
    }
  } catch (err) {
    setMessage(`Error cargando configuración: ${err.message}`, 'danger')
  } finally {
    isLoading.value = false
  }
}

async function saveConfig(silent = false) {
  try {
    const res = await fetch(`${props.apiBase}/api/worker/config/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value)
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(JSON.stringify(err))
    }
    const saved = await res.json()
    if (!silent) {
      setMessage('Configuración guardada correctamente.', 'success')
    }
    emit('config-saved', saved)
    return true
  } catch (err) {
    setMessage(`Error guardando: ${err.message}`, 'danger')
    return false
  }
}

async function handleDetect() {
  if (!form.value.pod_id || !form.value.runpod_api_key) {
    setMessage('Debes ingresar RunPod API Key y Pod ID para detectar automáticamente.', 'warning')
    return
  }

  isDetecting.value = true
  setMessage('Consultando API GraphQL de RunPod...', 'info')

  try {
    const res = await fetch(`${props.apiBase}/api/worker/detect/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pod_id: form.value.pod_id,
        runpod_api_key: form.value.runpod_api_key
      })
    })

    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.error || 'Fallo en detección automática')
    }

    form.value.pod_host = data.pod_host
    form.value.pod_ssh_port = data.pod_ssh_port
    setMessage(`¡Detectado con éxito! IP: ${data.pod_host} | Puerto SSH: ${data.pod_ssh_port}`, 'success')
    emit('config-saved', form.value)
  } catch (err) {
    setMessage(`Error en detección: ${err.message}`, 'danger')
  } finally {
    isDetecting.value = false
  }
}

async function handleConnectAndValidate() {
  const savedOk = await saveConfig(true)
  if (!savedOk) return

  if (!form.value.pod_host) {
    setMessage('Debes configurar o detectar la IP del Pod (Host) antes de conectar.', 'warning')
    return
  }

  isConnecting.value = true
  connectionStep.value = 'ssh'
  setMessage('Paso 1/4: Conectando SSH hacia el Pod de RunPod...', 'info')

  // Simular transición visual de pasos mientras se orquesta en backend
  const stepTimer1 = setTimeout(() => {
    if (isConnecting.value) {
      connectionStep.value = 'env'
      setMessage('Paso 2/4: Verificando entorno y dependencias (ffmpeg, pyannote, faster-whisper)...', 'info')
    }
  }, 3000)

  const stepTimer2 = setTimeout(() => {
    if (isConnecting.value) {
      connectionStep.value = 'gpu'
      setMessage('Paso 3/4: Levantando worker GPU /server.py en sesión tmux...', 'info')
    }
  }, 9000)

  try {
    const res = await fetch(`${props.apiBase}/api/pod/connect/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form.value)
    })

    clearTimeout(stepTimer1)
    clearTimeout(stepTimer2)

    const data = await res.json()
    if (!res.ok || data.status !== 'connected') {
      throw new Error(data.error || data.message || 'Worker no respondió 200 OK')
    }

    connectionStep.value = 'done'
    setMessage(`¡Paso 4/4: Listo! GPU Conectada y Worker validado exitosamente (200 OK en proxy :${form.value.local_proxy_port})`, 'success')
    emit('worker-connected', data)
  } catch (err) {
    clearTimeout(stepTimer1)
    clearTimeout(stepTimer2)
    connectionStep.value = ''
    setMessage(`Fallo al preparar worker: ${err.message}`, 'danger')
  } finally {
    isConnecting.value = false
  }
}

watch(() => props.isOpen, (open) => {
  if (open) {
    loadConfig()
  }
})
</script>

<template>
  <div v-if="isOpen" class="modal-backdrop" @click.self="emit('close')">
    <div class="modal-card">
      <div class="modal-header">
        <div class="modal-title-group">
          <div class="icon-cube">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
              <polyline points="2 17 12 22 22 17"></polyline>
              <polyline points="2 12 12 17 22 12"></polyline>
            </svg>
          </div>
          <div>
            <h3>Configuración Remota de RunPod GPU</h3>
            <p class="subtitle">Orquestación de túnel SSH y Worker sin usar terminal Mac</p>
          </div>
        </div>
        <button class="btn-close" @click="emit('close')">&times;</button>
      </div>

      <!-- Feedback Alert & Stepper -->
      <div v-if="connectionStep" class="connection-steps-container">
        <div class="step-badge" :class="{ active: connectionStep === 'ssh', done: ['env', 'gpu', 'done'].includes(connectionStep) }">
          <span class="step-num">1</span>
          <span>Conectando SSH</span>
        </div>
        <div class="step-arrow">&rarr;</div>
        <div class="step-badge" :class="{ active: connectionStep === 'env', done: ['gpu', 'done'].includes(connectionStep) }">
          <span class="step-num">2</span>
          <span>Verificando entorno</span>
        </div>
        <div class="step-arrow">&rarr;</div>
        <div class="step-badge" :class="{ active: connectionStep === 'gpu', done: connectionStep === 'done' }">
          <span class="step-num">3</span>
          <span>Levantando GPU</span>
        </div>
        <div class="step-arrow">&rarr;</div>
        <div class="step-badge" :class="{ done: connectionStep === 'done' }">
          <span class="step-num">4</span>
          <span>Listo</span>
        </div>
      </div>

      <div v-if="message.text" :class="['alert-box', `alert-${message.type}`]">
        <span v-if="message.type === 'info'" class="alert-icon">ℹ️</span>
        <span v-else-if="message.type === 'success'" class="alert-icon">✅</span>
        <span v-else-if="message.type === 'warning'" class="alert-icon">⚠️</span>
        <span v-else class="alert-icon">❌</span>
        <span>{{ message.text }}</span>
      </div>

      <div class="modal-body">
        <div class="grid-form">
          <!-- RunPod API Key -->
          <div class="form-group full-span">
            <div class="label-row">
              <label>RunPod API Key (GraphQL)</label>
              <button class="btn-text-action" type="button" @click="showApiKey = !showApiKey">
                {{ showApiKey ? 'Ocultar' : 'Mostrar' }}
              </button>
            </div>
            <input
              :type="showApiKey ? 'text' : 'password'"
              v-model="form.runpod_api_key"
              placeholder="rpa_..."
              class="form-control"
            />
            <small class="field-hint">Necesaria para resolver automáticamente IP y puerto SSH mapeado a 22.</small>
          </div>

          <!-- Pod ID + Detect Button -->
          <div class="form-group full-span">
            <label>Pod ID</label>
            <div class="input-with-action">
              <input
                type="text"
                v-model="form.pod_id"
                placeholder="ej. k7hmz605lbejrn"
                class="form-control"
              />
              <button
                class="btn btn-detect"
                :disabled="isDetecting || !form.pod_id"
                @click="handleDetect"
                title="Consultar API GraphQL de RunPod para autocompletar IP y puerto SSH"
              >
                <span v-if="isDetecting" class="spinner-small"></span>
                <span v-else>🔍 Detectar por API</span>
              </button>
            </div>
          </div>

          <!-- Pod Host & SSH Port -->
          <div class="form-group">
            <label>IP del Pod (Host público)</label>
            <input
              type="text"
              v-model="form.pod_host"
              placeholder="ej. 157.157.221.29"
              class="form-control"
            />
          </div>

          <div class="form-group">
            <label>Puerto SSH externo (TCP mapeado a 22)</label>
            <input
              type="number"
              v-model.number="form.pod_ssh_port"
              placeholder="57393"
              class="form-control"
            />
          </div>

          <!-- SSH Key Path & Local Proxy Port -->
          <div class="form-group">
            <label>Ruta clave privada SSH local</label>
            <input
              type="text"
              v-model="form.ssh_key_path"
              placeholder="~/.ssh/id_rsa"
              class="form-control"
            />
          </div>

          <div class="form-group">
            <label>Puerto Proxy Local (Túnel SSH)</label>
            <input
              type="number"
              v-model.number="form.local_proxy_port"
              placeholder="8005"
              class="form-control"
            />
          </div>

          <!-- Hugging Face Token -->
          <div class="form-group full-span">
            <label>Hugging Face Token (Diarización Pyannote)</label>
            <input
              type="password"
              v-model="form.hf_token"
              placeholder="hf_..."
              class="form-control"
            />
            <small class="field-hint">Token con permisos aceptados en pyannote/speaker-diarization-3.1.</small>
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="btn btn-secondary" @click="saveConfig(false)">
          Guardar Configuración
        </button>
        <button
          class="btn btn-primary btn-validate"
          :disabled="isConnecting || !form.pod_host"
          @click="handleConnectAndValidate"
        >
          <span v-if="isConnecting" class="spinner-small"></span>
          <span>⚡ Conectar y Preparar Worker</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(4, 7, 15, 0.78);
  backdrop-filter: blur(8px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 1rem;
}

.modal-card {
  background: #121826;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  width: 100%;
  max-width: 620px;
  box-shadow: 0 24px 48px -12px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: modalIn 0.2s ease-out;
}

@keyframes modalIn {
  from { opacity: 0; transform: scale(0.96) translateY(8px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.connection-steps-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: rgba(15, 23, 42, 0.85);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  margin: 1rem 1.5rem 0.5rem 1.5rem;
  padding: 0.75rem 1rem;
  font-size: 0.78rem;
}

.step-badge {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  color: #64748b;
  font-weight: 500;
  transition: all 0.2s ease;
}

.step-badge .step-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.72rem;
  font-weight: 700;
}

.step-badge.active {
  color: #fbbf24;
}

.step-badge.active .step-num {
  background: #f59e0b;
  color: #0f172a;
}

.step-badge.done {
  color: #10b981;
}

.step-badge.done .step-num {
  background: #10b981;
  color: #0f172a;
}

.step-arrow {
  color: #475569;
  font-size: 0.85rem;
}

.modal-title-group {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}

.icon-cube {
  background: rgba(99, 102, 241, 0.15);
  color: #818cf8;
  padding: 0.5rem;
  border-radius: 10px;
  display: flex;
}

.modal-title-group h3 {
  font-size: 1.15rem;
  font-weight: 700;
  color: #f1f5f9;
}

.modal-title-group .subtitle {
  font-size: 0.8rem;
  color: #94a3b8;
}

.btn-close {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 1.6rem;
  line-height: 1;
  cursor: pointer;
  padding: 0.25rem 0.5rem;
  border-radius: 6px;
  transition: color 0.15s;
}

.btn-close:hover {
  color: #fff;
}

.modal-body {
  padding: 1.5rem;
  max-height: 70vh;
  overflow-y: auto;
}

.grid-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.1rem;
}

.full-span {
  grid-column: span 2;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.form-group label {
  font-size: 0.82rem;
  font-weight: 600;
  color: #cbd5e1;
}

.label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.btn-text-action {
  background: none;
  border: none;
  color: #818cf8;
  font-size: 0.75rem;
  font-weight: 600;
  cursor: pointer;
  padding: 0;
}

.btn-text-action:hover {
  text-decoration: underline;
}

.form-control {
  background: #0b0f19;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 0.55rem 0.85rem;
  color: #f8fafc;
  font-size: 0.875rem;
  transition: border-color 0.15s;
}

.form-control:focus {
  outline: none;
  border-color: #6366f1;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.25);
}

.field-hint {
  font-size: 0.72rem;
  color: #64748b;
}

.input-with-action {
  display: flex;
  gap: 0.5rem;
}

.input-with-action .form-control {
  flex: 1;
}

.btn-detect {
  background: #1e293b;
  color: #38bdf8;
  border: 1px solid rgba(56, 189, 248, 0.3);
  padding: 0.55rem 1rem;
  border-radius: 8px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  transition: all 0.15s;
}

.btn-detect:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.15);
  border-color: #38bdf8;
}

.btn-detect:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 0.75rem;
  padding: 1.25rem 1.5rem;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(11, 15, 25, 0.5);
}

.btn-validate {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.btn-validate:hover:not(:disabled) {
  background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
}

.alert-box {
  margin: 1rem 1.5rem 0 1.5rem;
  padding: 0.75rem 1rem;
  border-radius: 8px;
  font-size: 0.84rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.alert-info {
  background: rgba(56, 189, 248, 0.12);
  border: 1px solid rgba(56, 189, 248, 0.3);
  color: #bae6fd;
}

.alert-success {
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: #a7f3d0;
}

.alert-warning {
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.3);
  color: #fde68a;
}

.alert-danger {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fecaca;
}

.spinner-small {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  display: inline-block;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
