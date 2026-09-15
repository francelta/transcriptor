<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import UploadSection from './components/UploadSection.vue'
import AudioControls from './components/AudioControls.vue'
import QuickNotationsBar from './components/QuickNotationsBar.vue'
import KaraokeViewer from './components/KaraokeViewer.vue'
import PodConfigModal from './components/PodConfigModal.vue'

const API_BASE = 'http://127.0.0.1:8002'

// Subcomponent refs
const uploadSectionRef = ref(null)
const audioControlsRef = ref(null)

// Application State
const currentJob = ref(null)
const activeJobId = ref(null)
const activeJobsCount = ref(0)
let activeJobsPoller = null
let workerHealthPoller = null
const audioUrl = ref('')
const isUploading = ref(false)
const liveLogs = ref([])
const currentTime = ref(0)
const activeTextareaIndex = ref(null)

// RunPod Worker State: 'disconnected' (red) | 'provisioning' (yellow) | 'connected' (green)
const workerStatus = ref('disconnected')
const workerDetails = ref({ pod_host: '', pod_ssh_port: null, message: '' })
const showPodConfigModal = ref(false)

const isGpuConnected = computed(() => workerStatus.value === 'connected')

// Tracking changes & Reset Modal
const lastSavedState = ref('')
const showResetConfirmModal = ref(false)

// Notifications
const notification = ref({ show: false, message: '', type: 'info' })
let eventSource = null

function notify(msg, type = 'info') {
  notification.value = { show: true, message: msg, type }
  setTimeout(() => {
    notification.value.show = false
  }, 4000)
}

async function checkWorkerHealth() {
  try {
    const res = await fetch(`${API_BASE}/api/pod/status/`)
    if (res.ok) {
      const data = await res.json()
      if (data.status === 'connected' && data.healthy) {
        workerStatus.value = 'connected'
      } else if (workerStatus.value !== 'provisioning') {
        workerStatus.value = 'disconnected'
      }
      workerDetails.value = data
    }
  } catch (err) {
    if (workerStatus.value !== 'provisioning') {
      workerStatus.value = 'disconnected'
    }
  }
}

function handleWorkerConnected(data) {
  workerStatus.value = 'connected'
  workerDetails.value = data
  notify('Worker GPU conectado y listo para transcribir.', 'success')
}

function openPodConfigModal() {
  showPodConfigModal.value = true
}


// Consultar procesos activos en el servidor (solo 1 permitido)
async function fetchActiveJobs() {
  try {
    const res = await fetch(`${API_BASE}/api/transcribe/active/`)
    if (res.ok) {
      const data = await res.json()
      activeJobsCount.value = data.count || 0
      // Si hay un proceso corriendo y no estábamos enterados, podemos enlazarlo
      if (data.count > 0 && !isUploading.value && !currentJob.value) {
        const runningJob = data.jobs[0]
        activeJobId.value = runningJob.id
      }
    }
  } catch (e) {
    console.debug('Error comprobando trabajos activos:', e)
  }
}

// Matar todos los procesos viejos/colgados
async function killActiveJobs() {
  try {
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    const res = await fetch(`${API_BASE}/api/transcribe/kill-active/`, {
      method: 'POST'
    })
    if (res.ok) {
      const data = await res.json()
      activeJobsCount.value = 0
      isUploading.value = false
      liveLogs.value.push({
        time: new Date().toLocaleTimeString(),
        message: `Se han detenido ${data.killed_count} proceso(s) en ejecución. El servidor quedó limpio.`,
        level: 'warning'
      })
      notify(`Se han detenido ${data.killed_count} proceso(s) viejos.`, 'warning')
    }
  } catch (err) {
    notify(`Error al detener procesos: ${err.message}`, 'danger')
  }
}

function formatFullTimestamp(secs) {
  if (isNaN(secs) || secs === null) return '[0:00:00]'
  const total = Math.floor(secs)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return `[${h}:${m < 10 ? '0' : ''}${m}:${s < 10 ? '0' : ''}${s}]`
}

