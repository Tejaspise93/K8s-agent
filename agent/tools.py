"""
tools.py -- The agent's hands.

Nine functions the model can call to investigate and fix problems.
The model never calls them directly, it tells the agent loop which tool to call,
and the agent loop calls it, then sends the result back to the model.

Each function returns either a dict or a string.
Strings are used for error messages so the model can reason about failures too.
"""

from kubernetes import client, config
from kubernetes.client.rest import ApiException
from agent.utils import parse_container_statuses


# ---------------------------------------------------------------------------
# Client factory -- kubeconfig is loaded once, clients are created per-call
# ---------------------------------------------------------------------------

_kube_config_loaded: bool = False


def _ensure_kube_config() -> None:
    """Load kubeconfig once. Subsequent calls are no-ops."""
    global _kube_config_loaded
    if not _kube_config_loaded:
        config.load_kube_config()
        _kube_config_loaded = True


def _get_clients() -> tuple[client.CoreV1Api, client.AppsV1Api]:
    """
    Returns connected K8s API clients.
    two API groups:
      - CoreV1Api: pods, namespaces, events, nodes
      - AppsV1Api: deployments, replicasets
    """
    _ensure_kube_config()
    core = client.CoreV1Api()
    apps = client.AppsV1Api()
    return core, apps


# ---------------------------------------------------------------------------
# READ TOOLS -- gather information, no cluster changes
# ---------------------------------------------------------------------------

def get_cluster_state(namespace: str = "default") -> dict | str:
    """
    Tool 1 -- Snapshot of all pods in the namespace.
    Returns a list of dicts, one per pod.
    This will be called by LLM first to get the big picture.
    """
    core, _ = _get_clients()

    try:
        pod_list = core.list_namespaced_pod(namespace=namespace)
    except ApiException as e:
        return f"Error fetching cluster state: {e.status} {e.reason}"

    pods = []
    for pod in pod_list.items:
        restarts, ready, reason = parse_container_statuses(
            pod.status.container_statuses
        )

        pods.append({
            "name": pod.metadata.name,
            "phase": pod.status.phase or "Unknown",
            "ready": ready,
            "restarts": restarts,
            "reason": reason,
            "node": pod.spec.node_name or "unscheduled",
        })

    return {"namespace": namespace, "pod_count": len(pods), "pods": pods}


def get_pod_logs(pod_name: str, namespace: str = "default", tail_lines: int = 50) -> dict | str:
    """
    Tool 2 -- Fetch the last N lines of logs from a pod.
    tail_lines=50 keeps the response small enough for the LLM context window.
    """
    core, _ = _get_clients()

    try:
        logs = core.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            tail_lines=tail_lines,
            # timestamps=True adds a timestamp to each log line -- useful for
            # understanding when crashes happened
            timestamps=True,
        )
        return {
            "pod": pod_name,
            "namespace": namespace,
            "tail_lines": tail_lines,
            "logs": logs if logs else "(no logs available)",
        }
    except ApiException as e:
        return f"Error fetching logs for {pod_name}: {e.status} {e.reason}"


def get_pod_events(pod_name: str, namespace: str = "default") -> dict | str:
    """
    Tool 3 -- Fetch Kubernetes events related to a specific pod.

    """
    core, _ = _get_clients()

    try:
        # Events are cluster-wide resources filtered by a field selector.
        # involvedObject.name filters to events about our specific pod.
        all_events = core.list_namespaced_event(
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}"
        )
    except ApiException as e:
        return f"Error fetching events for {pod_name}: {e.status} {e.reason}"

    events = []
    for evt in all_events.items:
        events.append({
            "type": evt.type,           # Normal or Warning
            "reason": evt.reason,       # e.g. "OOMKilling", "BackOff", "Pulled"
            "message": evt.message,     # Human-readable description
            "count": evt.count,         # How many times this event fired
            "first_time": str(evt.first_timestamp),
            "last_time": str(evt.last_timestamp),
        })

    return {
        "pod": pod_name,
        "namespace": namespace,
        "event_count": len(events),
        "events": events,
    }


