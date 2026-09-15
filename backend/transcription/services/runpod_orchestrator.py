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

# Script optimizado para ejecutar en el Pod GPU (/server.py)
POD_SERVER_PY_CONTENT = '''# /server.py - TranscriberStudio Pro Remote GPU Worker
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
from faster_whisper import WhisperModel
from pyannote.audio import Pipeline

app = FastAPI(title="TranscriberStudio Remote GPU Worker")

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE = "float16" if torch.cuda.is_available() else "int8"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Context prompt temático para evitar confusiones léxicas
INITIAL_PROMPT = (
    "Entrevista de investigación académica sobre el Mar Menor, nitratos, rambla, "
    "metales pesados, personalidad jurídica, puertos deportivos, contaminación agrícola "
    "y regeneración ambiental."
)

whisper_model = None
diarize_pipeline = None

def get_whisper():
    global whisper_model
    if whisper_model is None:
        print(f"[Worker] Cargando Faster-Whisper large-v3 en {DEVICE} ({COMPUTE_TYPE})...")
        whisper_model = WhisperModel("large-v3", device=DEVICE, compute_type=COMPUTE_TYPE)
    return whisper_model

def get_diarization(hf_token: str = None):
    global diarize_pipeline
    token = (hf_token or HF_TOKEN or "").strip()
    if not token:
        return None
    if diarize_pipeline is None:
        print("[Worker] Cargando Pyannote Audio Diarization 3.1...")
        try:
            diarize_pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=token
            )
            if torch.cuda.is_available():
                diarize_pipeline.to(torch.device("cuda"))
        except Exception as e:
            print(f"[Worker] Error cargando Pyannote: {e}")
            return None
    return diarize_pipeline

@app.get("/")
@app.get("/docs")
@app.get("/health")
def health():
    return {
        "status": "healthy",
        "device": DEVICE,
        "cuda_available": torch.cuda.is_available(),
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "N/A"
    }

def convert_to_wav_16k_mono(input_bytes: bytes) -> np.ndarray:
    """
    Convierte cualquier archivo multimedia a WAV 16kHz mono usando ffmpeg
    y lo carga en memoria como array float32.
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
    Agrupación temporal inteligente:
    Fusiona turnos contiguos del mismo orador si la pausa entre ellos es menor a pause_threshold (1.0 s).
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

        print(f"[Worker] Procesando audio recibido ({len(content)} bytes)...")
        data = convert_to_wav_16k_mono(content)
        sample_rate = 16000
        total_duration = len(data) / float(sample_rate)

        # 1. Diarización In-Memory con Pyannote 3.1
        diar_pipe = get_diarization(hf_token)
        raw_turns = []
        if diar_pipe:
            waveform = torch.from_numpy(data).unsqueeze(0)
            audio_in_memory = {"waveform": waveform, "sample_rate": 16000}
            kwargs = {}
            if min_speakers: kwargs["min_speakers"] = int(min_speakers)
            if max_speakers: kwargs["max_speakers"] = int(max_speakers)
            
            print("[Worker] Ejecutando diarización en GPU con Pyannote...")
            diar_out = diar_pipe(audio_in_memory, **kwargs)
            for turn, _, speaker in diar_out.itertracks(yield_label=True):
                raw_turns.append({
                    "start": round(float(turn.start), 3),
                    "end": round(float(turn.end), 3),
                    "speaker": speaker
                })
        else:
            print("[Worker] Sin Pyannote token: asignando orador por defecto.")
            raw_turns.append({"start": 0.0, "end": total_duration, "speaker": "SPEAKER_00"})

        # 2. Agrupación Temporal Inteligente (pausas < 1.0 s)
        merged_turns = merge_speaker_turns(raw_turns, pause_threshold=1.0)
        print(f"[Worker] Diarización agrupada en {len(merged_turns)} intervenciones.")

        # 3. Clasificador Bilingüe Forzado (es vs en) & Inferencia por segmentos
        whisper = get_whisper()
        out_segments = []

        for turn_idx, turn in enumerate(merged_turns):
            spk = turn["speaker"]
            t_start = turn["start"]
            t_end = turn["end"]

            # Padding Acústico (+- 200 ms)
            pad_sec = 0.20
            p_start = max(0.0, t_start - pad_sec)
            p_end = min(total_duration, t_end + pad_sec)

            idx_start = int(p_start * sample_rate)
            idx_end = int(p_end * sample_rate)
            chunk = data[idx_start:idx_end]

            if len(chunk) < int(0.25 * sample_rate):  # Descartar ruidos < 250ms
                continue

            # Detección de idioma con Clasificador Bilingüe Forzado (es vs en)
            try:
                sample_chunk = chunk[:int(30 * sample_rate)]
                mel = whisper.feature_extractor(sample_chunk)
                encoder_output = whisper.model.encode(mel)
                lang_probs = whisper.model.detect_language(encoder_output)
                
                prob_dict = {l.split("/")[-1]: p for l, p in lang_probs}
                prob_es = prob_dict.get("es", 0.0)
                prob_en = prob_dict.get("en", 0.0)

                assigned_lang = "en" if prob_en > prob_es else "es"
            except Exception:
                assigned_lang = "es"

            # Inferencia con Context Prompt
            segments_gen, _ = whisper.transcribe(
                chunk,
                beam_size=5,
                temperature=0.0,
                condition_on_previous_text=False,
                word_timestamps=True,
                language=assigned_lang,
                initial_prompt=INITIAL_PROMPT
            )

            seg_text_list = []
            seg_words = []

            for s in segments_gen:
                t_words = []
                if hasattr(s, "words") and s.words:
                    for w in s.words:
                        w_abs_start = round(p_start + float(w.start), 2)
                        w_abs_end = round(p_start + float(w.end), 2)
                        t_words.append({
                            "word": w.word,
                            "start": w_abs_start,
                            "end": w_abs_end,
                            "speaker": spk,
                            "probability": getattr(w, "probability", 1.0)
                        })
                
                seg_words.extend(t_words)
                if s.text and s.text.strip():
                    seg_text_list.append(s.text.strip())

            full_seg_text = " ".join(seg_text_list).strip()
            if not full_seg_text and not seg_words:
                continue

            # 4. Cálculo Matemático de Pausas e Interrupciones en el texto
            annotated_text_words = []
            for i in range(len(seg_words)):
                current_word = seg_words[i]
                annotated_text_words.append(current_word["word"].strip())
                if i < len(seg_words) - 1:
                    next_word = seg_words[i + 1]
                    gap = next_word["start"] - current_word["end"]
                    if gap >= 1.2:
                        annotated_text_words.append("...")

            computed_text = " ".join(annotated_text_words) if annotated_text_words else full_seg_text

            # 5. Detección de solapamiento de oradores [os]
            is_overlapping = False
            for other_turn in raw_turns:
                if other_turn["speaker"] != spk:
                    overlap = max(0.0, min(t_end, other_turn["end"]) - max(t_start, other_turn["start"]))
                    if overlap >= 0.2:
                        is_overlapping = True
                        break
            
            if is_overlapping and "[os]" not in computed_text:
                computed_text = f"{computed_text} [os]"

            out_segments.append({
                "start": round(t_start, 2),
                "end": round(t_end, 2),
                "text": computed_text,
                "speaker": spk,
                "language": assigned_lang,
                "words": seg_words
            })

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        return JSONResponse({
            "status": "success",
            "duration": round(total_duration, 2),
            "segments": out_segments
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
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
        self._action_lock = threading.Lock()

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
