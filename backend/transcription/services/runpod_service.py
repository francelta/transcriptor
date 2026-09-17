"""
backend/transcription/services/runpod_service.py
Servicio de aprovisionamiento automático de Pods GPU en RunPod.
Gestiona el ciclo completo: búsqueda de GPU económica (<0.40$/h),
despliegue via GraphQL, polling de estado y terminación.
Zero dependencias externas más allá de `requests`.
"""

import time
import logging
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

RUNPOD_GRAPHQL_ENDPOINT = "https://api.runpod.io/graphql"

# Orden de prioridad de GPUs (nombre parcial → buscar coincidencia)
GPU_PRIORITY_PATTERNS = [
    "RTX 2000 Ada",
    "RTX 3080",
    "RTX 3090",
    "RTX 4000",
    "RTX 4500",
    "L4",
]

MAX_PRICE_PER_HOUR = 0.40  # Límite estricto: abortar si ninguna GPU baja de este precio

# Imagen base oficial RunPod con PyTorch + CUDA
POD_IMAGE = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04"

# ─────────────────────────────────────────────
# Queries / Mutations GraphQL
# ─────────────────────────────────────────────

GPU_TYPES_QUERY = """
query GpuTypes {
  gpuTypes {
    id
    displayName
    memoryInGb
    lowestPrice {
      minimumBidPrice
      uninterruptablePrice
    }
  }
}
"""

DEPLOY_MUTATION = """
mutation podFindAndDeployOnDemand($input: PodFindAndDeployOnDemandInput!) {
  podFindAndDeployOnDemand(input: $input) {
    id
    imageName
    machineId
    desiredStatus
  }
}
"""

POD_STATUS_QUERY = """
query Pod($podId: String!) {
  pod(input: {podId: $podId}) {
    id
    name
    desiredStatus
    runtime {
      ports {
        ip
        publicPort
        privatePort
        type
      }
    }
  }
}
"""

TERMINATE_MUTATION = """
mutation terminatePod($podId: String!) {
  podTerminate(input: {podId: $podId})
}
"""


# ─────────────────────────────────────────────
# Helpers de conexión GraphQL
# ─────────────────────────────────────────────

