<script setup>
defineProps({
  liveLogs: {
    type: Array,
    default: () => []
  },
  isUploading: {
    type: Boolean,
    default: false
  },
  logsTerminalRef: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['cancel'])
</script>

<template>
  <div class="terminal-logs-wrapper" v-if="liveLogs.length > 0">
    <div class="terminal-header">
      <div class="terminal-controls">
        <span class="dot dot-red"></span>
        <span class="dot dot-yellow"></span>
        <span class="dot dot-green"></span>
      </div>
      <span class="terminal-title">Monitor de Logs en Tiempo Real (WhisperX + Pyannote + LLM)</span>
      <div class="terminal-actions">
        <button 
          v-if="isUploading" 
          class="btn-terminal-kill" 
          @click="emit('cancel')"
          title="Matar proceso de fondo"
        >
          ■ Detener proceso
        </button>
        <span class="terminal-status" :class="{ 'status-pulse': isUploading }">
          {{ isUploading ? '● PROCESANDO' : 'LISTO' }}
        </span>
      </div>
    </div>
    <div class="terminal-body" ref="logsTerminalRef">
      <div 
        v-for="(log, idx) in liveLogs" 
        :key="idx" 
        :class="['log-line', `log-${log.level || 'info'}`]"
      >
        <span class="log-time">[{{ log.time }}]</span>
        <span class="log-tag">[{{ (log.level || 'INFO').toUpperCase() }}]</span>
        <span class="log-text">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.terminal-logs-wrapper {
  margin-top: 1.5rem;
  border-radius: 10px;
  background: #020617;
  border: 1px solid #1e293b;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
}

.terminal-header {
  background: #0f172a;
  padding: 0.6rem 1rem;
  display: flex;
  align-items: center;
  border-bottom: 1px solid #1e293b;
  font-size: 0.8rem;
  gap: 0.75rem;
}

.terminal-controls {
  display: flex;
  gap: 0.4rem;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.dot-red { background: #ef4444; }
.dot-yellow { background: #f59e0b; }
.dot-green { background: #10b981; }

.terminal-title {
  flex: 1;
  color: #94a3b8;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
}

.terminal-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.btn-terminal-kill {
  background: #dc2626;
  border: none;
  color: white;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  font-size: 0.7rem;
  cursor: pointer;
}

.terminal-status {
  font-size: 0.7rem;
  font-weight: 700;
  color: #10b981;
}

.status-pulse {
  color: #38bdf8;
  animation: pulse 1.2s infinite;
}

@keyframes pulse {
  0% { opacity: 0.5; }
  50% { opacity: 1; }
  100% { opacity: 0.5; }
}

.terminal-body {
  padding: 0.8rem 1rem;
  max-height: 200px;
  overflow-y: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.log-line {
  display: flex;
  gap: 0.5rem;
  line-height: 1.4;
}

.log-time { color: #64748b; }
.log-tag { font-weight: 600; }
.log-info .log-tag { color: #38bdf8; }
.log-info .log-text { color: #cbd5e1; }
.log-success .log-tag, .log-success .log-text { color: #34d399; }
.log-warning .log-tag, .log-warning .log-text { color: #fbbf24; }
.log-error .log-tag, .log-error .log-text { color: #f87171; }
</style>
