import logging
import requests

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

def query_runpod_instance(pod_id: str, api_key: str):
    """
    Consulta la API GraphQL de RunPod para obtener información del pod
    y extraer la IP pública y el puerto mapeado al puerto privado 22 (SSH).
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
        response = requests.post(
            RUNPOD_GRAPHQL_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        logger.error(f"Error comunicando con RunPod GraphQL API: {e}")
        raise RuntimeError(f"Error de red con la API de RunPod: {e}")

    if "errors" in data:
        err_msg = "; ".join([err.get("message", "Error desconocido") for err in data["errors"]])
        logger.error(f"Errores devueltos por RunPod GraphQL: {err_msg}")
        raise RuntimeError(f"Error de RunPod GraphQL: {err_msg}")

    pod_data = data.get("data", {}).get("pod")
    if not pod_data:
        raise RuntimeError(f"No se encontró información para el Pod ID '{pod_id}'.")

    runtime = pod_data.get("runtime")
    if not runtime:
        desired_status = pod_data.get("desiredStatus", "UNKNOWN")
        raise RuntimeError(
            f"El pod '{pod_id}' no tiene runtime activo (Estado deseado: {desired_status}). "
            "Asegúrate de que la instancia esté encendida ('RUNNING') en RunPod."
        )

    ports = runtime.get("ports", [])
    if not ports:
        raise RuntimeError(f"El pod '{pod_id}' no reporta puertos expuestos en su runtime.")

    # Buscar el puerto privado 22 (SSH)
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
            f"El mapeo SSH del pod '{pod_id}' no tiene IP o puerto público disponible (IP: {public_ip}, Port: {public_port})."
        )

    return {
        "pod_id": pod_id,
        "pod_name": pod_data.get("name", ""),
        "desired_status": pod_data.get("desiredStatus", ""),
        "pod_host": public_ip,
        "pod_ssh_port": int(public_port)
    }
