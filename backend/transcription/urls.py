from django.urls import path
from .views import (
    TranscribeAudioView, 
    JobDetailView, 
    SaveTranscriptView, 
    ExportDocxView,
    JobLogsStreamView,
    CancelJobView,
    ActiveJobsView,
    KillAllActiveJobsView,
    WorkerConfigView,
    WorkerDetectView,
    WorkerHealthView,
    WorkerConnectView,
    WorkerDisconnectView,
    PodStatusView
)


urlpatterns = [
    path('transcribe/', TranscribeAudioView.as_view(), name='transcribe-audio'),
    path('transcribe/active/', ActiveJobsView.as_view(), name='active-jobs'),
    path('transcribe/kill-active/', KillAllActiveJobsView.as_view(), name='kill-active-jobs'),
    path('transcribe/<uuid:pk>/', JobDetailView.as_view(), name='job-detail'),
    path('transcribe/<uuid:pk>/events/', JobLogsStreamView.as_view(), name='job-events'),
    path('transcribe/<uuid:pk>/cancel/', CancelJobView.as_view(), name='cancel-job'),
    path('transcribe/<uuid:pk>/save/', SaveTranscriptView.as_view(), name='save-transcript'),
    path('transcribe/<uuid:pk>/export-docx/', ExportDocxView.as_view(), name='export-docx'),

    # Endpoints de Worker Remoto RunPod / Túnel SSH
    path('pod/status/', PodStatusView.as_view(), name='pod-status'),
    path('pod/connect/', WorkerConnectView.as_view(), name='pod-connect'),
    path('worker/config/', WorkerConfigView.as_view(), name='worker-config'),
    path('worker/detect/', WorkerDetectView.as_view(), name='worker-detect'),
    path('worker/health/', WorkerHealthView.as_view(), name='worker-health'),
    path('worker/connect/', WorkerConnectView.as_view(), name='worker-connect'),
    path('worker/disconnect/', WorkerDisconnectView.as_view(), name='worker-disconnect'),
]

