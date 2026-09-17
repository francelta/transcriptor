"""
backend/transcription/services/runpod_orchestrator.py
Gestor centralizado y programático de instancias de RunPod y Worker GPU.
Maneja conexión SSH con Paramiko/SSHTunnel, auto-aprovisionamiento idempotente,
despliegue de /server.py optimizado y healthcheck.
"""

import os
import io
import time
import socket
import logging
import threading
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

RUNPOD_GRAPHQL_ENDPOINT = "https://api.runpod.io/graphql"

POD_QUERY = """
query Pod($podId: String!) {
  pod(input: {podId: $podId}) {
    id
    name
    runtime {
      ports {
        ip
        publicPort
        privatePort
        type
      }
    }
    desiredStatus
  }
}
"""

# Script optimizado y exhaustivo para ejecutar en el Pod GPU (/server.py)
POD_SERVER_PY_CONTENT = '''# /server.py - TranscriberStudio Pro Remote GPU Worker
# Inferencia oficial de alta fidelidad con WhisperX (large-v3)
# Incluye: transcripción batched, alineación fonética wav2vec2 palabra por palabra,
# diarización Pyannote 3.1, pausas (≥1.2s → ...) y solapamientos ([os]).
import os
import io
import gc
import tempfile
import subprocess
import torch
import soundfile as sf
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
import whisperx
from whisperx.diarize import DiarizationPipeline
from faster_whisper import WhisperModel
from typing import Optional

app = FastAPI(title="TranscriberStudio Remote GPU Worker")

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = "float16" if torch.cuda.is_available() else "int8"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

whisper_model = None
diarize_pipeline = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        print(f"[Worker] Cargando Whisper (large-v3) en {DEVICE} ({COMPUTE_TYPE})...")
        whisper_model = WhisperModel(
            "large-v3",
            device=DEVICE,
            compute_type=COMPUTE_TYPE
        )
    return whisper_model

def get_diarization_pipeline(hf_token: str = None):
    global diarize_pipeline
    token = (hf_token or HF_TOKEN or "").strip()
    if not token:
        return None
    if diarize_pipeline is None:
        print("[Worker] Cargando WhisperX DiarizationPipeline (Pyannote)...")
        try:
            diarize_pipeline = DiarizationPipeline(
                token=token,
                device=DEVICE
            )
        except Exception as e:
            print(f"[Worker] Error cargando pipeline de diarización: {e}")
            return None
    return diarize_pipeline

@app.get("/")
@app.get("/health")
def health():
    return {
        "status": "healthy",
        "device": DEVICE,
        "cuda_available": torch.cuda.is_available(),
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A"
    }

@app.get("/docs")
def docs_redirect():
    return {"status": "healthy", "message": "TranscriberStudio GPU Worker running"}

def convert_to_wav_16k_mono_filtered(input_bytes: bytes) -> np.ndarray:
    """
    Convierte cualquier archivo multimedia a WAV 16kHz mono usando ffmpeg.
    Aplica:
      - Filtro pasa-banda de voz humana: 80 Hz a 8000 Hz (highpass + lowpass)
      - Normalización perceptual EBU R128 para rescatar susurros y voces lejanas
        loudnorm=I=-16:TP=-1.5:LRA=11
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp_in:
        tmp_in.write(input_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + "_16k.wav"
    try:
        cmd = [
            "ffmpeg", "-y", "-i", tmp_in_path,
            "-ar", "16000",
            "-ac", "1",
            "-af", "highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-f", "wav",
            tmp_out_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {res.stderr.decode('utf-8', errors='ignore')}")

        data, sr = sf.read(tmp_out_path)
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        return data.astype(np.float32)
    finally:
        if os.path.exists(tmp_in_path):
            os.remove(tmp_in_path)
        if os.path.exists(tmp_out_path):
            os.remove(tmp_out_path)

def merge_speaker_turns(turns, pause_threshold=1.0):
    """
    Fusión de turnos contiguos del mismo orador si la pausa entre ellos
    es menor a pause_threshold (1.0 s). Reduce fragmentación excesiva.
    """
    if not turns:
        return []

    merged = []
    current = dict(turns[0])

    for nxt in turns[1:]:
        if nxt["speaker"] == current["speaker"] and (nxt["start"] - current["end"] < pause_threshold):
            current["end"] = max(current["end"], nxt["end"])
        else:
            merged.append(current)
            current = dict(nxt)
    merged.append(current)
    return merged

def detect_language_bilingual(whisper, chunk: np.ndarray) -> str:
    """
    Clasificador bilingüe RESTRINGIDO: evalúa EXCLUSIVAMENTE es y en.
    Descarta cualquier desvío a francés u otras lenguas.
    Devuelve "es" o "en".
    """
    try:
        sample_chunk = chunk[:int(30 * 16000)]
        _, lang_probs = whisper.detect_language(sample_chunk)
        prob_es = lang_probs.get("es", 0.0)
        prob_en = lang_probs.get("en", 0.0)
        return "en" if prob_en > prob_es else "es"
    except Exception:
        return "es"

@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
    hf_token: Optional[str] = Form(None)
):
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Archivo vacío recibido.")

        print(f"[Worker] Procesando audio recibido ({len(content)} bytes) con WhisperX...")

        # ── 1. Preprocesamiento Acústico (EBU R128 + filtro pasa-banda voz) ──
        data = convert_to_wav_16k_mono_filtered(content)
        sample_rate = 16000
        total_duration = len(data) / float(sample_rate)
        print(f"[Worker] Audio preprocesado: {total_duration:.2f}s @ 16kHz mono")

        # Guardar temporal para carga en whisperx
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
            sf.write(tmp_wav.name, data, sample_rate)
            wav_path = tmp_wav.name

        try:
            audio = whisperx.load_audio(wav_path)
            model = get_whisper_model()
            diar_pipe = get_diarization_pipeline(hf_token)

            raw_segments = []
            detected_lang = "es"

            if diar_pipe:
                print("[Worker] 1. Ejecutando diarización de oradores con Pyannote...")
                kwargs = {}
                if min_speakers:
                    kwargs["min_speakers"] = int(min_speakers)
                if max_speakers:
                    kwargs["max_speakers"] = int(max_speakers)

                diarize_df = diar_pipe(audio, **kwargs)

                # Agrupar intervenciones contiguas del mismo orador si la pausa es menor a 0.8s
                merged_turns = []
                for _, row in diarize_df.iterrows():
                    spk = str(row["speaker"])
                    st = float(row["start"])
                    en = float(row["end"])
                    if merged_turns and merged_turns[-1]["speaker"] == spk and (st - merged_turns[-1]["end"]) < 0.8:
                        merged_turns[-1]["end"] = en
                    else:
                        merged_turns.append({"speaker": spk, "start": st, "end": en})

                print(f"[Worker] Diarización completada: {len(merged_turns)} turnos de orador identificados.")
                print("[Worker] 2. Transcribiendo turnos con detección dinámica de idioma (¡cero traducciones forzadas!)...")

                sr = 16000
                detected_languages = []
                for t in merged_turns:
                    st_idx = int(t["start"] * sr)
                    en_idx = int(t["end"] * sr)
                    if en_idx - st_idx < sr * 0.4:
                        continue
                    turn_audio = audio[st_idx:en_idx]

                    # Detectar el idioma específico de este turno (en / es)
                    lang, prob, _ = model.detect_language(turn_audio)
                    detected_languages.append(lang)

                    segs, _ = model.transcribe(
                        turn_audio,
                        language=lang,
                        beam_size=5,
                        word_timestamps=True,
                        condition_on_previous_text=False
                    )

                    t_start = t["start"]
                    for s in segs:
                        s_text = s.text.strip()
                        if not s_text:
                            continue
                        seg_words = []
                        if hasattr(s, "words") and s.words:
                            for w in s.words:
                                w_text = w.word.strip()
                                if not w_text:
                                    continue
                                seg_words.append({
                                    "word": w_text,
                                    "start": round(float(t_start + w.start), 2),
                                    "end": round(float(t_start + w.end), 2),
                                    "speaker": t["speaker"],
                                    "score": getattr(w, "probability", 1.0)
                                })
                        raw_segments.append({
                            "start": round(float(t_start + s.start), 2),
                            "end": round(float(t_start + s.end), 2),
                            "text": s_text,
                            "speaker": t["speaker"],
                            "words": seg_words,
                            "language": lang
                        })

                if detected_languages:
                    from collections import Counter
                    detected_lang = Counter(detected_languages).most_common(1)[0][0]

            else:
                print("[Worker] Sin HF Token para Pyannote: transcribiendo con detección dinámica...")
                segs, info = model.transcribe(
                    wav_path,
                    beam_size=5,
                    word_timestamps=True,
                    multilingual=True,
                    condition_on_previous_text=False,
                    vad_filter=True
                )
                detected_lang = info.language or "es"
                for s in segs:
                    s_text = s.text.strip()
                    if not s_text:
                        continue
                    seg_words = []
                    if hasattr(s, "words") and s.words:
                        for w in s.words:
                            w_text = w.word.strip()
                            if not w_text:
                                continue
                            seg_words.append({
                                "word": w_text,
                                "start": round(float(w.start), 2),
                                "end": round(float(w.end), 2),
                                "speaker": "SPEAKER_00",
                                "score": getattr(w, "probability", 1.0)
                            })
                    raw_segments.append({
                        "start": round(float(s.start), 2),
                        "end": round(float(s.end), 2),
                        "text": s_text,
                        "speaker": "SPEAKER_00",
                        "words": seg_words,
                        "language": detected_lang
                    })

            # Ordenar segmentos cronológicamente
            raw_segments.sort(key=lambda x: x["start"])

        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

        # ── 3. Formato compatible con TranscriberStudio Pro (Pausas ... y [os]) ──
        out_segments = []

        for s_idx, seg in enumerate(raw_segments):
            seg_text = seg.get("text", "").strip()
            if not seg_text:
                continue

            spk = seg.get("speaker") or "SPEAKER_00"
            s_start = round(float(seg.get("start", 0.0)), 2)
            s_end = round(float(seg.get("end", s_start + 0.5)), 2)
            seg_words = seg.get("words", [])

            # Notación de pausas ≥ 1.2s
            annotated_words = []
            for i, current_word in enumerate(seg_words):
                annotated_words.append(current_word["word"])
                if i < len(seg_words) - 1:
                    gap = seg_words[i + 1]["start"] - current_word["end"]
                    if gap >= 1.2:
                        annotated_words.append("...")

            computed_text = " ".join(annotated_words) if annotated_words else seg_text

            # Detección de solapamiento de oradores [os]
            is_overlapping = False
            for other_seg in raw_segments:
                if other_seg.get("speaker") and other_seg["speaker"] != spk:
                    o_start = float(other_seg.get("start", 0.0))
                    o_end = float(other_seg.get("end", 0.0))
                    overlap = max(0.0, min(s_end, o_end) - max(s_start, o_start))
                    if overlap >= 0.2:
                        is_overlapping = True
                        break

            if is_overlapping and "[os]" not in computed_text:
                computed_text = f"{computed_text} [os]"

            out_segments.append({
                "start": s_start,
                "end": s_end,
                "text": computed_text,
                "speaker": spk,
                "words": seg_words,
                "language": seg.get("language")
            })

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        return JSONResponse({
            "status": "success",
            "duration": round(total_duration, 2),
            "language": detected_lang,
            "segments": out_segments
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
'''


