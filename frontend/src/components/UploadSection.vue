<script setup>
import { ref } from 'vue'
import LiveLogsTerminal from './LiveLogsTerminal.vue'

const props = defineProps({
  isUploading: {
    type: Boolean,
    default: false
  },
  liveLogs: {
    type: Array,
    default: () => []
  },
  logsTerminalRef: {
    type: Object,
    default: null
  },
  activeJobsCount: {
    type: Number,
    default: 0
  },
  isGpuConnected: {
    type: Boolean,
    default: false
  }
})


const emit = defineEmits(['start-transcription', 'cancel-transcription', 'kill-active-jobs', 'refresh-active-jobs'])

const audioFile = ref(null)
const fileInput = ref(null)
const isDragging = ref(false)
const taskNumber = ref('Tilaus-2026-001')
const hfToken = ref(localStorage.getItem('transcriber_hf_token') || '')
const minSpeakers = ref(2)
const maxSpeakers = ref(3)

// LLM settings persisted in localStorage
const llmEnabled = ref(localStorage.getItem('transcriber_llm_enabled') !== 'false')
const llmApiUrl = ref(localStorage.getItem('transcriber_llm_api_url') || 'https://opencode.ai/zen/v1/chat/completions')
const llmApiKey = ref(localStorage.getItem('transcriber_llm_api_key') || 'sk-irNIuNSdeSi7H07dkI534EhoMgOztCbP60WmLCVfOJIhMzUeiu4ziJNSe0IAQxi5')
const llmModel = ref(localStorage.getItem('transcriber_llm_model') || 'deepseek-v4-flash-free')
const showLlmSettings = ref(false)

function handleDragOver(e) {
  e.preventDefault()
  isDragging.value = true
}

function handleDragLeave() {
  isDragging.value = false
}

function handleDrop(e) {
  e.preventDefault()
  isDragging.value = false
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    audioFile.value = e.dataTransfer.files[0]
  }
}

function handleFileInput(e) {
  if (e.target.files && e.target.files.length > 0) {
    audioFile.value = e.target.files[0]
  }
}

function toggleLlmSettings() {
  showLlmSettings.value = !showLlmSettings.value
}

function saveLlmConfig() {
  localStorage.setItem('transcriber_llm_enabled', llmEnabled.value ? 'true' : 'false')
  localStorage.setItem('transcriber_llm_api_url', llmApiUrl.value.trim())
  localStorage.setItem('transcriber_llm_api_key', llmApiKey.value.trim())
  localStorage.setItem('transcriber_llm_model', llmModel.value.trim())
}

function triggerStart() {
  if (!audioFile.value) return
  if (hfToken.value.trim()) {
    localStorage.setItem('transcriber_hf_token', hfToken.value.trim())
  }
  saveLlmConfig()

  emit('start-transcription', {
    audioFile: audioFile.value,
    taskNumber: taskNumber.value,
    hfToken: hfToken.value.trim(),
    minSpeakers: minSpeakers.value,
    maxSpeakers: maxSpeakers.value,
    llmConfig: {
      enabled: llmEnabled.value,
      apiUrl: llmApiUrl.value.trim(),
      apiKey: llmApiKey.value.trim(),
      model: llmModel.value.trim()
    }
  })
}

