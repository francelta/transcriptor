import os
import io
import datetime
import math
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

from .models import TranscriptionJob
from .serializers import TranscriptionJobSerializer
from .services.llm_processor import (
    process_transcript_with_llm,
    map_speakers_by_language_and_role,
    normalize_speaker_label
)

import gc
import threading
import json
import time
import logging
import requests
from django.conf import settings
from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)

# Constantes de ejecución optimizadas para CPU y estabilidad de memoria (16 GB RAM)
WHISPERX_DEVICE = getattr(settings, 'WHISPERX_DEVICE', 'cpu')
WHISPERX_BATCH_SIZE = getattr(settings, 'WHISPERX_BATCH_SIZE', 4)
# compute_type='int8' ejecuta el mismo modelo large-v3 con vectorización AVX2 de alta velocidad en CPU
WHISPERX_COMPUTE_TYPE = getattr(settings, 'WHISPERX_COMPUTE_TYPE', 'int8')
WHISPERX_MODEL_NAME = getattr(settings, 'WHISPERX_MODEL_NAME', 'large-v3')

# Control de cancelación en memoria
_active_jobs_cancel_flags = {}
_jobs_lock = threading.Lock()


def normalize_speaker_label(raw_speaker):
    """
    Normaliza el identificador de orador devuelto por la diarización
    a la nomenclatura estándar de la transcripción (I, R, R1, R2, etc.)
    o preserva etiquetas si ya vienen formateadas.
    """
    if not raw_speaker:
        return 'I'
    clean = str(raw_speaker).strip()
    if clean.startswith('SPEAKER_'):
        # Mapea SPEAKER_00 -> I, SPEAKER_01 -> R, SPEAKER_02 -> R2, etc.
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


import threading
import json
import time
from django.http import StreamingHttpResponse

def append_job_log(job_id, message, level='info'):
    """Registra un evento con timestamp para el trabajo en la BD y en consola"""
    try:
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = {
            'time': now_str,
            'message': message,
            'level': level
        }
        print(f"[{now_str}] [{level.upper()}] [Job {job_id}]: {message}")
        job = TranscriptionJob.objects.get(pk=job_id)
        current_logs = list(job.logs or [])
        current_logs.append(log_entry)
        job.logs = current_logs
        job.save(update_fields=['logs'])
    except Exception as e:
        print(f"Error guardando log para job {job_id}: {e}")


def stop_runpod_instance(job_id=None):
    """
    Ejecuta el apagado automático inmediato de la instancia de RunPod
    para congelar la facturación de GPU.
    """
    pod_id = getattr(settings, 'RUNPOD_POD_ID', '')
    api_key = getattr(settings, 'RUNPOD_API_KEY', '')

    if pod_id and api_key and api_key != 'PEGA_AQUÍ_TU_API_KEY':
        try:
            stop_url = f"https://rest.runpod.io/v1/pods/{pod_id}/stop"
            resp = requests.post(
                stop_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5
            )
            msg = f"RunPod [{pod_id}] apagado automáticamente con éxito (Status: {resp.status_code}). Facturación congelada."
            if job_id:
                append_job_log(job_id, msg, "success")
            else:
                print(msg)
        except Exception as e:
            err = f"Error apagando RunPod [{pod_id}]: {e}"
            logger.warning(err)
            if job_id:
                append_job_log(job_id, err, "warning")
            else:
                print(err)
    else:
        msg = "Auto-apagado RunPod omitido: RUNPOD_POD_ID o RUNPOD_API_KEY no están configurados con una clave válida."
        if job_id:
            append_job_log(job_id, msg, "info")
        else:
            print(msg)