// Iniciar transcripción conectando con backend Django
async function handleStartTranscription(params) {
  const { audioFile, taskNumber, hfToken, minSpeakers, maxSpeakers, llmConfig } = params

  isUploading.value = true
  liveLogs.value = [
    { time: new Date().toLocaleTimeString(), message: 'Enviando archivo de audio al servidor backend...', level: 'info' }
  ]

  const formData = new FormData()
  formData.append('audio_file', audioFile)
  formData.append('task_number', taskNumber)
  if (hfToken) {
    formData.append('hf_token', hfToken)
  }
  formData.append('min_speakers', minSpeakers)
  formData.append('max_speakers', maxSpeakers)

  // Opciones de LLM
  if (llmConfig) {
    formData.append('llm_enabled', llmConfig.enabled ? 'true' : 'false')
    if (llmConfig.apiUrl) formData.append('llm_api_url', llmConfig.apiUrl)
    if (llmConfig.apiKey) formData.append('llm_api_key', llmConfig.apiKey)
    if (llmConfig.model) formData.append('llm_model', llmConfig.model)
  }

  try {
    const res = await fetch(`${API_BASE}/api/transcribe/`, {
      method: 'POST',
      body: formData,
    })

    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.error || 'Error al iniciar la transcripción.')
    }

    const initialJob = await res.json()
    const jobId = initialJob.id
    activeJobId.value = jobId

    // Suscribirse al stream SSE para logs en tiempo real
    if (eventSource) {
      eventSource.close()
    }

    eventSource = new EventSource(`${API_BASE}/api/transcribe/${jobId}/events/`)

    eventSource.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data)
        if (payload.type === 'log' && payload.log) {
          liveLogs.value.push(payload.log)
        } else if (payload.type === 'status_change') {
          const finishedJob = payload.job
          if (payload.status === 'completed') {
            currentJob.value = finishedJob
            audioUrl.value = finishedJob.audio_url || `${API_BASE}/media/${finishedJob.audio_file}`
            lastSavedState.value = JSON.stringify({
              blocks: finishedJob.transcript_blocks,
              metadata: finishedJob.metadata_header
            })
            isUploading.value = false
            notify('¡Transcripción y post-procesado completados con éxito!', 'success')
          } else if (payload.status === 'failed') {
            isUploading.value = false
            const errDetail = finishedJob.error_message || 'Fallo durante el pipeline de WhisperX.'
            notify(`Error en el procesamiento: ${errDetail}`, 'danger')
          }
          if (eventSource) {
            eventSource.close()
            eventSource = null
          }
        }
      } catch (err) {
        console.error('Error parseando SSE:', err)
      }
    }

    eventSource.onerror = (err) => {
      console.warn('Conexión SSE terminada o reconectando:', err)
    }

  } catch (err) {
    liveLogs.value.push({
      time: new Date().toLocaleTimeString(),
      message: `Error de conexión: ${err.message}`,
      level: 'error'
    })
    notify(`Error: ${err.message}`, 'danger')
    isUploading.value = false
  }
}

// Cancelar proceso activo
async function handleCancelTranscription() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }

  if (activeJobId.value) {
    try {
      await fetch(`${API_BASE}/api/transcribe/${activeJobId.value}/cancel/`, {
        method: 'POST'
      })
    } catch (err) {
      console.warn('Error enviando cancelación al backend:', err)
    }
  }

  isUploading.value = false
  liveLogs.value.push({
    time: new Date().toLocaleTimeString(),
    message: 'Proceso cancelado por el usuario. Recursos liberados.',
    level: 'warning'
  })
  notify('Proceso de transcripción cancelado.', 'warning')
}

// Actualización de tiempo del audio
function onTimeUpdate(val) {
  currentTime.value = val
}

function onSeekTo(time) {
  if (audioControlsRef.value) {
    audioControlsRef.value.seekTo(time)
  }
}

function onUpdateActiveTextarea(index) {
  activeTextareaIndex.value = index
}

function onUpdateBlockText({ index, text }) {
  if (currentJob.value?.transcript_blocks?.[index]) {
    currentJob.value.transcript_blocks[index].text = text
  }
}

