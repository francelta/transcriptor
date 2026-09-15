import json
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """Eres un transcriptor profesional especializado en investigación académica.
Tu tarea es formatear y enriquecer la transcripción literal que recibes respetando ESTRICTAMENTE las siguientes reglas:

1. BILINGÜISMO ESTRICTO (PROHIBIDO TRADUCIR):
   - Si una persona habla en inglés, transcribe LITERALMENTE en inglés.
   - Si una persona habla en español, transcribe LITERALMENTE en español.
   - Si hay mezcla (Spanglish o code-switching), mantén exactamente cada palabra en su idioma original.
   - BAJO NINGÚN CONCEPTO traduzcas el inglés al español ni viceversa.

2. ASIGNACIÓN DE ORADORES:
   - Identifica a la entrevistadora investigadora como "I".
   - Identifica a los entrevistados/asistentes como "R1", "R2" (o "R" si es un único entrevistado).
   - NUNCA asignes etiquetas de orador a pausas, ruidos de fondo o silencios.
   - Corrige etiquetas genéricas (e.g. SPEAKER_00, SPEAKER_01) a su rol conversacional correspondiente.

3. NOTACIONES REGLAMENTARIAS:
   - [...] o ... : Pausa prolongada o intervención interrumpida antes del siguiente cambio de orador.
   - [os] : Habla solapada (Overlapping speech).
   - [qs] : Voz baja o susurro (Quiet speech).
   - [us] : Habla poco clara o ininteligible (Unclear speech).
   - (-) : Palabra omitida o inaudible.
   - (--) : Frase o fragmento omitido o inaudible.
   - (palabra) : Palabra dudosa o deletreo incierto.
   - <u>palabra</u> : Palabra o segmento con especial énfasis tonal.

4. FIDELIDAD LITERAL Y CONSERVACIÓN:
   - No resumas, no corrijas sintaxis coloquial y no omitas muletillas.
   - CRÍTICO: NO alteres los timestamps ("start", "end", "id"). Consérvalos exactamente iguales.
   - Devuelve ÚNICAMENTE un array JSON válido con la misma lista de bloques, actualizando "speaker" a la etiqueta asignada ("I", "R1", "R2", etc.) y "text" con el contenido literal formateado según estas reglas.
"""


def normalize_speaker_label(raw_speaker):
    """Normaliza SPEAKER_00 -> I, SPEAKER_01 -> R, SPEAKER_02 -> R2, etc."""
    if not raw_speaker:
        return 'I'
    clean = str(raw_speaker).strip()
    if clean.startswith('SPEAKER_'):
        try:
            spk_num = int(clean.split('_')[-1])
            if spk_num == 0:
                return 'I'
            elif spk_num == 1:
                return 'R'
            else:
                return f'R{spk_num}'
        except Exception:
            return clean
    return clean