def get_resource_usage(pod_name: str, namespace: str = "default") -> dict | str:
    """
    Tool 4 -- Live CPU and memory usage from the metrics server.

    This is needed to diagnose OOMKilled and high CPU --
    see exactly how close the container is to its limit.

    Uses the CustomObjectsApi because metrics are not part of the core K8s API --
    they live in the metrics.k8s.io API group.
    """
    _ensure_kube_config()
    custom = client.CustomObjectsApi()

    try:
        # metrics.k8s.io/v1beta1 is the standard metrics API path
        metrics = custom.get_namespaced_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            namespace=namespace,
            plural="pods",
            name=pod_name,
        )
    except ApiException as e:
        if e.status == 404:
            return (
                f"Metrics not available for {pod_name}. "
                "metrics-server may still be starting -- wait 60s and retry."
            )
        return f"Error fetching metrics for {pod_name}: {e.status} {e.reason}"

    containers = []
    for c in metrics.get("containers", []):
        containers.append({
            "name": c["name"],
            # Values come back as strings like "125m" (millicores) or "64Mi"
            "cpu": c["usage"].get("cpu", "unknown"),
            "memory": c["usage"].get("memory", "unknown"),
        })

    return {
        "pod": pod_name,
        "namespace": namespace,
        "containers": containers,
    }


def get_resource_limits(pod_name: str, namespace: str = "default") -> dict | str:
    """
    Tool 5 -- The configured CPU and memory requests/limits for a pod.

    Paired with get_resource_usage(), this tells the LLM:
    - What the container is currently using
    - What it's allowed to use
    - How close it is to being OOMKilled again

    """
    core, _ = _get_clients()

    try:
        pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
    except ApiException as e:
        return f"Error fetching pod spec for {pod_name}: {e.status} {e.reason}"

    containers = []
    for c in pod.spec.containers:
        resources = c.resources or client.V1ResourceRequirements()
        requests = resources.requests or {}
        limits = resources.limits or {}

        containers.append({
            "name": c.name,
            "requests": {
                "cpu": requests.get("cpu", "not set"),
                "memory": requests.get("memory", "not set"),
            },
            "limits": {
                "cpu": limits.get("cpu", "not set"),
                "memory": limits.get("memory", "not set"),
            },
        })

    return {
        "pod": pod_name,
        "namespace": namespace,
        "containers": containers,
    }


def get_deployment_status(deployment_name: str, namespace: str = "default") -> dict | str:
    """
    Tool 6 -- Rollout status and replica counts for a deployment.

    Tells the LLM whether a deployment is healthy, mid-rollout, or stuck.
    Key fields:
    - desired: how many replicas you asked for
    - ready: how many are actually serving traffic
    - available: how many have passed their availability checks
    - updated: how many are running the new version (during a rollout)
    """
    _, apps = _get_clients()

    try:
        dep = apps.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )
    except ApiException as e:
        return f"Error fetching deployment {deployment_name}: {e.status} {e.reason}"

    spec = dep.spec
    status = dep.status

    return {
        "name": deployment_name,
        "namespace": namespace,
        "desired_replicas": spec.replicas,
        "ready_replicas": status.ready_replicas or 0,
        "available_replicas": status.available_replicas or 0,
        "updated_replicas": status.updated_replicas or 0,
        # conditions describe why a deployment is or isn't progressing
        "conditions": [
            {
                "type": c.type,
                "status": c.status,
                "reason": c.reason,
                "message": c.message,
            }
            for c in (status.conditions or [])
        ],
    }


# ---------------------------------------------------------------------------
# ACTION TOOLS -- these modify the cluster. Used only after investigation.
# ---------------------------------------------------------------------------