def execute_whisperx_pipeline(job_id, hf_token, min_speakers, max_speakers, llm_config=None):
    """
    Ejecuta la transcripción delegando de forma estricta y exclusiva en el worker remoto GPU de RunPod.
    Política estricta ZERO CPU FALLBACK: Prohibición absoluta de ejecutar modelos locales en CPU.
    Si el worker remoto o el túnel fallan, se aborta inmediatamente con error descriptivo.
    Timeout global de llamadas al worker: 900 segundos.
    """
    from .models import RunPodConfig
    from .services.runpod_orchestrator import RunPodOrchestrator

    try:
        job = TranscriptionJob.objects.get(pk=job_id)
        audio_path = job.audio_file.path
        config = RunPodConfig.get_solo()

        local_port = config.local_proxy_port or 8005
        pod_host = (config.pod_host or '').strip()
        pod_ssh_port = config.pod_ssh_port or 22
        ssh_key_path = config.ssh_key_path or '~/.ssh/id_rsa'
        effective_hf_token = (hf_token or config.hf_token or '').strip()

        orchestrator = RunPodOrchestrator()

        append_job_log(job_id, f"Verificando disponibilidad de worker remoto GPU (localhost:{local_port})...", "info")

        # Asegurar túnel y worker activo si hay configuración de host
        if pod_host:
            status_check = orchestrator.ensure_worker_ready(
                host=pod_host,
                ssh_port=pod_ssh_port,
                ssh_key_path=ssh_key_path,
                local_port=local_port,
                hf_token=effective_hf_token
            )
            if status_check.get("status") not in ["connected"]:
                err_msg = status_check.get("error", "No se pudo conectar o aprovisionar el worker GPU.")
                append_job_log(job_id, f"ERROR: Fallo conectando con el worker GPU: {err_msg}", "error")
                raise RuntimeError(f"Fallo de conexión con worker remoto GPU: {err_msg}. Transcripción abortada (Zero CPU Fallback).")
        else:
            # Si no hay pod_host configurado, verificar si al menos el proxy ya responde
            if not orchestrator.check_health(local_port):
                raise RuntimeError(
                    f"No hay host de Pod configurado y el worker en localhost:{local_port} no responde. "
                    "Por favor, configura la IP del Pod o detecta automáticamente antes de transcribir."
                )

        gpu_worker_url = f"http://127.0.0.1:{local_port}/transcribe"
        append_job_log(job_id, f"Worker GPU listo. Enviando audio a {gpu_worker_url} (Timeout: 900s)...", "info")

        content_type = 'audio/mpeg'
        ext = os.path.splitext(job.original_filename)[1].lower()
        if ext in ['.wav']:
            content_type = 'audio/wav'
        elif ext in ['.ogg']:
            content_type = 'audio/ogg'
        elif ext in ['.m4a']:
            content_type = 'audio/mp4'
        elif ext in ['.flac']:
            content_type = 'audio/flac'

        with open(audio_path, 'rb') as af:
            files = {'file': (job.original_filename, af.read(), content_type)}
        data = {
            'min_speakers': min_speakers,
            'max_speakers': max_speakers,
            'hf_token': effective_hf_token
        }

        append_job_log(job_id, f"Transcribiendo y diarizando en GPU RunPod (oradores: {min_speakers}-{max_speakers})...", "info")
        
        try:
            resp = requests.post(gpu_worker_url, files=files, data=data, timeout=900)
            if resp.status_code != 200:
                raise RuntimeError(f"El worker GPU remoto devolvió código de error HTTP {resp.status_code}: {resp.text}")
            worker_response = resp.json()
        except requests.Timeout:
            raise RuntimeError("La llamada de transcripción remota expiró tras 900 segundos (Timeout).")
        except requests.RequestException as req_err:
            raise RuntimeError(f"Error de comunicación HTTP con el worker GPU remoto ({gpu_worker_url}): {req_err}")

        append_job_log(job_id, "Transcripción GPU recibida exitosamente del worker de RunPod.", "success")

        # Auto-apagado opcional si está configurada la API de RunPod
        stop_runpod_instance(job_id=job_id)

        # Extraer segmentos y metadatos devueltos por el worker
        if isinstance(worker_response, dict):
            result = worker_response
            if 'duration' in worker_response:
                job.duration_seconds = round(float(worker_response['duration']), 2)
                job.save(update_fields=['duration_seconds'])
        elif isinstance(worker_response, list):
            result = {'segments': worker_response}
        else:
            result = {'segments': []}


        # -------------------------------------------------------------
        # CONSTRUCCIÓN DE BLOQUES COMPATIBLES CON KARAOKE Y FRONTEND
        # -------------------------------------------------------------
        blocks = []
        all_words = []
        block_idx = 1
        last_speaker = 'SPEAKER_00'

        raw_segments = result.get("segments", []) if isinstance(result, dict) else []
        for seg in raw_segments:
            text = seg.get("text", "").strip()
            if not text:
                continue

            raw_spk = seg.get("speaker") or last_speaker
            last_speaker = raw_spk

            seg_words = []
            for w in seg.get("words", []):
                w_text = w.get("word", "").strip()
                w_start = w.get("start")
                w_end = w.get("end")

                start_val = round(float(w_start), 2) if w_start is not None else round(float(seg.get("start", 0.0)), 2)
                end_val = round(float(w_end), 2) if w_end is not None else round(float(seg.get("end", start_val + 0.3)), 2)
                w_speaker = w.get("speaker", raw_spk)

                word_item = {
                    'word': w_text,
                    'start': start_val,
                    'end': end_val,
                    'speaker': w_speaker
                }
                seg_words.append(word_item)
                all_words.append(word_item)

            language = seg.get("language")
            blocks.append({
                'id': block_idx,
                'speaker': raw_spk,
                'language': language,
                'text': text,
                'start': round(float(seg.get("start", 0.0)), 2),
                'end': round(float(seg.get("end", 0.0)), 2),
                'words': seg_words
            })
            block_idx += 1

        # Mapeo inicial de oradores según rol e idioma (I: preguntas/inglés, R1/R2: español)
        initial_spk_map = map_speakers_by_language_and_role(blocks)
        for b in blocks:
            old_s = b.get('speaker')
            if old_s in initial_spk_map:
                b['speaker'] = initial_spk_map[old_s]
                for w in b.get('words', []):
                    w['speaker'] = initial_spk_map[old_s]

        # Si no se detectó duración previamente, inferir del último segmento
        if job.duration_seconds <= 0.0 and blocks:
            last_block = blocks[-1]
            job.duration_seconds = round(float(last_block.get('end', 0.0)), 2)
            job.save(update_fields=['duration_seconds'])

        # Comprobar cancelación antes de LLM
        with _jobs_lock:
            if _active_jobs_cancel_flags.get(str(job_id), False):
                append_job_log(job_id, "Proceso detenido a petición del usuario.", "warning")
                return

        # -------------------------------------------------------------
        # FASE 4: Capa semántica de Post-Procesado con LLM
        # -------------------------------------------------------------
        append_job_log(job_id, "Iniciando post-procesado semántico con LLM (roles de oradores, Spanglish, interrupciones)...", "info")
        processed_blocks = process_transcript_with_llm(blocks, hf_token=hf_token, llm_config=llm_config)

        # Sincronizar etiquetas de orador en words_data a partir de los bloques post-procesados
        for b in processed_blocks:
            spk = b.get('speaker', 'I')
            if 'words' in b:
                for w in b['words']:
                    w['speaker'] = spk

        job.transcript_blocks = processed_blocks
        job.words_data = all_words
        job.status = 'completed'
        job.save()
        append_job_log(job_id, "¡Transcripción y post-procesado semántico completados con éxito!", "success")

    except Exception as e:
        import traceback
        err_msg = str(e)
        trace_str = traceback.format_exc()
        print(f"Error crítico en transcripción {job_id}:\n{trace_str}")
        try:
            job = TranscriptionJob.objects.get(pk=job_id)
            job.status = 'failed'
            job.error_message = err_msg
            job.save(update_fields=['status', 'error_message'])
            append_job_log(job_id, f"ERROR: {err_msg}", "error")
        except Exception:
            pass



