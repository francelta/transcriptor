import os
import time
import socket
import logging
import threading
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Código fuente del servidor de FastAPI a desplegar en el Pod (/server.py)
POD_SERVER_PY_CONTENT = '''# /server.py - TranscriberStudio Pro Remote GPU Worker
import os
import io
import gc
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
    token = hf_token or HF_TOKEN
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

@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
    hf_token: Optional[str] = Form(None)
):
    try:
        content = await file.read()
        audio_buf = io.BytesIO(content)
        data, sample_rate = sf.read(audio_buf)

        # Convertir a mono y float32
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        total_duration = len(data) / float(sample_rate)

        # Re-muestreo a 16000 Hz si es necesario para Pyannote/Whisper
        if sample_rate != 16000:
            import scipy.signal
            target_length = int(len(data) * 16000 / sample_rate)
            data = scipy.signal.resample(data, target_length)
            sample_rate = 16000

        # 1. Diarización en memoria con Pyannote
        diar_pipe = get_diarization(hf_token)
        spk_segments = []
        if diar_pipe:
            waveform = torch.from_numpy(data).unsqueeze(0)
            audio_in_memory = {"waveform": waveform, "sample_rate": 16000}
            kwargs = {}
            if min_speakers: kwargs["min_speakers"] = int(min_speakers)
            if max_speakers: kwargs["max_speakers"] = int(max_speakers)
            
            diar_out = diar_pipe(audio_in_memory, **kwargs)
            for turn, _, speaker in diar_out.itertracks(yield_label=True):
                spk_segments.append({
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker
                })

        # 2. Transcripción con Faster-Whisper (language=None para autodetección y word_timestamps)
        model = get_whisper()
        segments_gen, info = model.transcribe(
            data,
            beam_size=5,
            word_timestamps=True,
            language=None
        )

        detected_lang = info.language
        out_segments = []

        def match_speaker(start_t, end_t):
            if not spk_segments:
                return "SPEAKER_00"
            best_spk = "SPEAKER_00"
            max_overlap = 0.0
            for seg in spk_segments:
                overlap = max(0.0, min(end_t, seg["end"]) - max(start_t, seg["start"]))
                if overlap > max_overlap:
                    max_overlap = overlap
                    best_spk = seg["speaker"]
            return best_spk

        for s in segments_gen:
            spk = match_speaker(s.start, s.end)
            words_list = []
            if hasattr(s, 'words') and s.words:
                for w in s.words:
                    words_list.append({
                        "word": w.word,
                        "start": round(float(w.start), 2),
                        "end": round(float(w.end), 2),
                        "speaker": spk,
                        "probability": getattr(w, 'probability', 1.0)
                    })

            out_segments.append({
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "text": s.text.strip(),
                "speaker": spk,
                "language": detected_lang,
                "words": words_list
            })

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        return JSONResponse({
            "status": "success",
            "language": detected_lang,
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


class PodTunnelManager:
    """
    Gestor singleton de ciclo de vida, túnel SSH y auto-aprovisionamiento del pod en RunPod.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(PodTunnelManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.tunnel = None
        self.tunnel_thread = None
        self.current_host = None
        self.current_port = None
        self.local_port = 8005
        self.is_running = False
        self._action_lock = threading.Lock()

    def is_tunnel_active(self, host: str = "127.0.0.1", port: int = 8005) -> bool:
        """Verifica si el puerto local está abierto y respondiendo."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            try:
                s.connect((host, port))
                return True
            except (socket.timeout, ConnectionRefusedError, OSError):
                return False

    def check_health(self, local_port: int = 8005) -> bool:
        """Comprueba si el endpoint /docs del worker responde 200 OK."""
        try:
            r = requests.get(f"http://127.0.0.1:{local_port}/docs", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def stop_tunnel(self):
        """Detiene cualquier túnel SSH activo."""
        with self._action_lock:
            if self.tunnel:
                try:
                    logger.info("Deteniendo túnel SSH...")
                    self.tunnel.stop()
                except Exception as e:
                    logger.warning(f"Error deteniendo SSHTunnel: {e}")
                finally:
                    self.tunnel = None
            self.is_running = False

    def start_tunnel(self, host: str, ssh_port: int, ssh_key_path: str, local_port: int = 8005, remote_port: int = 8000) -> bool:
        """
        Abre el túnel SSH hacia el pod:
        127.0.0.1:{local_port} -> {host}:{ssh_port} -> 127.0.0.1:{remote_port}
        """
        import paramiko
        if not hasattr(paramiko, "DSSKey"):
            paramiko.DSSKey = None
        import sshtunnel

        expanded_key_path = os.path.expanduser(ssh_key_path)
        if not os.path.isfile(expanded_key_path):
            raise FileNotFoundError(f"La clave SSH privada no existe en: {expanded_key_path}")

        with self._action_lock:
            # Si ya está activo con los mismos parámetros y responde, reutilizarlo
            if self.is_tunnel_active("127.0.0.1", local_port) and self.current_host == host and self.current_port == ssh_port:
                logger.info(f"Túnel SSH ya activo en 127.0.0.1:{local_port}")
                self.is_running = True
                return True

            self.stop_tunnel()

            logger.info(f"Iniciando túnel SSH hacia {host}:{ssh_port} vinculando local {local_port} -> remoto {remote_port}...")
            try:
                tunnel = sshtunnel.SSHTunnelForwarder(
                    (host, int(ssh_port)),
                    ssh_username="root",
                    ssh_pkey=expanded_key_path,
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
        Conecta por SSH mediante paramiko, instala dependencias requeridas en el pod si faltan,
        escribe /server.py y levanta el servidor FastAPI en segundo plano mediante tmux.
        """
        import paramiko

        expanded_key_path = os.path.expanduser(ssh_key_path)
        if not os.path.isfile(expanded_key_path):
            raise FileNotFoundError(f"Clave SSH privada no encontrada en: {expanded_key_path}")

        logger.info(f"Conectando vía Paramiko SSH a {host}:{ssh_port} para verificar/aprovisionar el worker...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(
                hostname=host,
                port=int(ssh_port),
                username="root",
                key_filename=expanded_key_path,
                timeout=20,
                banner_timeout=20
            )
        except Exception as e:
            raise RuntimeError(f"Error conectando por SSH a root@{host}:{ssh_port}: {e}")

        logs = []
        try:
            def exec_cmd(cmd, timeout=300):
                logger.info(f"[SSH Remote CMD]: {cmd}")
                stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
                exit_status = stdout.channel.recv_exit_status()
                out = stdout.read().decode('utf-8', errors='replace').strip()
                err = stderr.read().decode('utf-8', errors='replace').strip()
                if exit_status != 0:
                    logger.warning(f"Comando '{cmd}' falló (status {exit_status}): {err}")
                return exit_status, out, err

            # 1. Comprobar e instalar tmux y librerías del sistema
            status, _, _ = exec_cmd("which tmux && which ffmpeg")
            if status != 0:
                logs.append("Instalando dependencias de sistema (ffmpeg, libsndfile1, tmux)...")
                exec_cmd("apt-get update -y && apt-get install -y ffmpeg libsndfile1 tmux", timeout=180)

            # 2. Comprobar dependencias de Python
            status, _, _ = exec_cmd("python3 -c 'import faster_whisper, pyannote.audio, soundfile, fastapi, uvicorn'")
            if status != 0:
                logs.append("Instalando dependencias Python (faster-whisper, pyannote.audio, soundfile, fastapi, uvicorn)...")
                exec_cmd("pip install --no-cache-dir faster-whisper pyannote.audio soundfile fastapi uvicorn scipy", timeout=300)

            # 3. Escribir /server.py en el pod
            logs.append("Escribiendo script de servidor /server.py...")
            sftp = ssh.open_sftp()
            with sftp.file('/server.py', 'w') as f:
                f.write(POD_SERVER_PY_CONTENT)
            sftp.close()

            # 4. Lanzar servidor worker en tmux
            logs.append("Iniciando o reiniciando sesión tmux 'worker'...")
            # Cerrar sesión vieja si existe
            exec_cmd("tmux kill-session -t worker 2>/dev/null || true")

            # Lanzar con HF_TOKEN en el entorno
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
        Flujo completo:
        1. Comprueba si el worker ya responde en localhost:{local_port}/docs.
        2. Si no responde, abre el túnel SSH.
        3. Si sigue sin responder, conecta por SSH y auto-aprovisiona el pod.
        4. Espera confirmación HTTP 200 OK.
        """
        # Si el túnel está activo y responde OK
        if self.is_tunnel_active("127.0.0.1", local_port) and self.check_health(local_port):
            return {
                "status": "connected",
                "message": f"Worker GPU respondiendo 200 OK en 127.0.0.1:{local_port}/docs"
            }

        # Intentar abrir el túnel si no está activo o si fallaba
        try:
            self.start_tunnel(host, ssh_port, ssh_key_path, local_port=local_port)
        except Exception as e:
            return {
                "status": "disconnected",
                "error": f"Fallo al abrir túnel SSH: {e}"
            }

        # Verificar si tras abrir el túnel ya responde
        time.sleep(1)
        if self.check_health(local_port):
            return {
                "status": "connected",
                "message": f"Worker GPU conectado y respondiendo 200 OK en 127.0.0.1:{local_port}/docs"
            }

        # Si aún no responde 200 OK, auto-aprovisionar vía SSH
        logger.info(f"Worker en {host}:{ssh_port} no responde 200 OK. Iniciando auto-aprovisionamiento...")
        try:
            prov_res = self.provision_worker_via_ssh(host, ssh_port, ssh_key_path, hf_token=hf_token)
        except Exception as prov_err:
            return {
                "status": "error",
                "error": f"Error en auto-aprovisionamiento SSH: {prov_err}"
            }

        # Polling hasta 30 segundos para confirmar 200 OK en /docs
        max_attempts = 15
        for attempt in range(max_attempts):
            time.sleep(2)
            if self.check_health(local_port):
                return {
                    "status": "connected",
                    "provisioned": True,
                    "details": prov_res,
                    "message": f"Worker GPU aprovisionado exitosamente y respondiendo 200 OK en 127.0.0.1:{local_port}/docs"
                }

        return {
            "status": "warning",
            "error": "El worker fue lanzado en tmux pero no respondió 200 OK en /docs en el tiempo esperado."
        }