function resetForm() {
  audioFile.value = null
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

defineExpose({
  resetForm
})
</script>

<template>
  <section class="card upload-card">
    <div class="card-header">
      <div>
        <h2>1. Carga de Grabación y Parámetros</h2>
        <p class="section-desc">WhisperX large-v3 &bull; Diarización Pyannote &bull; Capa Semántica LLM</p>
      </div>
      <div class="header-badges">
        <span class="badge-pill">Privacidad Localhost</span>
        <span class="badge-pill badge-llm" :class="{ 'badge-llm-active': llmEnabled }">
          {{ llmEnabled ? 'LLM Semántico Activo' : 'LLM Desactivado' }}
        </span>
        <div v-if="activeJobsCount > 0" class="active-jobs-badge">
          <span class="pulse-dot"></span>
          <span class="jobs-text">{{ activeJobsCount }} proceso(s) activo(s)</span>
          <button 
            type="button" 
            class="btn-kill-orphan" 
            @click="emit('kill-active-jobs')" 
            title="Matar procesos viejos o colgados (solo se permite 1 a la vez)"
          >
            ⚡ Matar procesos viejos
          </button>
        </div>
      </div>
    </div>

    <div class="upload-grid">
      <!-- Drag & Drop Zone -->
      <div 
        class="drop-zone"
        :class="{ 'drop-zone-active': isDragging }"
        @dragover="handleDragOver"
        @dragleave="handleDragLeave"
        @drop="handleDrop"
        @click="fileInput?.click()"
      >
        <input 
          ref="fileInput" 
          type="file" 
          accept="audio/*,.mp3,.wav,.m4a,.ogg,.flac,.aac" 
          style="display: none;" 
          @change="handleFileInput" 
        />
        <div class="drop-icon">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" x2="12" y1="3" y2="15"></line>
          </svg>
        </div>
        <div class="drop-text" v-if="!audioFile">
          <p class="primary-text">Arrastra tu archivo de audio aquí o <span>explora</span></p>
          <p class="secondary-text">Soporta MP3, WAV, M4A, FLAC, OGG, AAC</p>
        </div>
        <div class="drop-selected" v-else>
          <p class="selected-name">{{ audioFile.name }}</p>
          <p class="selected-size">{{ (audioFile.size / (1024 * 1024)).toFixed(2) }} MB</p>
          <span class="change-file-hint">Haz clic para cambiar archivo</span>
        </div>
      </div>

      <!-- Settings & Form -->
      <div class="meta-inputs">
        <div class="input-group">
          <label>Number of transcription (Työnumero / Task ID)</label>
          <input 
            type="text" 
            v-model="taskNumber" 
            placeholder="Ej. Tilaus numero / Task ID" 
            class="form-control"
          />
        </div>

        <div class="input-group">
          <label>
            Hugging Face Token (Pyannote Diarization 3.1)
            <span class="label-hint">Para separación precisa de interlocutores</span>
          </label>
          <input 
            type="password" 
            v-model="hfToken" 
            placeholder="hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" 
            class="form-control"
          />
        </div>

        <div class="input-row-grid">
          <div class="input-group">
            <label>Mín. Oradores</label>
            <input 
              type="number" 
              min="1" 
              max="10" 
              v-model.number="minSpeakers" 
              class="form-control"
            />
          </div>
          <div class="input-group">
            <label>Máx. Oradores</label>
            <input 
              type="number" 
              min="1" 
              max="10" 
              v-model.number="maxSpeakers" 
              class="form-control"
            />
          </div>
        </div>

        <!-- LLM Toggle & Accordion Header -->
        <div class="llm-toggle-box">
          <div class="llm-toggle-header">
            <label class="switch-label">
              <input type="checkbox" v-model="llmEnabled" @change="saveLlmConfig" />
              <span class="switch-slider"></span>
              <span class="switch-text">Capa Semántica LLM (Roles I/R, Spanglish, "...")</span>
            </label>
            <button type="button" class="btn-link" @click="toggleLlmSettings">
              {{ showLlmSettings ? 'Ocultar config LLM' : 'Configurar LLM' }}
            </button>
          </div>

          <div v-if="showLlmSettings" class="llm-details-grid">
            <div class="input-group">
              <label>LLM Endpoint URL</label>
              <input 
                type="text" 
                v-model="llmApiUrl" 
                @input="saveLlmConfig" 
                placeholder="https://.../v1/chat/completions" 
                class="form-control form-control-sm"
              />
            </div>
            <div class="input-group">
              <label>LLM Model</label>
              <input 
                type="text" 
                v-model="llmModel" 
                @input="saveLlmConfig" 
                placeholder="deepseek-v4-flash-free o llama3:latest" 
                class="form-control form-control-sm"
              />
            </div>
            <div class="input-group full-span">
              <label>LLM API Key</label>
              <input 
                type="password" 
                v-model="llmApiKey" 
                @input="saveLlmConfig" 
                placeholder="sk-..." 
                class="form-control form-control-sm"
              />
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="action-row action-row-dual">
          <button 
            class="btn btn-primary btn-lg" 
            :disabled="!audioFile || isUploading || !isGpuConnected" 
            @click="triggerStart"
            :title="!isGpuConnected ? 'Debes conectar y validar el Worker GPU de RunPod antes de transcribir.' : 'Iniciar transcripción remota en GPU'"
          >
            <span v-if="!isUploading">
              <template v-if="isGpuConnected">🚀 Iniciar Transcripción en GPU</template>
              <template v-else>🔒 Conecta GPU RunPod para Iniciar</template>
            </span>
            <span v-else class="loading-state">
              <span class="spinner"></span>
              Procesando en GPU RunPod y aplicando LLM...
            </span>
          </button>
          <button 
            v-if="isUploading" 
            class="btn btn-danger btn-cancel-inline" 
            @click="emit('cancel-transcription')"
            title="Detener inmediatamente el proceso actual"
          >

            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <line x1="9" y1="9" x2="15" y2="15"></line>
              <line x1="15" y1="9" x2="9" y2="15"></line>
            </svg>
            Detener
          </button>
        </div>
      </div>
    </div>

    <!-- Live Terminal Component -->
    <LiveLogsTerminal
      :live-logs="liveLogs"
      :is-uploading="isUploading"
      :logs-terminal-ref="logsTerminalRef"
      @cancel="emit('cancel-transcription')"
    />
  </section>
</template>

<style scoped>
.upload-card {
  margin-bottom: 1.5rem;
}

.upload-card .card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.5rem;
  gap: 1rem;
}