function onUpdateBlockSpeaker({ index, speaker }) {
  if (currentJob.value?.transcript_blocks?.[index]) {
    currentJob.value.transcript_blocks[index].speaker = speaker
  }
}

// Inserción rápida de anotaciones
function handleInsertNotation(template) {
  if (activeTextareaIndex.value === null || !currentJob.value) {
    notify('Haz clic en el cuadro de texto del bloque donde deseas insertar la anotación.', 'warning')
    return
  }

  const block = currentJob.value.transcript_blocks[activeTextareaIndex.value]
  const ts = formatFullTimestamp(currentTime.value)
  let insertion = template.replace('{ts}', ts)

  const textarea = document.getElementById(`textarea-block-${block.id}`)
  if (textarea) {
    const startPos = textarea.selectionStart
    const endPos = textarea.selectionEnd
    const currentText = block.text || ''

    if (template === 'underlined' || template === '[brackets]' || template === '(word)') {
      const selected = currentText.substring(startPos, endPos) || 'palabra'
      if (template === 'underlined') {
        insertion = `<u>${selected}</u>`
      } else if (template === '[brackets]') {
        insertion = `[${selected || 'comentario'}]`
      } else if (template === '(word)') {
        insertion = `(${selected})`
      }
    }

    block.text = currentText.substring(0, startPos) + ' ' + insertion + ' ' + currentText.substring(endPos)

    nextTick(() => {
      textarea.focus()
      const newPos = startPos + insertion.length + 2
      textarea.setSelectionRange(newPos, newPos)
    })
  } else {
    block.text = (block.text || '') + ' ' + insertion
  }
}

// Guardar progreso en la BD
async function saveProgress() {
  if (!currentJob.value) return

  try {
    const res = await fetch(`${API_BASE}/api/transcribe/${currentJob.value.id}/save/`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        transcript_blocks: currentJob.value.transcript_blocks,
        metadata_header: currentJob.value.metadata_header,
      }),
    })

    if (!res.ok) throw new Error('Error al guardar cambios')
    lastSavedState.value = JSON.stringify({
      blocks: currentJob.value.transcript_blocks,
      metadata: currentJob.value.metadata_header
    })
    notify('Progreso guardado correctamente en la base de datos.', 'success')
  } catch (err) {
    notify(`Error al guardar: ${err.message}`, 'danger')
  }
}

function hasUnsavedChanges() {
  if (!currentJob.value) return false
  if (!lastSavedState.value) return true
  const currentState = JSON.stringify({
    blocks: currentJob.value.transcript_blocks,
    metadata: currentJob.value.metadata_header
  })
  return currentState !== lastSavedState.value
}

function handleNewTranscriptionRequest() {
  if (hasUnsavedChanges()) {
    showResetConfirmModal.value = true
  } else {
    resetTranscriptionStudio()
  }
}

function cancelReset() {
  showResetConfirmModal.value = false
}

function confirmReset() {
  showResetConfirmModal.value = false
  resetTranscriptionStudio()
}

function resetTranscriptionStudio() {
  if (audioControlsRef.value) {
    audioControlsRef.value.stopAudio()
  }
  if (audioUrl.value && audioUrl.value.startsWith('blob:')) {
    URL.revokeObjectURL(audioUrl.value)
  }

  audioUrl.value = ''
  currentTime.value = 0
  activeTextareaIndex.value = null
  isUploading.value = false
  liveLogs.value = []
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }

  currentJob.value = null
  lastSavedState.value = ''
  if (uploadSectionRef.value) {
    uploadSectionRef.value.resetForm()
  }

  notify('Se ha reiniciado el estudio para una nueva transcripción.', 'info')
}

function exportDocx() {
  if (!currentJob.value) return
  window.open(`${API_BASE}/api/transcribe/${currentJob.value.id}/export-docx/`, '_blank')
}

