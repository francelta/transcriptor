<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  audioUrl: {
    type: String,
    required: true
  }
})

const emit = defineEmits(['timeupdate', 'seek-to'])

const audioElement = ref(null)
const isPlaying = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const playbackRate = ref(1.0)
const availableRates = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75]

function formatSeconds(secs) {
  if (isNaN(secs) || secs === null) return '0:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

function togglePlay() {
  if (!audioElement.value) return
  if (audioElement.value.paused) {
    audioElement.value.play()
    isPlaying.value = true
  } else {
    audioElement.value.pause()
    isPlaying.value = false
  }
}

function stopAudio() {
  if (!audioElement.value) return
  audioElement.value.pause()
  audioElement.value.currentTime = 0
  isPlaying.value = false
  emit('timeupdate', 0)
}

function restartAudio() {
  if (!audioElement.value) return
  audioElement.value.currentTime = 0
  audioElement.value.play()
  isPlaying.value = true
}

function seekRelative(offset) {
  if (!audioElement.value) return
  const newTime = Math.max(0, Math.min(duration.value, audioElement.value.currentTime + offset))
  audioElement.value.currentTime = newTime
  emit('timeupdate', newTime)
}

function setRate(rate) {
  playbackRate.value = rate
  if (audioElement.value) {
    audioElement.value.playbackRate = rate
  }
}

function seekTo(time) {
  if (!audioElement.value) return
  audioElement.value.currentTime = time
  if (audioElement.value.paused) {
    audioElement.value.play()
    isPlaying.value = true
  }
  emit('timeupdate', time)
}

function onTimeUpdate() {
  if (audioElement.value) {
    currentTime.value = audioElement.value.currentTime
    emit('timeupdate', currentTime.value)
  }
}

function onLoadedMetadata() {
  if (audioElement.value) {
    duration.value = audioElement.value.duration
    audioElement.value.playbackRate = playbackRate.value
  }
}

function onEnded() {
  isPlaying.value = false
}

function onSeekInput(e) {
  const time = parseFloat(e.target.value)
  if (audioElement.value) {
    audioElement.value.currentTime = time
    currentTime.value = time
    emit('timeupdate', time)
  }
}

defineExpose({
  togglePlay,
  stopAudio,
  restartAudio,
  seekRelative,
  seekTo,
  getCurrentTime: () => currentTime.value,
  audioElement
})
</script>

<template>
  <section class="card player-card">
    <audio 
      ref="audioElement"
      :src="audioUrl"
      @timeupdate="onTimeUpdate"
      @loadedmetadata="onLoadedMetadata"
      @ended="onEnded"
      preload="metadata"
    ></audio>

    <div class="player-top-row">
      <!-- Transport Controls -->
      <div class="transport-controls">
        <button class="icon-btn" title="Reiniciar (Inicio)" @click="restartAudio">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="19 20 9 12 19 4 19 20"></polygon>
            <line x1="5" x2="5" y1="19" y2="5"></line>
          </svg>
        </button>

        <button class="icon-btn" title="Retroceder 3s (Ctrl + ←)" @click="seekRelative(-3)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M1 4v6h6"></path>
            <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
          </svg>
          <span class="btn-subtext">3s</span>
        </button>

        <button class="play-btn" :title="isPlaying ? 'Pausa (Espacio)' : 'Reproducir (Espacio)'" @click="togglePlay">
          <svg v-if="!isPlaying" width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
          <svg v-else width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="4" width="4" height="16"></rect>
            <rect x="14" y="4" width="4" height="16"></rect>
          </svg>
        </button>

        <button class="icon-btn" title="Avanzar 3s (Ctrl + →)" @click="seekRelative(3)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6"></path>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
          <span class="btn-subtext">3s</span>
        </button>

        <button class="icon-btn" title="Detener (Stop)" @click="stopAudio">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12"></rect>
          </svg>
        </button>
      </div>

      <!-- Time Display -->
      <div class="time-display">
        <span class="current-time">{{ formatSeconds(currentTime) }}</span>
        <span class="time-sep">/</span>
        <span class="total-duration">{{ formatSeconds(duration) }}</span>
      </div>

      <!-- Extended Playback Rates -->
      <div class="rate-selector-container">
        <span class="rate-label">Velocidad:</span>
        <div class="rate-pills">
          <button 
            v-for="r in availableRates" 
            :key="r"
            :class="['rate-pill', { active: playbackRate === r }]"
            @click="setRate(r)"
          >
            {{ r }}x
          </button>
        </div>
      </div>
    </div>

    <!-- Interactive Progress / Scrub Bar -->
    <div class="scrub-container">
      <input 
        type="range" 
        class="scrubber" 
        min="0" 
        :max="duration || 100" 
        step="0.1" 
        :value="currentTime" 
        @input="onSeekInput" 
      />
    </div>
  </section>
</template>

<style scoped>
.player-card {
  position: sticky;
  top: 4.8rem;
  z-index: 90;
  background: var(--bg-card-solid);
  border: 1px solid var(--border-focus);
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.4);
  padding: 1rem 1.5rem;
  border-radius: 12px;
  margin-bottom: 1rem;
}

.player-top-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
}

.transport-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.icon-btn {
  background: #1e293b;
  border: 1px solid var(--border);
  color: var(--text-main);
  width: 38px;
  height: 38px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  transition: all 0.15s;
}

.icon-btn:hover {
  background: #334155;
  color: white;
}

.btn-subtext {
  font-size: 0.6rem;
  position: absolute;
  bottom: 2px;
  font-weight: 700;
}

.play-btn {
  background: var(--primary);
  color: white;
  border: none;
  width: 46px;
  height: 46px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s;
  box-shadow: 0 4px 14px var(--primary-glow);
}

.play-btn:hover {
  background: var(--primary-hover);
  transform: scale(1.05);
}

.time-display {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1rem;
  font-weight: 600;
  display: flex;
  gap: 0.4rem;
  align-items: center;
}

.current-time {
  color: #38bdf8;
}

.time-sep {
  color: var(--text-dim);
}

.total-duration {
  color: var(--text-muted);
}

.rate-selector-container {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.rate-label {
  font-size: 0.8rem;
  color: var(--text-muted);
  font-weight: 500;
}

.rate-pills {
  display: flex;
  gap: 0.25rem;
  background: #0f172a;
  padding: 0.25rem;
  border-radius: 8px;
  border: 1px solid var(--border);
  flex-wrap: wrap;
}

.rate-pill {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 0.75rem;
  padding: 0.25rem 0.45rem;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.1s;
  font-weight: 600;
}

.rate-pill:hover {
  color: white;
  background: #1e293b;
}

.rate-pill.active {
  background: var(--primary);
  color: white;
}

.scrub-container {
  margin-top: 0.85rem;
}

.scrubber {
  width: 100%;
  height: 6px;
  -webkit-appearance: none;
  background: #1e293b;
  border-radius: 3px;
  outline: none;
  cursor: pointer;
}

.scrubber::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #38bdf8;
  cursor: pointer;
  box-shadow: 0 0 8px #38bdf8;
  transition: transform 0.1s;
}

.scrubber::-webkit-slider-thumb:hover {
  transform: scale(1.2);
}
</style>