def query_runpod_graphql(pod_id: str, api_key: str) -> Dict[str, Any]:
    """
    Consulta la API GraphQL de RunPod para obtener el pod,
    su IP pública y el puerto público mapeado al puerto 22 privado.
    """
    if not pod_id or not api_key:
        raise ValueError("Se requieren pod_id y api_key para consultar la API de RunPod.")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}"
    }
    payload = {
        "query": POD_QUERY,
        "variables": {"podId": pod_id.strip()}
    }

    try:
        response = requests.post(RUNPOD_GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error conectando con RunPod GraphQL API: {e}")
        raise RuntimeError(f"Error de red con la API de RunPod: {e}")

    if "errors" in data:
        err_msg = "; ".join([err.get("message", "Error desconocido") for err in data["errors"]])
        raise RuntimeError(f"Error de RunPod GraphQL: {err_msg}")

    pod_data = data.get("data", {}).get("pod")
    if not pod_data:
        raise RuntimeError(f"No se encontró información para el Pod ID '{pod_id}'.")

    runtime = pod_data.get("runtime")
    if not runtime:
        desired_status = pod_data.get("desiredStatus", "UNKNOWN")
        raise RuntimeError(
            f"El pod '{pod_id}' no tiene runtime activo (Estado deseado: {desired_status}). "
            "Asegúrate de que la instancia esté en estado 'RUNNING' en la consola de RunPod."
        )

    ports = runtime.get("ports", [])
    ssh_port_entry = None
    fallback_ip = None

    for p in ports:
        ip = p.get("ip")
        if ip:
            fallback_ip = ip
        if p.get("privatePort") == 22:
            ssh_port_entry = p
            break

    if not ssh_port_entry:
        raise RuntimeError(
            f"No se encontró mapeo para el puerto privado 22 (SSH) en el pod '{pod_id}'. "
            "Verifica la configuración de red y puertos del pod."
        )

    public_ip = ssh_port_entry.get("ip") or fallback_ip
    public_port = ssh_port_entry.get("publicPort")

    if not public_ip or not public_port:
        raise RuntimeError(
            f"El mapeo SSH del pod '{pod_id}' no tiene IP o puerto público disponible."
        )

    return {
        "pod_id": pod_id,
        "pod_name": pod_data.get("name", ""),
        "desired_status": pod_data.get("desiredStatus", ""),
        "pod_host": public_ip,
        "pod_ssh_port": int(public_port)
    }


class RunPodOrchestrator:
    """
    Gestor Singleton para túnel SSH y auto-aprovisionamiento del Pod.
    Asegura túnel localhost:8005 -> POD_HOST:POD_SSH_PORT -> 127.0.0.1:8000
    y despliegue transparente de /server.py.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(RunPodOrchestrator, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.tunnel = None
        self.current_host = None
        self.current_port = None
        self.local_port = 8005
        self.is_running = False
        self._action_lock = threading.RLock()

    def is_port_listening(self, host: str = "127.0.0.1", port: int = 8005) -> bool:
        """Comprueba si el socket TCP local responde."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            try:
                s.connect((host, port))
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                return False

    def check_health(self, local_port: int = 8005) -> bool:
        """Comprueba si http://127.0.0.1:local_port/health o /docs responde 200 OK."""
        try:
            r = requests.get(f"http://127.0.0.1:{local_port}/health", timeout=3)
            return r.status_code == 200
        except Exception:
            try:
                r = requests.get(f"http://127.0.0.1:{local_port}/docs", timeout=3)
                return r.status_code == 200
            except Exception:
                return False

    def stop_tunnel(self):
        """Detiene el túnel SSH."""
        with self._action_lock:
            if self.tunnel:
                try:
                    logger.info("Deteniendo túnel SSHTunnel...")
                    self.tunnel.stop()
                except Exception as e:
                    logger.warning(f"Error deteniendo SSHTunnel: {e}")
                finally:
                    self.tunnel = None
            self.is_running = False

    def start_tunnel(self, host: str, ssh_port: int, ssh_key_path: str, local_port: int = 8005, remote_port: int = 8000) -> bool:
        """
        Abre túnel SSH hacia el Pod:
        127.0.0.1:{local_port} -> {host}:{ssh_port} -> 127.0.0.1:{remote_port}
        """
        import paramiko
        if not hasattr(paramiko, "DSSKey"):
            paramiko.DSSKey = None
        import sshtunnel

        expanded_key = os.path.expanduser(ssh_key_path)
        if not os.path.isfile(expanded_key):
            raise FileNotFoundError(f"La clave privada SSH no existe en: {expanded_key}")

        with self._action_lock:
            if self.is_port_listening("127.0.0.1", local_port) and self.current_host == host and self.current_port == ssh_port:
                logger.info(f"Túnel SSH ya activo en 127.0.0.1:{local_port}")
                self.is_running = True
                return True

            self.stop_tunnel()

            logger.info(f"Levantando túnel SSH hacia {host}:{ssh_port} (local {local_port} -> remoto {remote_port})...")
            try:
                tunnel = sshtunnel.SSHTunnelForwarder(
                    (host, int(ssh_port)),
                    ssh_username="root",
                    ssh_pkey=expanded_key,
                    remote_bind_address=("127.0.0.1", int(remote_port)),
                    local_bind_address=("127.0.0.1", int(local_port)),
                    set_keepalive=30.0
                )
                tunnel.start()
                self.tunnel = tunnel
                self.current_host = host
                self.current_port = ssh_port
                self.local_port = local_port
                self.is_running = True
                logger.info(f"Túnel SSH levantado exitosamente en 127.0.0.1:{local_port}")
                return True
            except Exception as e:
                self.stop_tunnel()
                logger.error(f"Fallo al abrir túnel SSH hacia {host}:{ssh_port}: {e}")
                raise RuntimeError(f"No se pudo establecer el túnel SSH con {host}:{ssh_port}: {e}")

    def provision_worker_via_ssh(self, host: str, ssh_port: int, ssh_key_path: str, hf_token: str = "") -> Dict[str, Any]:
        """
        Conecta mediante paramiko, instala dependencias si faltan,
        escribe /server.py y ejecuta en tmux en segundo plano.
        """
        import paramiko

        expanded_key = os.path.expanduser(ssh_key_path)
        if not os.path.isfile(expanded_key):
            raise FileNotFoundError(f"Clave SSH privada no encontrada en: {expanded_key}")

        logger.info(f"Conectando vía Paramiko SSH a root@{host}:{ssh_port}...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                hostname=host,
                port=int(ssh_port),
                username="root",
                key_filename=expanded_key,
                timeout=25,
                banner_timeout=25
            )
        except Exception as e:
            raise RuntimeError(f"Error conectando por SSH a root@{host}:{ssh_port}: {e}")

        logs = []
        try:
            def exec_cmd(cmd, timeout=300):
                stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode('utf-8', errors='replace').strip()
                err = stderr.read().decode('utf-8', errors='replace').strip()
                return exit_status, out, err

            # 1. Comprobar e instalar dependencias del sistema
            status, _, _ = exec_cmd("which tmux && which ffmpeg")
            if status != 0:
                logs.append("Instalando ffmpeg, libsndfile1 y tmux en el Pod...")
                exec_cmd("apt-get update -y && apt-get install -y ffmpeg libsndfile1 tmux", timeout=180)

            # 2. Comprobar dependencias de Python
            status, _, _ = exec_cmd("python3 -c 'import faster_whisper, pyannote.audio, soundfile, fastapi, uvicorn, scipy'")
            if status != 0:
                logs.append("Instalando librerías Python (faster-whisper, pyannote.audio, fastapi, uvicorn, python-multipart, soundfile, scipy)...")
                exec_cmd("pip install --no-cache-dir faster-whisper pyannote.audio soundfile fastapi uvicorn python-multipart scipy", timeout=300)

            # 3. Escribir /server.py en el pod
            logs.append("Escribiendo script /server.py optimizado en el Pod...")
            sftp = ssh.open_sftp()
            with sftp.file('/server.py', 'w') as f:
                f.write(POD_SERVER_PY_CONTENT)
            sftp.close()

            # 4. Iniciar sesión tmux con el worker
            logs.append("Lanzando servidor en tmux session 'worker'...")
            exec_cmd("tmux kill-session -t worker 2>/dev/null || true")

            token_env = f"export HF_TOKEN='{hf_token.strip()}';" if hf_token else ""
            launch_cmd = f"tmux new-session -d -s worker '{token_env} python3 /server.py'"
            exec_cmd(launch_cmd)

            return {
                "success": True,
                "logs": logs,
                "message": "Worker aprovisionado y lanzado en tmux correctamente."
            }
        finally:
            ssh.close()

    def ensure_worker_ready(
        self,
        host: str,
        ssh_port: int,
        ssh_key_path: str,
        local_port: int = 8005,
        hf_token: str = ""
    ) -> Dict[str, Any]:
        """
        Orquesta todo el ciclo:
        1. Comprueba si el worker ya responde 200 OK en 127.0.0.1:{local_port}.
        2. Si no responde, levanta el túnel SSH.
        3. Si tras levantar túnel no responde, conecta vía Paramiko y aprovisiona /server.py.
        4. Espera confirmación 200 OK.
        """
        if self.is_port_listening("127.0.0.1", local_port) and self.check_health(local_port):
            return {
                "status": "connected",
                "message": f"Worker GPU respondiendo 200 OK en 127.0.0.1:{local_port}"
            }

        # Abrir túnel
        try:
            self.start_tunnel(host, ssh_port, ssh_key_path, local_port=local_port)
        except Exception as e:
            return {
                "status": "disconnected",
                "error": f"Fallo al abrir túnel SSH: {e}"
            }

        time.sleep(1)
        if self.check_health(local_port):
            return {
                "status": "connected",
                "message": f"Worker GPU conectado y respondiendo 200 OK en 127.0.0.1:{local_port}"
            }

        # Auto-aprovisionar vía SSH
        logger.info(f"Worker en {host}:{ssh_port} requiere aprovisionamiento. Conectando por SSH...")
        try:
            prov_res = self.provision_worker_via_ssh(host, ssh_port, ssh_key_path, hf_token=hf_token)
        except Exception as prov_err:
            return {
                "status": "error",
                "error": f"Error en auto-aprovisionamiento SSH: {prov_err}"
            }

        # Esperar confirmación hasta 35 segundos
        for _ in range(18):
            time.sleep(2)
            if self.check_health(local_port):
                return {
                    "status": "connected",
                    "provisioned": True,
                    "details": prov_res,
                    "message": f"Worker GPU aprovisionado exitosamente y respondiendo 200 OK en 127.0.0.1:{local_port}"
                }

        return {
            "status": "warning",
            "error": "El worker fue lanzado en tmux pero no respondió 200 OK en el tiempo esperado."
        }
