<script setup>
import { nextTick } from 'vue'

const props = defineProps({
  blocks: {
    type: Array,
    required: true
  },
  currentTime: {
    type: Number,
    default: 0
  },
  activeTextareaIndex: {
    type: Number,
    default: null
  },
  originalFilename: {
    type: String,
    default: ''
  },
  durationSeconds: {
    type: Number,
    default: 0
  },
  taskNumber: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update-active-textarea', 'seek-to', 'update-block-text', 'update-block-speaker'])

const speakerOptions = ['I', 'R', 'R1', 'R2', 'R3', 'R4']

function formatSeconds(secs) {
  if (isNaN(secs) || secs === null) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

function isWordActive(word) {
  return props.currentTime >= word.start && props.currentTime <= word.end
}

function isBlockActive(block) {
  return props.currentTime >= block.start && props.currentTime <= block.end
}

function onBlockClick(startTime) {
  emit('seek-to', startTime)
}

function onWordClick(startTime) {
  emit('seek-to', startTime)
}

function onFocusTextarea(index) {
  emit('update-active-textarea', index)
}
</script>

<template>
  <section class="transcript-editor-container">
    <!-- Meta Summary Bar -->
    <div class="meta-summary-bar">
      <div class="meta-details">
        <span><strong>Grabación:</strong> {{ originalFilename || 'audio.mp3' }}</span>
        <span class="sep">&bull;</span>
        <span><strong>Duración:</strong> {{ Math.ceil(durationSeconds / 60) }} min</span>
        <span class="sep">&bull;</span>
        <span><strong>Tarea:</strong> {{ taskNumber || '--' }}</span>
      </div>
      <div class="spec-reminder">
        Exportación estricta: <strong>Verdana 8pt</strong> &bull; Línea en blanco entre oradores
      </div>
    </div>

    <!-- Blocks List -->
    <div class="blocks-list">
      <div 
        v-for="(block, bIndex) in blocks" 
        :key="block.id"
        :class="['block-card', { 'block-active': isBlockActive(block) }]"
      >
        <div class="block-meta">
          <!-- Speaker Selection -->
          <div class="speaker-select-wrap">
            <label class="speaker-label">Orador:</label>
            <select 
              v-model="block.speaker" 
              class="speaker-select"
              @change="emit('update-block-speaker', { index: bIndex, speaker: block.speaker })"
            >
              <option v-for="spk in speakerOptions" :key="spk" :value="spk">
                {{ spk }}: ({{ spk === 'I' ? 'Interviewer' : (spk === 'R' ? 'Respondent' : `Respondent ${spk.slice(1)}`) }})
              </option>
            </select>
          </div>

          <!-- Timestamp Badge -->
          <div class="block-timestamps" @click="onBlockClick(block.start)">
            <span class="ts-badge" title="Haz clic para saltar a este bloque">
              ▶ {{ formatSeconds(block.start) }} - {{ formatSeconds(block.end) }}
            </span>
          </div>
        </div>

        <!-- Karaoke Interactive Text Display -->
        <div class="karaoke-view" v-if="block.words && block.words.length > 0">
          <span 
            v-for="(wObj, wIndex) in block.words" 
            :key="wIndex"
            :class="['karaoke-word', { 'word-active': isWordActive(wObj) }]"
            @click="onWordClick(wObj.start)"
            :title="`Saltar a ${wObj.start}s`"
          >
            {{ wObj.word }}
          </span>
        </div>

        <!-- Editable Textarea for hot edits -->
        <div class="block-textarea-wrap">
          <textarea 
            :id="`textarea-block-${block.id}`"
            v-model="block.text" 
            class="block-textarea"
            rows="3"
            @focus="onFocusTextarea(bIndex)"
            @input="emit('update-block-text', { index: bIndex, text: block.text })"
            placeholder="Edita o transcribe la intervención..."
          ></textarea>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.transcript-editor-container {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.meta-summary-bar {
  background: var(--bg-card);
  border: 1px solid var(--border);
  padding: 0.75rem 1.25rem;
  border-radius: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.85rem;
  color: var(--text-muted);
  flex-wrap: wrap;
  gap: 0.75rem;
}

.meta-details {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.meta-details strong {
  color: var(--text-main);
}

.sep {
  color: var(--text-dim);
}

.spec-reminder {
  font-size: 0.8rem;
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
  padding: 0.25rem 0.6rem;
  border-radius: 4px;
  border: 1px solid rgba(56, 189, 248, 0.2);
}

.blocks-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.block-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.25rem;
  transition: all 0.2s ease;
}

.block-card:hover {
  border-color: rgba(255, 255, 255, 0.15);
}

.block-active {
  border-color: var(--karaoke-active);
  box-shadow: 0 0 15px rgba(56, 189, 248, 0.15);
  background: rgba(18, 24, 38, 0.95);
}

.block-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.85rem;
}

.speaker-select-wrap {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.speaker-label {
  font-size: 0.8rem;
  color: var(--text-muted);
  font-weight: 600;
}

.speaker-select {
  background: #0f172a;
  color: #38bdf8;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.35rem 0.6rem;
  font-size: 0.85rem;
  font-weight: 700;
  outline: none;
  cursor: pointer;
}

.speaker-select:focus {
  border-color: var(--primary);
}

.block-timestamps {
  cursor: pointer;
}

.ts-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  background: #0f172a;
  border: 1px solid var(--border);
  padding: 0.3rem 0.6rem;
  border-radius: 6px;
  color: var(--text-muted);
  transition: all 0.15s;
}

.ts-badge:hover {
  background: #1e293b;
  color: #38bdf8;
  border-color: #38bdf8;
}

.karaoke-view {
  background: rgba(15, 23, 42, 0.6);
  padding: 0.75rem;
  border-radius: 8px;
  margin-bottom: 0.85rem;
  line-height: 1.8;
  font-size: 0.95rem;
}

.karaoke-word {
  display: inline-block;
  padding: 0.05rem 0.2rem;
  margin: 0 0.1rem;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.1s ease;
  color: var(--text-main);
}

.karaoke-word:hover {
  background: rgba(255, 255, 255, 0.1);
}

.word-active {
  background: var(--karaoke-bg);
  color: var(--karaoke-active);
  font-weight: 700;
  text-shadow: 0 0 8px rgba(56, 189, 248, 0.5);
  transform: scale(1.04);
}

.block-textarea-wrap {
  width: 100%;
}

.block-textarea {
  width: 100%;
  background: #0f172a;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem 1rem;
  color: var(--text-main);
  font-size: 0.95rem;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s;
}

.block-textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px var(--primary-glow);
}
</style>
