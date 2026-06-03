import re

def get_deployment_name(pod_name: str) -> str:
    parts = pod_name.split("-")
    if len(parts) >= 3:
        # Pod hash: 5 alphanum chars. ReplicaSet hash: 9-10 alphanum chars.
        pod_hash = parts[-1]
        rs_hash  = parts[-2]
        if (re.fullmatch(r"[a-z0-9]{5}", pod_hash) and
                re.fullmatch(r"[a-z0-9]{9,10}", rs_hash)):
            return "-".join(parts[:-2])
    return pod_name