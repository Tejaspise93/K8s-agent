def get_deployment_name(pod_name: str) -> str:
    """
    Strip the ReplicaSet hash and pod hash from a pod name to get
    the Deployment name.
    
    crash-test-5ddf575687-7lsgs -> crash-test
    nginx-deployment-abc12-xyz99 -> nginx-deployment
    standalone-pod -> standalone-pod (unchanged, no hashes to strip)
    """
    parts = pod_name.split("-")
    # Pod names managed by a Deployment have 2 trailing hash segments
    # ReplicaSet hash is 9-10 chars, pod hash is 5 chars
    if len(parts) >= 3:
        # Check if last two segments look like hashes (alphanumeric, no vowels typical)
        if len(parts[-1]) == 5 and len(parts[-2]) == 10:
            return "-".join(parts[:-2])
    return pod_name