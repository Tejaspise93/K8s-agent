"""
watcher.py -- The cluster observer.

Connects to k8s cluster via your local kubeconfig file (the same one kubectl uses).
Polls the Kubernetes API every N seconds(set in config.yaml) and returns a snapshot of
what's happening in the cluster - pod states, restart counts, exit reasons.

"""

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime
from agent.config import cfg
from agent.utils import parse_container_statuses


def connect_to_cluster():
    """
    Load kubeconfig from the default location (~/.kube/config).
    Returns a CoreV1Api client we use for all pod/namespace queries.
    """
    # load_kube_config() reads ~/.kube/config automatically.
    # If you have multiple clusters, it picks the current-context --
    config.load_kube_config()
    return client.CoreV1Api()


def get_pod_snapshot(core_api, namespace="default"):
    """
    Poll the Kubernetes API and return a list of pod snapshots.

    Each pod becomes a dict with the fields we care about for anomaly detection.
    We extract exit reasons and OOMKilled flags from container statuses.

    Args:
        core_api: a connected CoreV1Api client (from connect_to_cluster)
        namespace: which namespace to watch.

    Returns:
        {
            "name": "my-app-7d9f8b-xkp2q",
            "namespace": "default",
            "phase": "Running",
            "ready": True,
            "restarts": 3,
            "reason": "OOMKilled",   # or None if no abnormal exit
            "node": "minikube",
            "age_seconds": 3620
        }
    """
    try:
        pod_list = core_api.list_namespaced_pod(namespace=namespace)
    except ApiException as e:
        print(f"[watcher] Kubernetes API error: {e.status} {e.reason}")
        return []

    pods = []

    for pod in pod_list.items:
        name = pod.metadata.name
        ns = pod.metadata.namespace

        # phase is the high-level state: Pending, Running, Succeeded, Failed, Unknown
        phase = pod.status.phase or "Unknown"

        # Calculate how long the pod has been alive
        creation_time = pod.metadata.creation_timestamp
        if creation_time:
            age_seconds = (datetime.now(creation_time.tzinfo) - creation_time).total_seconds()
        else:
            age_seconds = 0

        # Node this pod is scheduled on (None if still Pending)
        node = pod.spec.node_name or "unscheduled"

        # Extract container-level details using the shared helper
        total_restarts, all_ready, exit_reason = parse_container_statuses(
            pod.status.container_statuses
        )

        pods.append({
            "name": name,
            "namespace": ns,
            "phase": phase,
            "ready": all_ready,
            "restarts": total_restarts,
            "reason": exit_reason,   # None means normal / no abnormal exit
            "node": node,
            "age_seconds": int(age_seconds),
        })

    return pods


def detect_anomalies(pods):
    """
    These are the conditions the agent needs to investigate:
    CrashLoopBackOff, OOMKilled, 
    Pending too long: pod can't be scheduled (resource crunch, taint mismatch, etc.),
    High restart count

    Returns a list of anomaly dicts -- one per problematic pod.
    """
    anomalies = []

    for pod in pods:
        reason = pod["reason"] or ""
        phase = pod["phase"]
        restarts = pod["restarts"]

        if reason == "CrashLoopBackOff":
            anomalies.append({
                "pod": pod["name"],
                "namespace": pod["namespace"],
                "type": "CrashLoopBackOff",
                "detail": f"Restart count: {restarts}",
                "severity": "HIGH",
            })

        elif reason == "OOMKilled":
            anomalies.append({
                "pod": pod["name"],
                "namespace": pod["namespace"],
                "type": "OOMKilled",
                "detail": f"Restart count: {restarts}",
                "severity": "HIGH",
            })

        elif phase == "Pending" and pod["age_seconds"] > cfg.pending_duration_seconds:
            # Only flag Pending pods that have been stuck for over pending_duration_seconds(set in config.yaml).
            anomalies.append({
                "pod": pod["name"],
                "namespace": pod["namespace"],
                "type": "PendingTooLong",
                "detail": f"Pending for {pod['age_seconds']}s",
                "severity": "MEDIUM",
            })

        elif restarts > cfg.restart_count_threshold and reason not in ("CrashLoopBackOff", "OOMKilled"):
            # High restart count without a specific named reason
            anomalies.append({
                "pod": pod["name"],
                "namespace": pod["namespace"],
                "type": "HighRestartCount",
                "detail": f"Restart count: {restarts}, reason: {reason or 'unknown'}",
                "severity": "MEDIUM",
            })

    return anomalies