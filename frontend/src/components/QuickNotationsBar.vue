<script setup>
const emit = defineEmits(['insert-notation'])

function triggerNotation(template) {
  emit('insert-notation', template)
}
</script>

<template>
  <section class="card notations-card">
    <div class="notations-header">
      <span class="title">Inserción Rápida con Timestamp Automático:</span>
      <span class="hint">Inserta en la posición del cursor de edición del bloque activo</span>
    </div>
    <div class="notations-buttons">
      <button class="btn-tag" @click="triggerNotation('(-) {ts}')" title="Palabra inaudible con timestamp">
        <strong>(-)</strong> [ts] Inaudible
      </button>
      <button class="btn-tag" @click="triggerNotation('(--) {ts}')" title="Frase o fragmento inaudible con timestamp">
        <strong>(--)</strong> [ts] Fragmento
      </button>
      <button class="btn-tag" @click="triggerNotation('(word)')" title="Palabra dudosa o deletreo incierto">
        <strong>(palabra)</strong> Dudosa
      </button>
      <button class="btn-tag" @click="triggerNotation('[os]')" title="Overlapping speech (habla solapada)">
        <strong>[os]</strong> Solapado
      </button>
      <button class="btn-tag" @click="triggerNotation('[qs]')" title="Quiet speech (habla baja / susurro)">
        <strong>[qs]</strong> Susurro / Bajo
      </button>
      <button class="btn-tag" @click="triggerNotation('[us]')" title="Unclear speech (habla poco clara)">
        <strong>[us]</strong> No claro
      </button>
      <button class="btn-tag btn-tag-underline" @click="triggerNotation('underlined')" title="Palabra con énfasis (subrayada)">
        <u>Énfasis (U)</u>
      </button>
      <button class="btn-tag" @click="triggerNotation('[brackets]')" title="Comentario del transcriptor">
        [Comentario]
      </button>
      <button class="btn-tag" @click="triggerNotation('...')" title="Declaración interrumpida o continuada">
        <strong>...</strong> Interrupción
      </button>
    </div>
  </section>
</template>

<style scoped>
.notations-card {
  background: var(--bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.5rem;
  margin-bottom: 1.5rem;
}

.notations-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
  font-size: 0.85rem;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.title {
  font-weight: 600;
  color: var(--text-main);
}

.hint {
  color: var(--text-dim);
  font-size: 0.75rem;
}

.notations-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.btn-tag {
  background: #0f172a;
  border: 1px solid var(--border);
  color: var(--text-main);
  padding: 0.4rem 0.75rem;
  border-radius: 6px;
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
}

.btn-tag:hover {
  border-color: var(--primary);
  background: #1e293b;
  color: #38bdf8;
  transform: translateY(-1px);
}

.btn-tag strong {
  color: #a5b4fc;
}

.btn-tag-underline u {
  text-decoration: underline;
  font-weight: 600;
}
</style>
