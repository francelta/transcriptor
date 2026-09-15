import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Cargar variables de entorno desde .env si existe
env_file = BASE_DIR / '.env'
if env_file.exists():
    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)

SECRET_KEY = 'django-insecure-local-transcription-studio-app-key-change-in-prod'

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'corsheaders',
    'rest_framework',
    # Local apps
    'transcription',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# CORS setup for local SPA development
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True

# Max upload size (allow audio files up to 500MB)
DATA_UPLOAD_MAX_MEMORY_SIZE = 524288000
FILE_UPLOAD_MAX_MEMORY_SIZE = 524288000

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Hugging Face Token para modelos de diarización Pyannote en WhisperX
# (Requiere aceptar los términos de uso en pyannote/speaker-diarization-3.1 y pyannote/segmentation-3.0)
HF_TOKEN = os.environ.get('HF_TOKEN', '')

# Configuración del LLM para la capa semántica de post-procesado
LLM_API_URL = os.environ.get('LLM_API_URL', 'http://localhost:11434/v1/chat/completions')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', 'llama3:latest')

# Configuración del worker remoto GPU en RunPod y auto-shutdown
RUNPOD_POD_ID = os.environ.get('RUNPOD_POD_ID', 'k7hmz605lbejrn')
GPU_WORKER_URL = os.environ.get('GPU_WORKER_URL', 'https://k7hmz605lbejrn-8000.proxy.runpod.net/transcribe')
RUNPOD_API_KEY = os.environ.get('RUNPOD_API_KEY', 'PEGA_AQUÍ_TU_API_KEY')
# Evitar fallbacks silenciosos a CPU local si el worker remoto falla
ALLOW_LOCAL_FALLBACK = os.environ.get('ALLOW_LOCAL_FALLBACK', 'false').lower() in ['true', '1', 'yes']