def map_speakers_by_language_and_role(blocks):
    """
    Mapea los oradores dinámicos (SPEAKER_00, SPEAKER_01, SPEAKER_02) a las etiquetas canónicas:
    - Si el interlocutor interviene principalmente en inglés o formula preguntas: 'I' (Interviewer).
    - Si el interlocutor responde principalmente en español: 'R1', 'R2' (Respondents).
    - Si no hay suficiente información lingüística, asigna orden canónico (SPEAKER_00 -> I, etc.).
    Preserva el bilingüismo estricto: NUNCA traduce ni modifica las palabras originales.
    """
    import re
    if not blocks:
        return {}

    speaker_stats = {}
    for block in blocks:
        raw_spk = str(block.get('speaker') or 'SPEAKER_00').strip()
        if raw_spk not in speaker_stats:
            speaker_stats[raw_spk] = {
                'total_words': 0,
                'en_indicators': 0,
                'es_indicators': 0,
                'question_count': 0,
                'first_appearance': len(speaker_stats)
            }

        text = str(block.get('text', '')).strip()
        lang = str(block.get('language', '')).lower().strip()

        # Conteo de preguntas
        if '?' in text:
            speaker_stats[raw_spk]['question_count'] += text.count('?')

        # Palabras indicadoras en inglés y español
        words = re.findall(r'\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]+\b', text.lower())
        speaker_stats[raw_spk]['total_words'] += len(words)

        if lang == 'en':
            speaker_stats[raw_spk]['en_indicators'] += len(words) or 5
        elif lang == 'es':
            speaker_stats[raw_spk]['es_indicators'] += len(words) or 5

        en_vocab = {'the', 'and', 'is', 'you', 'what', 'how', 'why', 'who', 'where', 'when', 'which',
                    'would', 'could', 'should', 'can', 'are', 'in', 'on', 'at', 'responsible', 'tell', 'me'}
        es_vocab = {'de', 'los', 'las', 'el', 'la', 'en', 'es', 'un', 'una', 'que', 'quien', 'quién',
                    'por', 'para', 'con', 'responsable', 'invernaderos', 'sería', 'comunidad', 'nosotros'}

        for w in words:
            if w in en_vocab:
                speaker_stats[raw_spk]['en_indicators'] += 1
            if w in es_vocab:
                speaker_stats[raw_spk]['es_indicators'] += 1

    # Identificar cuál orador es principalmente 'I' (Interviewer)
    # Criterios: mayor proporción de inglés o mayor formulación de preguntas
    speaker_scores_for_i = {}
    for spk, stats in speaker_stats.items():
        en_score = stats['en_indicators']
        q_score = stats['question_count'] * 3
        # Si el orador ya se llama 'I' o 'SPEAKER_00' tiene peso de inicio
        bias = 2 if (spk == 'I' or spk == 'SPEAKER_00') else 0
        speaker_scores_for_i[spk] = (en_score * 2) + q_score + bias

    # Elegir el mejor candidato a 'I'
    interviewer_candidate = max(speaker_scores_for_i, key=speaker_scores_for_i.get) if speaker_scores_for_i else None

    # Mapear los roles
    speaker_map = {}
    if interviewer_candidate:
        speaker_map[interviewer_candidate] = 'I'

    r_counter = 1
    # Asignar R1, R2, etc. al resto por orden de aparición
    for spk in sorted(speaker_stats.keys(), key=lambda s: speaker_stats[s]['first_appearance']):
        if spk != interviewer_candidate:
            speaker_map[spk] = f'R{r_counter}'
            r_counter += 1

    return speaker_map


def fallback_deterministic_processor(blocks):
    """
    Fallback determinista en Python cuando el LLM falla o no está disponible.
    Preserva exactamente la estructura de bloques y timestamps, mapeando de forma segura
    los identificadores de orador según idioma (inglés/preguntas -> I, español -> R1, R2).
    Fidelidad literal: NUNCA traduce ni altera el texto original.
    """
    logger.info("Aplicando fallback determinista con mapeo de oradores basado en roles e idioma.")
    cleaned_blocks = []
    speaker_map = map_speakers_by_language_and_role(blocks)

    for block in blocks:
        raw_spk = str(block.get('speaker', '')).strip()
        assigned_speaker = speaker_map.get(raw_spk, normalize_speaker_label(raw_spk))
        
        # Copiar y preservar todas las propiedades y timestamps
        new_block = dict(block)
        new_block['speaker'] = assigned_speaker
        # Limpieza básica de espacios sin filtros destructivos
        new_block['text'] = str(block.get('text', '')).strip()
        cleaned_blocks.append(new_block)

    return cleaned_blocks