// Shortcuts
function handleKeyDown(e) {
  const isInputActive = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)

  if (e.code === 'Space' && !isInputActive) {
    e.preventDefault()
    audioControlsRef.value?.togglePlay()
  } else if ((e.ctrlKey || e.metaKey) && e.code === 'ArrowLeft') {
    e.preventDefault()
    audioControlsRef.value?.seekRelative(-3)
  } else if ((e.ctrlKey || e.metaKey) && e.code === 'ArrowRight') {
    e.preventDefault()
    audioControlsRef.value?.seekRelative(3)
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown)
  fetchActiveJobs()
  activeJobsPoller = setInterval(fetchActiveJobs, 3000)
  checkWorkerHealth()
  workerHealthPoller = setInterval(checkWorkerHealth, 8000)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown)
  if (activeJobsPoller) {
    clearInterval(activeJobsPoller)
    activeJobsPoller = null
  }
  if (workerHealthPoller) {
    clearInterval(workerHealthPoller)
    workerHealthPoller = null
  }
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
})

</script>

<template>
  <div class="studio-container">
    <!-- Header / Navbar -->
    <header class="studio-header">
      <div class="header-left">
        <div class="logo-badge">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
            <line x1="12" x2="12" y1="19" y2="22"></line>
          </svg>
        </div>
        <div>
          <h1 class="header-title">TranscriberStudio Pro</h1>
          <p class="header-subtitle">WhisperX large-v3 &bull; Pyannote Diarization &bull; Capa Semántica LLM &bull; Verdana 8pt</p>
        </div>
      </div>

      <div class="header-right">
        <!-- Badge de Estado GPU RunPod (🔴 Desconectado | 🟡 Inicializando Worker GPU | 🟢 Worker Listo) -->
        <button
          :class="['btn-gpu-badge', `status-${workerStatus}`]"
          @click="openPodConfigModal"
          :title="`Estado del Pod: ${workerStatus.toUpperCase()} - Haz clic para configurar o conectar`"
        >
          <span class="status-indicator-dot"></span>
          <span v-if="workerStatus === 'connected'" class="status-label">
            🟢 Worker Listo <template v-if="workerDetails.pod_host">({{ workerDetails.pod_host }}:{{ workerDetails.pod_ssh_port }})</template>
          </span>
          <span v-else-if="workerStatus === 'provisioning'" class="status-label">
            🟡 Inicializando Worker GPU...
          </span>
          <span v-else class="status-label">
            🔴 Desconectado (Configurar RunPod)
          </span>
        </button>

        <!-- Indicador global en navbar si hay procesos de fondo y estamos en el workspace -->
        <button 
          v-if="activeJobsCount > 0" 
          class="btn btn-warning-pill" 
          @click="killActiveJobs" 
          title="Solo se permite 1 proceso a la vez. Haz clic para matar procesos viejos colgados."
        >
          ⚡ {{ activeJobsCount }} en ejecución &bull; Matar viejos
        </button>

        <template v-if="currentJob">
          <button class="btn btn-secondary btn-new" @click="handleNewTranscriptionRequest" title="Iniciar una nueva transcripción">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M12 5v14M5 12h14"></path>
            </svg>
            Nueva transcripción
          </button>
          <button class="btn btn-secondary" @click="saveProgress">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
              <polyline points="17 21 17 13 7 13 7 21"></polyline>
              <polyline points="7 3 7 8 15 8"></polyline>
            </svg>
            Guardar progreso
          </button>
          <button class="btn btn-primary" @click="exportDocx">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" x2="12" y1="15" y2="3"></line>
            </svg>
            Descargar .DOCX
          </button>
        </template>
      </div>
    </header>

    <!-- Modal de Configuración y Diagnóstico del Pod RunPod -->
    <PodConfigModal
      :is-open="showPodConfigModal"
      :api-base="API_BASE"
      @close="showPodConfigModal = false"
      @config-saved="checkWorkerHealth"
      @worker-connected="handleWorkerConnected"
    />

    <!-- Notification Toast -->
    <div v-if="notification.show" :class="['toast-notification', `toast-${notification.type}`]">
      <span>{{ notification.message }}</span>
    </div>

    <!-- Main Workspace -->
    <main class="studio-main">
      <!-- Upload Section Component -->
      <UploadSection
        v-if="!currentJob || isUploading"
        ref="uploadSectionRef"
        :is-uploading="isUploading"
        :live-logs="liveLogs"
        :active-jobs-count="activeJobsCount"
        :is-gpu-connected="isGpuConnected"
        @start-transcription="handleStartTranscription"
        @cancel-transcription="handleCancelTranscription"
        @kill-active-jobs="killActiveJobs"
        @refresh-active-jobs="fetchActiveJobs"
      />


      <!-- Active Workspace -->
      <div v-if="currentJob && !isUploading" class="workspace-layout">
        <AudioControls
          ref="audioControlsRef"
          :audio-url="audioUrl"
          @timeupdate="onTimeUpdate"
          @seek-to="onSeekTo"
        />

        <QuickNotationsBar
          @insert-notation="handleInsertNotation"
        />

        <KaraokeViewer
          :blocks="currentJob.transcript_blocks || []"
          :current-time="currentTime"
          :active-textarea-index="activeTextareaIndex"
          :original-filename="currentJob.original_filename"
          :duration-seconds="currentJob.duration_seconds"
          :task-number="currentJob.metadata_header?.task_number"
          @seek-to="onSeekTo"
          @update-active-textarea="onUpdateActiveTextarea"
          @update-block-text="onUpdateBlockText"
          @update-block-speaker="onUpdateBlockSpeaker"
        />
      </div>
    </main>

    <!-- Modal de confirmación para nueva transcripción con cambios sin guardar -->
    <div v-if="showResetConfirmModal" class="modal-overlay" @click.self="cancelReset">
      <div class="modal-dialog">
        <div class="modal-header">
          <div class="modal-icon-warn">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
          </div>
          <h3>Nueva transcripción</h3>
        </div>
        <div class="modal-body">
          <p>¿Deseas iniciar una nueva transcripción? Asegúrate de haber descargado el archivo .docx antes de continuar.</p>
        </div>
        <div class="modal-actions">
          <button class="btn btn-secondary" @click="cancelReset">Continuar editando</button>
          <button class="btn btn-danger" @click="confirmReset">Iniciar nueva transcripción</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.studio-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.studio-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: var(--bg-card-solid);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.logo-badge {
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: white;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 14px var(--primary-glow);
}