.upload-card h2 {
  font-size: 1.15rem;
  font-weight: 700;
  color: var(--text-main);
}

.section-desc {
  font-size: 0.8rem;
  color: var(--text-muted);
  margin-top: 0.2rem;
}

.header-badges {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}


.badge-llm {
  background: rgba(99, 102, 241, 0.15);
  color: #a5b4fc;
  border-color: rgba(99, 102, 241, 0.3);
}

.badge-llm-active {
  background: rgba(6, 182, 212, 0.15);
  color: #38bdf8;
  border-color: rgba(6, 182, 212, 0.35);
}

.upload-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
}

@media (max-width: 900px) {
  .upload-grid {
    grid-template-columns: 1fr;
  }
}

.drop-zone {
  border: 2px dashed rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  padding: 2.5rem 1.5rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: rgba(15, 23, 42, 0.4);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.drop-zone:hover, .drop-zone-active {
  border-color: var(--primary);
  background: rgba(99, 102, 241, 0.05);
}

.drop-icon {
  color: var(--text-muted);
  margin-bottom: 1rem;
}

.drop-text .primary-text {
  font-weight: 500;
  margin-bottom: 0.3rem;
}

.drop-text .primary-text span {
  color: var(--primary);
  text-decoration: underline;
}

.drop-text .secondary-text {
  font-size: 0.8rem;
  color: var(--text-dim);
}

.drop-selected .selected-name {
  font-weight: 600;
  color: #38bdf8;
  word-break: break-all;
}

.drop-selected .selected-size {
  font-size: 0.8rem;
  color: var(--text-muted);
  margin-top: 0.25rem;
}

.change-file-hint {
  display: inline-block;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: var(--text-dim);
  text-decoration: underline;
}

.meta-inputs {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.input-group label {
  display: block;
  font-size: 0.825rem;
  color: var(--text-muted);
  margin-bottom: 0.35rem;
  font-weight: 500;
}

.label-hint {
  display: block;
  font-size: 0.75rem;
  color: #94a3b8;
  font-weight: normal;
}

.input-row-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.form-control {
  width: 100%;
  background: #0f172a;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.65rem 0.85rem;
  color: white;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.15s;
}

.form-control:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px var(--primary-glow);
}

.form-control-sm {
  padding: 0.45rem 0.65rem;
  font-size: 0.8rem;
}

.llm-toggle-box {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem;
}

.llm-toggle-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.switch-label {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  cursor: pointer;
  user-select: none;
}

.switch-label input {
  display: none;
}

.switch-slider {
  width: 34px;
  height: 18px;
  background: #334155;
  border-radius: 12px;
  position: relative;
  transition: background 0.2s;
}

.switch-slider::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: white;
  transition: transform 0.2s;
}

.switch-label input:checked + .switch-slider {
  background: var(--primary);
}

.switch-label input:checked + .switch-slider::after {
  transform: translateX(16px);
}

.switch-text {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--text-main);
}

.btn-link {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 0.75rem;
  cursor: pointer;
  text-decoration: underline;
}

.llm-details-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.6rem;
  margin-top: 0.75rem;
  padding-top: 0.75rem;
  border-top: 1px solid var(--border);
}

.full-span {
  grid-column: 1 / -1;
}

.action-row-dual {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
}

.btn-lg {
  padding: 0.85rem 1.5rem;
  font-size: 0.95rem;
  justify-content: center;
}

.btn-cancel-inline {
  padding: 0.85rem 1.2rem;
}

.spinner {
  display: inline-block;
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  border-top-color: #fff;
  animation: spin 0.8s linear infinite;
  margin-right: 0.5rem;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