class ActiveJobsView(APIView):
    """
    GET /api/transcribe/active/
    Devuelve los trabajos actualmente en estado 'processing'.
    """
    def get(self, request):
        active_jobs = TranscriptionJob.objects.filter(status='processing')
        serializer = TranscriptionJobSerializer(active_jobs, many=True, context={'request': request})
        return Response({'count': active_jobs.count(), 'jobs': serializer.data})


class KillAllActiveJobsView(APIView):
    """
    POST /api/transcribe/kill-active/
    Cancela y mata todos los procesos en ejecución para dejar el servidor limpio.
    """
    def post(self, request):
        with _jobs_lock:
            active_jobs = TranscriptionJob.objects.filter(status='processing')
            killed_ids = []
            for j in active_jobs:
                _active_jobs_cancel_flags[str(j.id)] = True
                j.status = 'failed'
                j.error_message = 'Cancelado/detenido forzosamente por el usuario desde el panel.'
                j.save(update_fields=['status', 'error_message'])
                append_job_log(j.id, "Proceso detenido y liberado forzosamente desde el panel.", "warning")
                killed_ids.append(str(j.id))
        
        # Forzar recolección de basura
        gc.collect()
        return Response({
            'status': 'success',
            'killed_count': len(killed_ids),
            'killed_ids': killed_ids
        }, status=status.HTTP_200_OK)