def restart_pod(pod_name: str, namespace: str = "default") -> dict:
    """
    Delete a pod so Kubernetes recreates it.
    Only safe if the pod is owned by a Deployment/ReplicaSet.
    Standalone pods will not come back after deletion.
    """
    try:
        core, _ = _get_clients()

        # Ownership check -- refuse to delete standalone pods
        pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        owner_refs = pod.metadata.owner_references

        if not owner_refs:
            return {
                "action": "restart_pod",
                "pod": pod_name,
                "namespace": namespace,
                "result": "aborted",
                "note": "Pod has no owner (standalone pod) -- deleting would destroy it permanently. Deploy via a Deployment instead."
            }

        core.delete_namespaced_pod(name=pod_name, namespace=namespace)
        return {
            "action": "restart_pod",
            "pod": pod_name,
            "namespace": namespace,
            "result": "success",
            "note": "Pod deleted -- Deployment will recreate it automatically."
        }

    except Exception as e:
        return {
            "action": "restart_pod",
            "pod": pod_name,
            "namespace": namespace,
            "result": "error",
            "note": str(e)
        }


def scale_deployment(deployment_name: str, namespace: str = "default", replicas: int = 1) -> dict | str:
    """
    Tool 8 -- Change the replica count of a deployment.

    Used to scale up when a deployment is under-resourced, or scale to 0
    as an emergency stop (like a circuit breaker).
    Uses a patch instead of a full update -- only changes the replica count,
    leaves everything else untouched.
    """
    _, apps = _get_clients()

    try:
        # patch_namespaced_deployment_scale sends only the fields we want to change.
        # The body matches the structure of a Scale object -- just replicas needed.
        apps.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body={"spec": {"replicas": replicas}},
        )
        return {
            "action": "scale_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "new_replica_count": replicas,
            "result": "success",
        }
    except ApiException as e:
        return f"Error scaling deployment {deployment_name}: {e.status} {e.reason}"


def rollback_deployment(deployment_name: str, namespace: str = "default") -> dict:
    """
    Tool 9 -- Roll a deployment back to its previous version.

    Finds all ReplicaSets owned by the deployment, sorts them by
    revision number, and patches the deployment's pod template to
    match the second-latest ReplicaSet (the previous revision).

    This is the API equivalent of `kubectl rollout undo`.
    Only works if there IS a previous version to roll back to.
    """
    try:
        _, apps = _get_clients()

        # List all ReplicaSets in the namespace
        rs_list = apps.list_namespaced_replica_set(namespace=namespace)

        # Filter to ReplicaSets owned by this deployment
        owned_rs: list[tuple[int, object]] = []
        for rs in rs_list.items:
            if rs.metadata.owner_references:
                for ref in rs.metadata.owner_references:
                    if ref.kind == "Deployment" and ref.name == deployment_name:
                        revision = int(
                            rs.metadata.annotations.get(
                                "deployment.kubernetes.io/revision", "0"
                            )
                        )
                        owned_rs.append((revision, rs))

        if len(owned_rs) < 2:
            return {
                "action": "rollback_deployment",
                "deployment": deployment_name,
                "namespace": namespace,
                "result": "aborted",
                "note": "No previous revision found -- nothing to rollback to.",
            }

        # Sort by revision descending; [0] = current, [1] = previous
        owned_rs.sort(key=lambda x: x[0], reverse=True)
        prev_revision, previous_rs = owned_rs[1]

        # Convert the previous RS's pod template to a dict for patching
        api_client = client.ApiClient()
        template_dict = api_client.sanitize_for_serialization(
            previous_rs.spec.template
        )

        # Patch the deployment to use the previous template
        apps.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body={"spec": {"template": template_dict}},
        )

        return {
            "action": "rollback_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "result": "initiated",
            "note": f"Rolled back to revision {prev_revision} -- check deployment status to confirm.",
        }

    except ApiException as e:
        return {
            "action": "rollback_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "result": "error",
            "note": f"{e.status} {e.reason}",
        }
    except Exception as e:
        return {
            "action": "rollback_deployment",
            "deployment": deployment_name,
            "namespace": namespace,
            "result": "error",
            "note": str(e),
        }