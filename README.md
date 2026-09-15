# TranscriberStudio Pro (100% Local & Confidential)

Sistema integral de asistencia a la transcripción médica, académica y legal, ejecutado 100% en local sin servicios en la nube para garantizar absoluta privacidad y confidencialidad.

---

## 🛠️ Requisitos Previos
- **Python 3.10+**
- **Node.js 18+** y npm
- **ffmpeg** (necesario para el procesamiento de audio con Whisper):
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`

---

## 🚀 Puesta en Marcha

### 1. Backend (Django + WhisperX + Pyannote + python-docx)

1. Abre una terminal y sitúate en la carpeta `backend`:
   ```bash
   cd backend
   ```
2. Crea y activa un entorno virtual de Python:
   ```bash
   python3 -m venv venv
   source venv/bin/activate    # En Windows: venv\Scripts\activate
   ```
3. Instala las dependencias:
   ```bash
   pip install -r requirements.txt
   ```
4. **(Opcional pero recomendado para Diarización de Hablantes)**:
   Acepta los términos de uso en Hugging Face para:
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
   
   Y exporta tu token en tu terminal o introdúcelo directamente en la interfaz web:
   ```bash
   export HF_TOKEN="tu_huggingface_token"
   ```

5. Ejecuta las migraciones de base de datos e inicializa el servidor:
   ```bash
   python manage.py makemigrations transcription
   python manage.py migrate
   python manage.py runserver 8000
   ```
   El backend estará disponible en `http://localhost:8000`.

---

### 2. Frontend (Vue 3 + Vite)

1. En una nueva terminal, entra en la carpeta `frontend`:
   ```bash
   cd frontend
   ```
2. Instala las dependencias:
   ```bash
   npm install
   ```
3. Inicia el servidor de desarrollo:
   ```bash
   npm run dev
   ```
4. Abre tu navegador en `http://localhost:5174` (o en el siguiente puerto que Vite asigne automáticamente si 5174 estuviera ocupado).

---

## ⚡ Características Implementadas
- **Motor WhisperX Local:**
  - **Fase 1 (Transcripción):** Modelo `large-v3` para máxima riqueza léxica y exactitud acústica.
  - **Fase 2 (Alineación Fonética):** Modelos `Wav2Vec2` para marcas de tiempo fonéticas palabra por palabra milimétricas.
  - **Fase 3 (Diarización de Hablantes):** `pyannote/speaker-diarization-3.1` para identificación y separación precisa de interlocutores (`I`, `R`, `R1`, etc.).
  - **Optimización de Memoria:** Recolección de basura forzada (`del model; gc.collect()`) por fase para ejecución estable en CPU de 16 GB RAM con `batch_size=4`.
- **Modo Karaoke:** Resaltado interactivo de palabras en tiempo real (`currentTime >= start && currentTime <= end`), con salto instantáneo al audio al hacer clic en cualquier palabra.
- **Velocidades Extendidas:** `0.5x` a `2.75x` con botones de acceso directo.
- **Atajos de Teclado:**
  - `Espacio`: Play / Pause.
  - `Ctrl + Flecha Izquierda` / `Cmd + ←`: Retroceder 3 segundos.
  - `Ctrl + Flecha Derecha` / `Cmd + →`: Avanzar 3 segundos.
- **Barra de Inserción Rápida de Notaciones:**
  - `(-)`: Palabra inaudible con timestamp automático actual `[H:MM:SS]`.
  - `(--)`: Fragmento inaudible con timestamp automático actual `[H:MM:SS]`.
  - `(palabra)`: Palabra dudosa.
  - `[os]`: Overlapping speech.
  - `[qs]`: Quiet speech.
  - `[us]`: Unclear speech.
  - Subrayado rápido para palabras con énfasis (`<u>...</u>`).
- **Exportación Estricta a Microsoft Word (.docx):**
  - Tipografía global: **Verdana**, tamaño **8 pt**.
  - Cabecera obligatoria idéntica a la plantilla corporativa.
  - Regla estricta: Una línea en blanco entre oradores.
  - Nombre del archivo descargado idéntico al audio subido (ej: `mi_audio.docx`).
