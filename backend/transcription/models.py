import uuid
from django.db import models

class TranscriptionJob(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audio_file = models.FileField(upload_to='audios/')
    original_filename = models.CharField(max_length=255)
    duration_seconds = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    metadata_header = models.JSONField(default=dict)
    transcript_blocks = models.JSONField(default=list)
    words_data = models.JSONField(default=list)
    logs = models.JSONField(default=list)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.status}) - {self.id}"


class RunPodConfig(models.Model):
    """
    Configuración dinámica persistente del Pod y túnel SSH para RunPod.
    """
    runpod_api_key = models.CharField(max_length=255, blank=True, default='')
    pod_id = models.CharField(max_length=100, blank=True, default='')
    pod_host = models.CharField(max_length=255, blank=True, default='')
    pod_ssh_port = models.IntegerField(default=22)
    ssh_key_path = models.CharField(max_length=255, default='~/.ssh/id_rsa')
    hf_token = models.CharField(max_length=255, blank=True, default='')
    local_proxy_port = models.IntegerField(default=8005)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "RunPod Configuration"
        verbose_name_plural = "RunPod Configuration"

    @classmethod
    def get_solo(cls):
        """Devuelve la configuración única activa o la inicializa con variables de entorno/settings."""
        from django.conf import settings
        config, created = cls.objects.get_or_create(id=1)
        if created:
            config.runpod_api_key = getattr(settings, 'RUNPOD_API_KEY', '') or ''
            config.pod_id = getattr(settings, 'RUNPOD_POD_ID', '') or ''
            config.hf_token = getattr(settings, 'HF_TOKEN', '') or ''
            config.save()
        return config

    def __str__(self):
        return f"RunPodConfig (Pod: {self.pod_id or 'None'}, Host: {self.pod_host}:{self.pod_ssh_port})"