def _gql_request(api_key: str, query: str, variables: Optional[Dict] = None, timeout: int = 20) -> Dict:
    """Realiza una petición GraphQL a RunPod y devuelve `data`. Lanza RuntimeError en caso de error."""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}",
    }
    payload: Dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    try:
        resp = requests.post(
            RUNPOD_GRAPHQL_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Error de red con la API GraphQL de RunPod: {exc}") from exc

    if "errors" in body:
        msgs = "; ".join(e.get("message", "Error desconocido") for e in body["errors"])
        raise RuntimeError(f"Error GraphQL RunPod: {msgs}")

    return body.get("data", {})


# ─────────────────────────────────────────────
# API Pública del Servicio
# ─────────────────────────────────────────────

def find_cheapest_gpu(api_key: str) -> Dict[str, Any]:
    """Devuelve la primera GPU prioritaria disponible. Ver find_all_affordable_gpus."""
    results = find_all_affordable_gpus(api_key)
    if not results:
        raise RuntimeError(
            f"No hay ninguna GPU On-Demand disponible por debajo de {MAX_PRICE_PER_HOUR} $/h en RunPod. "
            "Verifica la disponibilidad o ajusta el límite de precio."
        )
    return results[0]


def find_all_affordable_gpus(api_key: str) -> list:
    """
    Consulta los tipos de GPU disponibles en RunPod On-Demand y devuelve
    la lista COMPLETA de GPUs cuyo precio sea ESTRICTAMENTE inferior a 0.40 $/h,
    ordenadas por prioridad (GPU_PRIORITY_PATTERNS) y luego por precio.

    Usado para el mecanismo de fallback automático: si una GPU no tiene
    instancias libres al intentar el despliegue, se prueba la siguiente.

    Returns:
        Lista de dicts [{"id", "displayName", "price", "memoryInGb"}, ...]
        Lista vacía si ninguna GPU supera el filtro.
    """
    logger.info("Consultando GPU types disponibles en RunPod GraphQL...")
    data = _gql_request(api_key, GPU_TYPES_QUERY)

    gpu_types: List[Dict] = data.get("gpuTypes") or []
    if not gpu_types:
        raise RuntimeError("RunPod GraphQL no devolvió ningún tipo de GPU disponible.")

    affordable: List[Dict] = []
    for gpu in gpu_types:
        lowest = gpu.get("lowestPrice") or {}
        price = lowest.get("uninterruptablePrice")
        if price is not None and price < MAX_PRICE_PER_HOUR:
            affordable.append({
                "id": gpu["id"],
                "displayName": gpu.get("displayName", gpu["id"]),
                "price": price,
                "memoryInGb": gpu.get("memoryInGb", 0),
            })

    if not affordable:
        return []

    logger.info(f"GPUs asequibles encontradas: {[g['displayName'] for g in affordable]}")

    # Ordenar por prioridad (GPU_PRIORITY_PATTERNS) primero, luego por precio
    def priority_key(gpu):
        name = gpu["displayName"].lower()
        gpu_id = gpu["id"].lower()
        for idx, pattern in enumerate(GPU_PRIORITY_PATTERNS):
            if pattern.lower() in name or pattern.lower() in gpu_id:
                return (idx, gpu["price"])
        return (len(GPU_PRIORITY_PATTERNS), gpu["price"])  # sin prioridad → al final

    affordable.sort(key=priority_key)
    summary_list = [f"{g['displayName']} ({g['price']:.3f}$/h)" for g in affordable]
    logger.info(f"GPUs ordenadas por prioridad: {summary_list}")
    return affordable


def provision_pod(api_key: str, gpu_type_id: str) -> str:
    """
    Despliega un nuevo Pod On-Demand en RunPod con:
      - Imagen: runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04
      - 20 GB de disco de contenedor, 0 GB de volumen de red
      - Puerto privado 22 expuesto como TCP público

    Returns:
        pod_id (str) del pod creado.
    """
    logger.info(f"Desplegando pod On-Demand con GPU: {gpu_type_id}...")

    variables = {
        "input": {
            "name": "TranscriberStudio-Worker",
            "gpuTypeId": gpu_type_id,
            "imageName": POD_IMAGE,
            "gpuCount": 1,
            "volumeInGb": 0,
            "containerDiskInGb": 20,
            "ports": "22/tcp",
            "startSsh": True,
            "supportPublicIp": True,
            "cloudType": "ALL",
        }
    }

    data = _gql_request(api_key, DEPLOY_MUTATION, variables=variables, timeout=30)
    pod = data.get("podFindAndDeployOnDemand")

    if not pod or not pod.get("id"):
        raise RuntimeError(
            "La mutación podFindAndDeployOnDemand no devolvió un Pod ID válido. "
            "Puede que no haya capacidad disponible para este tipo de GPU."
        )

    pod_id = pod["id"]
    logger.info(f"Pod creado exitosamente con ID: {pod_id}")
    return pod_id


def poll_pod_until_running(
    api_key: str,
    pod_id: str,
    max_retries: int = 80,
    interval_seconds: float = 3.0,
) -> Dict[str, Any]:
    """
    Hace polling del estado del pod cada `interval_seconds` hasta que su runtime
    esté activo y devuelva IP pública + puerto SSH mapeado al 22 privado.

    Returns:
        {"pod_id": "...", "pod_host": "157.157.221.29", "pod_ssh_port": 57393}
    """
    logger.info(f"Esperando que el pod {pod_id} alcance estado RUNNING...")

    for attempt in range(1, max_retries + 1):
        logger.debug(f"Poll intento {attempt}/{max_retries} para pod {pod_id}...")
        try:
            data = _gql_request(api_key, POD_STATUS_QUERY, variables={"podId": pod_id}, timeout=15)
        except RuntimeError as exc:
            logger.warning(f"Error en poll intento {attempt}: {exc}")
            time.sleep(interval_seconds)
            continue

        pod_data = data.get("pod")
        if not pod_data:
            time.sleep(interval_seconds)
            continue

        desired_status = pod_data.get("desiredStatus", "")
        runtime = pod_data.get("runtime")

        if runtime:
            ports = runtime.get("ports", []) or []
            ssh_port_entry = None
            fallback_ip = None

            for p in ports:
                if p.get("ip"):
                    fallback_ip = p["ip"]
                if p.get("privatePort") == 22:
                    ssh_port_entry = p

            if ssh_port_entry:
                public_ip = ssh_port_entry.get("ip") or fallback_ip
                public_port = ssh_port_entry.get("publicPort")
                if public_ip and public_port:
                    logger.info(f"Pod {pod_id} RUNNING en {public_ip}:{public_port}")
                    return {
                        "pod_id": pod_id,
                        "pod_host": public_ip,
                        "pod_ssh_port": int(public_port),
                    }

        logger.debug(f"Pod {pod_id} aún no RUNNING (status: {desired_status}). Esperando {interval_seconds}s...")
        time.sleep(interval_seconds)

    total_wait = max_retries * interval_seconds
    raise RuntimeError(
        f"El pod {pod_id} no alcanzó el estado RUNNING con IP pública después de {total_wait:.0f} segundos."
    )


def terminate_pod(api_key: str, pod_id: str) -> bool:
    """
    Elimina definitivamente el pod de RunPod para congelar la facturación.

    Returns:
        True si la terminación fue exitosa.
    """
    if not pod_id or not api_key:
        logger.warning("terminate_pod: pod_id o api_key vacíos. Omitiendo.")
        return False

    logger.info(f"Terminando pod {pod_id} en RunPod...")
    try:
        _gql_request(api_key, TERMINATE_MUTATION, variables={"podId": pod_id}, timeout=20)
        logger.info(f"Pod {pod_id} terminado exitosamente. Facturación congelada.")
        return True
    except RuntimeError as exc:
        logger.error(f"Error terminando pod {pod_id}: {exc}")
        raise
