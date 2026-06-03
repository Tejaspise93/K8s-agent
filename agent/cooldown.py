"""
cooldown.py -- Prevents the agent from repeating the same action on the same resource.

Tracks every action taken, blocks repeats within a cooldown window,
and escalates to alert-only after too many failed attempts.
"""

from datetime import datetime, timedelta


class CooldownTracker:
    """
    Tracks actions taken on pods and applies cooldown windows.

    Each entry tracks:
        - pod_name    : which pod the action was taken on
        - action      : what action was taken (restart_pod, scale_deployment, etc.)
        - timestamp   : when the last action was taken
        - attempts    : how many times this action has been taken on this pod

    Rules:
        1. Same action on same pod within window_seconds   -> BLOCKED
        2. Same action on same pod exceeds max_attempts    -> alert only
        3. Different action on same pod                    -> ALLOWED (ex. restarts the pod for some issue that didn't fix the issue, 
                                                      after restarts reach restart_count_threshold it checks for high restart issue)
    """

    def __init__(self, window_seconds: int = 300, max_attempts: int = 3):
    #  main.py passes cfg.cooldown_window_seconds and cfg.cooldown_max_attempts
        """
        Args:
            window_seconds: How long to block the same action on the same pod.
                            Default 300s (5 minutes).
            max_attempts:   How many times the same action can be tried on the
                            same pod before escalating to alert-only.
                            Default 3.
        """
        self.window_seconds = window_seconds
        self.max_attempts = max_attempts

        # Key: (pod_name, action) -> {"timestamp": datetime, "attempts": int}
        self._records: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_allowed(self, pod_name: str, action: str) -> tuple[bool, str]:
        """
        Check whether an action is allowed on a pod right now.

        Returns:
            (True,  "allowed")                        -- proceed normally
            (False, "cooldown")                       -- within cooldown window
            (False, "escalated")                      -- max attempts exceeded
        """
        key = (pod_name, action)

        if key not in self._records:
            return True, "allowed"

        record = self._records[key]

        # Escalation check -- too many attempts regardless of timing
        if record["attempts"] >= self.max_attempts:
            return False, "escalated"

        # Cooldown window check
        elapsed = datetime.now() - record["timestamp"]
        if elapsed < timedelta(seconds=self.window_seconds):
            remaining = self.window_seconds - int(elapsed.total_seconds())
            return False, f"cooldown:{remaining}s remaining"

        # Window has expired and not escalated -- allowed
        return True, "allowed"

    def record(self, pod_name: str, action: str) -> None:
        """
        Record that an action was just taken on a pod.
        Call this immediately after executing any action.
        """
        key = (pod_name, action)
        now = datetime.now()

        if key not in self._records:
            self._records[key] = {"timestamp": now, "attempts": 1}
        else:
            self._records[key]["timestamp"] = now
            self._records[key]["attempts"] += 1

    def get_attempts(self, pod_name: str, action: str) -> int:
        """Return how many times this action has been taken on this pod."""
        key = (pod_name, action)
        return self._records.get(key, {}).get("attempts", 0)

    def reset(self, pod_name: str, action: str) -> None:
        """
        Manually reset the cooldown for a specific pod+action pair.
        Use this when a pod recovers and you want to clear its history.
        """
        key = (pod_name, action)
        if key in self._records:
            del self._records[key]

    def reset_all(self) -> None:
        """Clear all cooldown records. Useful for testing."""
        self._records.clear()

    def status(self) -> list[dict]:
        """
        Return a readable status of all tracked pod+action pairs.
        Useful for display.py to show active cooldowns.
        """
        now = datetime.now()
        result = []

        for (pod_name, action), record in self._records.items():
            elapsed = now - record["timestamp"]
            remaining = max(0, self.window_seconds - int(elapsed.total_seconds()))
            escalated = record["attempts"] >= self.max_attempts

            result.append({
                "pod":       pod_name,
                "action":    action,
                "attempts":  record["attempts"],
                "last_seen": record["timestamp"].strftime("%H:%M:%S"),
                "remaining": remaining,
                "escalated": escalated,
            })

        return result