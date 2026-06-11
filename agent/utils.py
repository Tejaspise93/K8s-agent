"""
utils.py -- Shared helpers used across the agent package.
"""

import re
from typing import Optional


def get_deployment_name(pod_name: str) -> str:
    """
    Extract the deployment name from a pod name by stripping the
    ReplicaSet and pod hash suffixes.

    Kubernetes pod names follow the pattern:
        <deployment>-<rs-hash>-<pod-hash>
    where rs-hash is 9-10 alphanumeric chars and pod-hash is 5 chars.
    """
    parts = pod_name.split("-")
    if len(parts) >= 3:
        pod_hash = parts[-1]
        rs_hash  = parts[-2]
        if (re.fullmatch(r"[a-z0-9]{5}", pod_hash) and
                re.fullmatch(r"[a-z0-9]{9,10}", rs_hash)):
            return "-".join(parts[:-2])
    return pod_name


def parse_container_statuses(container_statuses) -> tuple[int, bool, Optional[str]]:
    """
    Shared helper -- extracts restart count, readiness, and exit reason
    from a list of V1ContainerStatus objects.

    Used by both watcher.get_pod_snapshot() and tools.get_cluster_state()
    to avoid duplicating the same parsing logic.

    Returns:
        (total_restarts, all_ready, exit_reason)
    """
    total_restarts = 0
    exit_reason = None
    all_ready = True

    if container_statuses:
        for cs in container_statuses:
            total_restarts += cs.restart_count or 0

            if not cs.ready:
                all_ready = False

            if cs.state.waiting and cs.state.waiting.reason:
                exit_reason = cs.state.waiting.reason
            elif cs.state.terminated and cs.state.terminated.reason:
                exit_reason = cs.state.terminated.reason

            # Check the previous run -- captures OOMKilled etc. that
            # persist even after the container restarts.
            if cs.last_state and cs.last_state.terminated:
                last = cs.last_state.terminated
                if last.reason and not exit_reason:
                    exit_reason = last.reason

    return total_restarts, all_ready, exit_reason