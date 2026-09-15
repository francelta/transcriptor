from rest_framework import serializers
from .models import TranscriptionJob

class TranscriptionJobSerializer(serializers.ModelSerializer):
    audio_url = serializers.SerializerMethodField()

    class Meta:
        model = TranscriptionJob
        fields = [
            'id',
            'audio_file',
            'audio_url',
            'original_filename',
            'duration_seconds',
            'created_at',
            'status',
            'metadata_header',
            'transcript_blocks',
            'words_data',
            'logs',
            'error_message',
        ]
        read_only_fields = ['id', 'created_at', 'duration_seconds', 'words_data', 'logs', 'error_message', 'audio_url']

    def get_audio_url(self, obj):
        request = self.context.get('request')
        if obj.audio_file:
            if request:
                return request.build_absolute_uri(obj.audio_file.url)
            return obj.audio_file.url
        return None


from .models import RunPodConfig

class RunPodConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunPodConfig
        fields = [
            'runpod_api_key',
            'pod_id',
            'pod_host',
            'pod_ssh_port',
            'ssh_key_path',
            'hf_token',
            'local_proxy_port',
            'updated_at'
        ]