class CancelJobView(APIView):
    """
    POST /api/transcribe/<id>/cancel/
    Cancela un trabajo en curso y limpia sus recursos.
    """
    def post(self, request, pk):
        job = get_object_or_404(TranscriptionJob, pk=pk)
        with _jobs_lock:
            _active_jobs_cancel_flags[str(job.id)] = True
        job.status = 'failed'
        job.error_message = 'Cancelado por el usuario.'
        job.save(update_fields=['status', 'error_message'])
        append_job_log(job.id, "Proceso cancelado por el usuario.", "warning")
        gc.collect()
        return Response({'status': 'cancelled', 'job_id': str(job.id)}, status=status.HTTP_200_OK)


class TranscribeAudioView(APIView):
    """
    POST /api/transcribe/
    Garantiza una ÚNICA ejecución activa a la vez: cancela cualquier proceso anterior
    antes de iniciar el nuevo trabajo.
    """
    def post(self, request):
        audio = request.FILES.get('audio_file')
        if not audio:
            return Response({'error': 'No audio_file provided'}, status=status.HTTP_400_BAD_REQUEST)

        task_number = request.data.get('task_number', 'Tilaus numero / Task ID')
        original_filename = audio.name
        current_date_str = datetime.datetime.now().strftime("%B %d, %Y")

        hf_token = (request.data.get('hf_token') or getattr(settings, 'HF_TOKEN', '')).strip()
        min_speakers = int(request.data.get('min_speakers', 2))
        max_speakers = int(request.data.get('max_speakers', 3))

        # Configuración opcional del LLM enviada desde el cliente
        llm_enabled = request.data.get('llm_enabled', True)
        if isinstance(llm_enabled, str):
            llm_enabled = llm_enabled.lower() in ['true', '1', 'yes']
        llm_api_url = request.data.get('llm_api_url') or getattr(settings, 'LLM_API_URL', '')
        llm_api_key = request.data.get('llm_api_key') or getattr(settings, 'LLM_API_KEY', '')
        llm_model = request.data.get('llm_model') or getattr(settings, 'LLM_MODEL', '')

        llm_config = {
            'enabled': llm_enabled,
            'api_url': llm_api_url,
            'api_key': llm_api_key,
            'model': llm_model
        }

        # -------------------------------------------------------------
        # REGLA ESTRICTA: Solo un trabajo a la vez
        # Cancelar y limpiar cualquier trabajo previo que estuviera en 'processing'
        # -------------------------------------------------------------
        with _jobs_lock:
            active_jobs = TranscriptionJob.objects.filter(status='processing')
            for old_job in active_jobs:
                _active_jobs_cancel_flags[str(old_job.id)] = True
                old_job.status = 'failed'
                old_job.error_message = 'Cancelado automáticamente al iniciar una nueva transcripción.'
                old_job.save(update_fields=['status', 'error_message'])
                append_job_log(old_job.id, "Trabajo abortado: se ha iniciado una nueva transcripción.", "warning")

        initial_log = {
            'time': datetime.datetime.now().strftime("%H:%M:%S"),
            'message': f"Archivo '{original_filename}' recibido. Iniciando delegación en Worker GPU RunPod (Zero CPU Fallback)...",
            'level': 'info'
        }


        job = TranscriptionJob.objects.create(
            audio_file=audio,
            original_filename=original_filename,
            status='processing',
            metadata_header={
                'task_number': task_number,
                'transcribed_date': current_date_str,
                'comments': '--'
            },
            transcript_blocks=[],
            words_data=[],
            logs=[initial_log],
            error_message=''
        )

        with _jobs_lock:
            _active_jobs_cancel_flags[str(job.id)] = False

        # Iniciar pipeline de WhisperX en hilo exclusivo en segundo plano con soporte LLM
        thread = threading.Thread(
            target=execute_whisperx_pipeline,
            args=(job.id, hf_token, min_speakers, max_speakers, llm_config),
            daemon=True
        )
        thread.start()

        serializer = TranscriptionJobSerializer(job, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


from django.views import View

class JobLogsStreamView(View):
    """
    GET /api/transcribe/<id>/events/
    Server-Sent Events (SSE) para emitir logs y cambios de estado en tiempo real.
    Hereda de View estándar para permitir 'Accept: text/event-stream' sin bloqueo de DRF.
    """
    def get(self, request, pk):
        job = get_object_or_404(TranscriptionJob, pk=pk)

        def event_stream():
            last_index = 0
            while True:
                try:
                    current_job = TranscriptionJob.objects.get(pk=pk)
                except TranscriptionJob.DoesNotExist:
                    break

                all_logs = current_job.logs or []
                if len(all_logs) > last_index:
                    for entry in all_logs[last_index:]:
                        payload = json.dumps({
                            'type': 'log',
                            'log': entry,
                            'status': current_job.status
                        })
                        yield f"data: {payload}\n\n"
                    last_index = len(all_logs)

                if current_job.status in ['completed', 'failed']:
                    # Emitir evento final con el objeto de trabajo actualizado
                    final_data = TranscriptionJobSerializer(current_job, context={'request': request}).data
                    final_payload = json.dumps({
                        'type': 'status_change',
                        'status': current_job.status,
                        'job': final_data
                    })
                    yield f"data: {final_payload}\n\n"
                    break

                time.sleep(0.5)

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class JobDetailView(APIView):
    """
    GET /api/transcribe/<id>/
    """
    def get(self, request, pk):
        job = get_object_or_404(TranscriptionJob, pk=pk)
        serializer = TranscriptionJobSerializer(job, context={'request': request})
        return Response(serializer.data)


class SaveTranscriptView(APIView):
    """
    PATCH /api/transcribe/<id>/save/
    Updates transcript_blocks and metadata_header.
    """
    def patch(self, request, pk):
        job = get_object_or_404(TranscriptionJob, pk=pk)
        transcript_blocks = request.data.get('transcript_blocks')
        metadata_header = request.data.get('metadata_header')

        if transcript_blocks is not None:
            job.transcript_blocks = transcript_blocks
        if metadata_header is not None:
            job.metadata_header = metadata_header

        job.save()
        serializer = TranscriptionJobSerializer(job, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


import re
import urllib.parse

class ExportDocxView(APIView):
    """
    GET /api/transcribe/<id>/export-docx/
    Genera un archivo .docx según las directrices exactas del cliente:
    - Nombre del archivo idéntico al original sustituyendo la extensión por .docx
    - Tipografía obligatoria: Verdana 8 pt en todo el documento
    - Cabecera estructurada idéntica
    - Línea en blanco obligatoria entre intervenciones de oradores
    - Renderizado de palabras con énfasis/subrayado (<u>...</u>) con run.font.underline = True
    """
    def get(self, request, pk):
        job = get_object_or_404(TranscriptionJob, pk=pk)
        
        doc = Document()

        # Configurar márgenes estándar (1 pulgada)
        for section in doc.sections:
            section.top_margin = Inches(1)
            section.bottom_margin = Inches(1)
            section.left_margin = Inches(1)
            section.right_margin = Inches(1)

        # Configurar estilo Normal por defecto: Verdana 8 pt
        normal_style = doc.styles['Normal']
        normal_style.font.name = 'Verdana'
        normal_style.font.size = Pt(8)
        normal_style.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

        # Helper para crear párrafos estrictamente en Verdana 8 pt
        def add_p(text='', bold=False, underline=False, space_after=0, space_before=0):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(space_after)
            p.paragraph_format.space_before = Pt(space_before)
            p.paragraph_format.line_spacing = 1.0
            if text:
                r = p.add_run(text)
                r.font.name = 'Verdana'
                r.font.size = Pt(8)
                r.bold = bold
                r.underline = underline
            return p

        # Metadatos de la cabecera
        meta = job.metadata_header or {}
        task_no = meta.get('task_number', 'Tilaus numero / Task ID')
        filename = job.original_filename or 'recording.mp3'
        
        # Duración en minutos (entero o redondeo hacia arriba)
        duration_mins = math.ceil(job.duration_seconds / 60.0) if job.duration_seconds > 0 else 0
        length_str = f"{duration_mins} min" if duration_mins > 0 else "0 min"
        
        # Formato de fecha estilo inglés: "September 12, 2026"
        created_dt = job.created_at if job.created_at else datetime.datetime.now()
        formatted_date = created_dt.strftime("%B %d, %Y")
        comments = meta.get('comments', '--')

        # 1. Cabecera exacta obligatoria
        add_p(f"Number of transcription:        {task_no}")
        add_p(f"Name of recording file:         {filename}")
        add_p(f"Length of recording:            {length_str}")
        add_p(f"Transcribed on:                 {formatted_date}", space_after=12)

        add_p("Indicators used:     I: Interviewer(s)")
        add_p("                     R: Respondent(s)", space_after=12)

        add_p("Other notations:     ...         Interrupted or continued statement")
        add_p("                     (-)         Omitted word or part of word")
        add_p("                     (--)        Omitted part of speech")
        add_p("                     (word)      Unclear word or uncertain spelling")
        add_p("                     underlined  Word or part of speech with particular emphasis")
        add_p("                     [brackets]  Transcriber's comment")
        add_p("                     [os]        Overlapping speech")
        add_p("                     [qs]        Quiet speech")
        add_p("                     [us]        Unclear speech", space_after=12)

        add_p(f"Comments:            {comments}", space_after=18)

        # Salto / Separación antes de las intervenciones
        add_p("", space_after=0)

        # 2. Intervenciones de oradores con regla estricta:
        # UNA LÍNEA EN BLANCO ENTRE CADA INTERVENCIÓN CUANDO CAMBIA EL ORADOR
        blocks = job.transcript_blocks or []
        for i, block in enumerate(blocks):
            speaker = block.get('speaker', 'I').strip()
            text = block.get('text', '').strip()

            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0

            # Etiqueta del orador (ej: "I: ", "R: ", "R1: ", "R2: ") en negrita Verdana 8pt
            label_run = p.add_run(f"{speaker}: ")
            label_run.font.name = 'Verdana'
            label_run.font.size = Pt(8)
            label_run.bold = True

            # Parsear texto respetando posibles etiquetas <u>...</u> para palabras con énfasis
            if '<u>' in text and '</u>' in text:
                parts = re.split(r'(<u>.*?</u>)', text, flags=re.IGNORECASE)
                for part in parts:
                    if not part:
                        continue
                    if part.lower().startswith('<u>') and part.lower().endswith('</u>'):
                        underlined_content = part[3:-4]
                        r = p.add_run(underlined_content)
                        r.font.name = 'Verdana'
                        r.font.size = Pt(8)
                        r.underline = True
                    else:
                        r = p.add_run(part)
                        r.font.name = 'Verdana'
                        r.font.size = Pt(8)
            else:
                r = p.add_run(text)
                r.font.name = 'Verdana'
                r.font.size = Pt(8)

            # Dejar obligatoriamente UNA LÍNEA EN BLANCO solo cuando el orador cambia respecto a la siguiente intervención
            if i < len(blocks) - 1:
                next_speaker = blocks[i + 1].get('speaker', 'I').strip()
                if next_speaker != speaker:
                    blank_p = doc.add_paragraph()
                    blank_p.paragraph_format.space_before = Pt(0)
                    blank_p.paragraph_format.space_after = Pt(0)
                    blank_p.paragraph_format.line_spacing = 1.0
                    blank_r = blank_p.add_run("")
                    blank_r.font.name = 'Verdana'
                    blank_r.font.size = Pt(8)

        # Generar nombre del archivo idéntico al audio subido sustituyendo la extensión por .docx
        base_name, _ = os.path.splitext(job.original_filename or 'transcription.mp3')
        export_filename = f"{base_name}.docx"

        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        response = HttpResponse(
            buffer.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
        quoted_filename = urllib.parse.quote(export_filename)
        response['Content-Disposition'] = f'attachment; filename="{export_filename}"; filename*=UTF-8\'\'{quoted_filename}'
        return response


# =====================================================================
# Vistas de Configuración y Ciclo de Vida del Worker GPU Remoto / RunPod
# =====================================================================
# Vistas de Configuración y Ciclo de Vida del Worker GPU Remoto / RunPod
# =====================================================================

from .models import RunPodConfig
from .serializers import RunPodConfigSerializer
from .services.runpod_orchestrator import RunPodOrchestrator, query_runpod_graphql


class PodStatusView(APIView):
    """
    GET /api/pod/status/
    Devuelve si el puerto local 8005 o el worker remoto responde 200 OK.
    """
    def get(self, request):
        config = RunPodConfig.get_solo()
        orchestrator = RunPodOrchestrator()
        local_port = config.local_proxy_port or 8005

        is_listening = orchestrator.is_port_listening(port=local_port)
        is_healthy = orchestrator.check_health(local_port=local_port)

        if is_healthy:
            return Response({
                "status": "connected",
                "healthy": True,
                "local_proxy_port": local_port,
                "pod_host": config.pod_host,
                "pod_ssh_port": config.pod_ssh_port,
                "message": f"Worker GPU respondiendo 200 OK en localhost:{local_port}"
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "disconnected",
                "healthy": False,
                "local_proxy_port": local_port,
                "pod_host": config.pod_host,
                "pod_ssh_port": config.pod_ssh_port,
                "message": f"Worker GPU no responde en localhost:{local_port}"
            }, status=status.HTTP_200_OK)


class WorkerConfigView(APIView):
    """
    GET /api/worker/config/
    POST /api/worker/config/
    Permite consultar y actualizar los parámetros de configuración dinámica del Pod.
    """
    def get(self, request):
        config = RunPodConfig.get_solo()
        serializer = RunPodConfigSerializer(config)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        config = RunPodConfig.get_solo()
        serializer = RunPodConfigSerializer(config, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class WorkerDetectView(APIView):
    """
    POST /api/worker/detect/
    Consulta la API GraphQL de RunPod para autocompletar la IP y puerto SSH a partir del POD_ID y RUNPOD_API_KEY.
    """
    def post(self, request):
        config = RunPodConfig.get_solo()
        pod_id = request.data.get('pod_id') or config.pod_id
        api_key = request.data.get('runpod_api_key') or config.runpod_api_key

        if not pod_id or not api_key:
            return Response(
                {"error": "Debes especificar tanto Pod ID como RunPod API Key para la detección automática."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            pod_info = query_runpod_graphql(pod_id=pod_id, api_key=api_key)
            config.pod_id = pod_id
            config.runpod_api_key = api_key
            config.pod_host = pod_info["pod_host"]
            config.pod_ssh_port = pod_info["pod_ssh_port"]
            config.save()

            return Response({
                "status": "success",
                "pod_id": pod_id,
                "pod_name": pod_info.get("pod_name"),
                "pod_host": pod_info["pod_host"],
                "pod_ssh_port": pod_info["pod_ssh_port"],
                "desired_status": pod_info.get("desired_status"),
                "message": f"IP ({pod_info['pod_host']}) y Puerto SSH ({pod_info['pod_ssh_port']}) detectados exitosamente."
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class WorkerHealthView(APIView):
    """
    GET /api/worker/health/
    Verifica el estado del worker remoto:
    - 200 OK si localhost:{LOCAL_PROXY_PORT} responde OK
    """
    def get(self, request):
        config = RunPodConfig.get_solo()
        orchestrator = RunPodOrchestrator()
        local_port = config.local_proxy_port or 8005

        is_listening = orchestrator.is_port_listening(port=local_port)
        is_healthy = orchestrator.check_health(local_port=local_port)

        if is_healthy:
            return Response({
                "status": "connected",
                "tunnel_active": True,
                "local_proxy_port": local_port,
                "pod_host": config.pod_host,
                "pod_ssh_port": config.pod_ssh_port,
                "message": "Worker GPU conectado y respondiendo 200 OK."
            }, status=status.HTTP_200_OK)

        if config.pod_host and (request.query_params.get('auto_connect', 'false').lower() in ['1', 'true']):
            res = orchestrator.ensure_worker_ready(
                host=config.pod_host,
                ssh_port=config.pod_ssh_port,
                ssh_key_path=config.ssh_key_path,
                local_port=local_port,
                hf_token=config.hf_token
            )
            return Response(res, status=status.HTTP_200_OK if res.get("status") == "connected" else status.HTTP_502_BAD_GATEWAY)

        return Response({
            "status": "disconnected",
            "tunnel_active": is_listening,
            "local_proxy_port": local_port,
            "pod_host": config.pod_host,
            "pod_ssh_port": config.pod_ssh_port,
            "message": "Worker GPU desconectado o no responde en el puerto proxy local."
        }, status=status.HTTP_200_OK)


class WorkerConnectView(APIView):
    """
    POST /api/worker/connect/ o /api/pod/connect/
    Ejecuta el flujo completo de conexión, túnel SSH y auto-aprovisionamiento del pod.
    """
    def post(self, request):
        config = RunPodConfig.get_solo()

        pod_host = (request.data.get('pod_host') or config.pod_host or '').strip()
        pod_ssh_port = request.data.get('pod_ssh_port') or config.pod_ssh_port or 22
        ssh_key_path = request.data.get('ssh_key_path') or config.ssh_key_path or '~/.ssh/id_rsa'
        hf_token = (request.data.get('hf_token') or config.hf_token or '').strip()
        local_port = int(request.data.get('local_proxy_port') or config.local_proxy_port or 8005)

        if not pod_host:
            return Response({"error": "No se ha especificado POD_HOST."}, status=status.HTTP_400_BAD_REQUEST)

        orchestrator = RunPodOrchestrator()
        result = orchestrator.ensure_worker_ready(
            host=pod_host,
            ssh_port=int(pod_ssh_port),
            ssh_key_path=ssh_key_path,
            local_port=local_port,
            hf_token=hf_token
        )

        http_status = status.HTTP_200_OK if result.get("status") == "connected" else status.HTTP_502_BAD_GATEWAY
        return Response(result, status=http_status)


class WorkerDisconnectView(APIView):
    """
    POST /api/worker/disconnect/
    Cierra el túnel SSH hacia el pod.
    """
    def post(self, request):
        orchestrator = RunPodOrchestrator()
        orchestrator.stop_tunnel()
        return Response({"status": "disconnected", "message": "Túnel SSH detenido."}, status=status.HTTP_200_OK)