.header-title {
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.header-subtitle {
  font-size: 0.8rem;
  color: var(--text-muted);
}

.header-right {
  display: flex;
  gap: 0.75rem;
  align-items: center;
}

.studio-main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1.5rem 5rem 1.5rem;
  width: 100%;
}

.workspace-layout {
  display: flex;
  flex-direction: column;
}

/* GPU Status Badge */
.btn-gpu-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.5rem 0.9rem;
  border-radius: 9999px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s ease;
  background: #1e293b;
  color: #cbd5e1;
}

.status-indicator-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

/* 🔴 Desconectado */
.btn-gpu-badge.status-disconnected {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}
.btn-gpu-badge.status-disconnected .status-indicator-dot {
  background: #ef4444;
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.8);
}
.btn-gpu-badge.status-disconnected:hover {
  background: rgba(239, 68, 68, 0.2);
  border-color: #ef4444;
}

/* 🟡 Aprovisionando / Conectando */
.btn-gpu-badge.status-provisioning {
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.35);
  color: #fde68a;
}
.btn-gpu-badge.status-provisioning .status-indicator-dot {
  background: #f59e0b;
  box-shadow: 0 0 8px rgba(245, 158, 11, 0.8);
  animation: pulse-yellow 1.4s infinite;
}
.btn-gpu-badge.status-provisioning:hover {
  background: rgba(245, 158, 11, 0.2);
  border-color: #f59e0b;
}

/* 🟢 GPU Conectada */
.btn-gpu-badge.status-connected {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.35);
  color: #6ee7b7;
}
.btn-gpu-badge.status-connected .status-indicator-dot {
  background: #10b981;
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.8);
}
.btn-gpu-badge.status-connected:hover {
  background: rgba(16, 185, 129, 0.2);
  border-color: #10b981;
}

@keyframes pulse-yellow {
  0% { transform: scale(0.95); opacity: 0.6; }
  50% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(0.95); opacity: 0.6; }
}
</style>