def process_transcript_with_llm(blocks, hf_token=None, llm_config=None):
    """
    Envía los bloques transcritos por WhisperX/Pyannote al LLM configurado (OpenAI-compatible)
    para resolver semánticamente roles de oradores (I, R, R1, etc.), corregir code-switching
    y puntuar interrupciones con '...', manteniendo estrictamente idénticos los timestamps.
    """
    if not blocks:
        return []

    # Configuración del LLM
    config = llm_config or {}
    api_url = config.get('api_url') or getattr(settings, 'LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
    api_key = config.get('api_key') or getattr(settings, 'LLM_API_KEY', '')
    model = config.get('model') or getattr(settings, 'LLM_MODEL', 'llama3:latest')
    enabled = config.get('enabled', True)

    if not enabled or not api_url:
        logger.info("Procesamiento LLM deshabilitado o URL no configurada. Usando fallback.")
        return fallback_deterministic_processor(blocks)

    # Preparar payload simplificado para el LLM (ahorra tokens y reduce alucinación)
    simplified_input = []
    for b in blocks:
        simplified_input.append({
            "id": b.get("id"),
            "speaker": b.get("speaker"),
            "start": b.get("start"),
            "end": b.get("end"),
            "text": b.get("text", "")
        })

    user_prompt = f"Transcription blocks to process:\n{json.dumps(simplified_input, ensure_ascii=False, indent=2)}"

    headers = {
        "Content-Type": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "temperature": 0.15,
        "messages": [
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
    }

    # Intentar estructuración JSON nativa si el endpoint lo soporta
    # Algunos endpoints compatibles con OpenAI admiten response_format: {"type": "json_object"}
    # pero como el prompt pide un array JSON directamente, verificamos la respuesta parseada.
    try:
        logger.info(f"Llamando a LLM en {api_url} con modelo {model} para {len(blocks)} bloques...")
        resp = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120  # Timeout generoso para transcripciones extensas
        )

        if resp.status_code != 200:
            logger.warning(f"Respuesta no exitosa del LLM ({resp.status_code}): {resp.text[:300]}")
            return fallback_deterministic_processor(blocks)

        data = resp.json()
        raw_content = ""
        if "choices" in data and len(data["choices"]) > 0:
            raw_content = data["choices"][0].get("message", {}).get("content", "")
        elif "response" in data:  # Formato nativo Ollama /api/generate
            raw_content = data.get("response", "")

        raw_content = raw_content.strip()
        if not raw_content:
            logger.warning("Contenido vacío recibido del LLM. Aplicando fallback.")
            return fallback_deterministic_processor(blocks)

        # Extraer array JSON si viene envuelto en markdown (ej. ```json ... ```)
        if "```" in raw_content:
            parts = raw_content.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("[") and candidate.endswith("]"):
                    raw_content = candidate
                    break

        # Limpiar posibles caracteres extra antes de '[' y después de ']'
        start_bracket = raw_content.find("[")
        end_bracket = raw_content.rfind("]")
        if start_bracket != -1 and end_bracket != -1 and end_bracket > start_bracket:
            raw_content = raw_content[start_bracket:end_bracket+1]

        processed_array = json.loads(raw_content)

        if not isinstance(processed_array, list):
            logger.warning("La salida del LLM no es un array JSON. Aplicando fallback.")
            return fallback_deterministic_processor(blocks)

        # Mapear los resultados preservando las marcas de tiempo originales y estructura completa
        result_by_id = {item.get("id"): item for item in processed_array if isinstance(item, dict) and "id" in item}

        final_blocks = []
        for orig_block in blocks:
            b_id = orig_block.get("id")
            llm_item = result_by_id.get(b_id)

            new_block = dict(orig_block)
            if llm_item:
                new_spk = str(llm_item.get("speaker", orig_block.get("speaker", "I"))).strip()
                new_text = str(llm_item.get("text", orig_block.get("text", ""))).strip()
                # Regla 4: CRITICAL: DO NOT alter timestamps
                new_block["speaker"] = new_spk if new_spk else orig_block.get("speaker", "I")
                new_block["text"] = new_text if new_text else orig_block.get("text", "")
            else:
                # Si el LLM omitió un bloque, normalizamos de forma determinista
                new_block["speaker"] = orig_block.get("speaker", "I")
                new_block["text"] = orig_block.get("text", "")

            final_blocks.append(new_block)

        logger.info(f"Post-procesado LLM completado con éxito para {len(final_blocks)} bloques.")
        return final_blocks

    except Exception as e:
        logger.error(f"Excepción durante la llamada o parsing del LLM: {e}. Activando fallback determinista.")
        return fallback_deterministic_processor(blocks)
