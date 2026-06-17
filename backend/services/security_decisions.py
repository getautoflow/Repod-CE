"""
Persistance des décisions de sécurité RSSI.

Chaque décision est stockée dans :
  /repos/security/decisions/{name}_{version}_{arch}.json

Cycle de vie d'un paquet soumis à révision :

  pending_review   → le RSSI doit agir
  ├── accept_risk  → paquet promu dans APT, risque accepté formellement
  ├── exception    → exception temporaire avec date d'expiration
  ├── reject       → quarantaine définitive
  └── upgrade_req  → en attente de la version patchée (reste hors APT)

Les décisions "accept_risk" et "exception" ont une date d'expiration.
À expiration, le statut repasse à "pending_review" (géré par le scheduler).
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DECISIONS_DIR = Path(os.getenv("SECURITY_CACHE_DIR", "/repos/security")) / "decisions"
DECISIONS_DIR.mkdir(parents=True, exist_ok=True)

# Actions valides
VALID_ACTIONS = {"accept_risk", "exception", "reject", "upgrade_required"}

# Statuts de manifest résultants par action
ACTION_TO_STATUS = {
    "accept_risk":      "accepted_risk",
    "exception":        "exception",
    "reject":           "quarantined",
    "upgrade_required": "upgrade_required",
}


def _decision_path(name: str, version: str, arch: str) -> Path:
    version_safe = version.replace(":", "_").replace("/", "_")
    return DECISIONS_DIR / f"{name}_{version_safe}_{arch}.json"


def save_decision(
    name: str,
    version: str,
    arch: str,
    action: str,
    justification: str,
    decided_by: str,
    expires_in_days: int | None = None,
    target_version: str | None = None,
    cve_ids: list[str] | None = None,
) -> dict:
    """
    Persiste une décision RSSI et retourne le document complet.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Action invalide : {action}. Valeurs : {VALID_ACTIONS}")

    now = datetime.now(timezone.utc)
    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = (now + timedelta(days=expires_in_days)).isoformat()

    decision = {
        "package":        name,
        "version":        version,
        "arch":           arch,
        "action":         action,
        "status":         ACTION_TO_STATUS[action],
        "justification":  justification,
        "decided_by":     decided_by,
        "decided_at":     now.isoformat(),
        "expires_at":     expires_at,
        "expires_in_days": expires_in_days,
        "target_version": target_version,
        "cve_ids":        cve_ids or [],
    }

    path = _decision_path(name, version, arch)
    path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    return decision


def load_decision(name: str, version: str, arch: str = "amd64") -> dict | None:
    """Charge la décision existante pour un paquet, ou None si absente."""
    path = _decision_path(name, version, arch)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_decision(name: str, version: str, arch: str = "amd64") -> bool:
    """Supprime la décision (ex: lors d'une mise à jour de paquet)."""
    path = _decision_path(name, version, arch)
    if path.exists():
        path.unlink()
        return True
    return False


def list_all_decisions() -> list[dict]:
    """Retourne toutes les décisions stockées."""
    decisions = []
    for path in sorted(DECISIONS_DIR.glob("*.json")):
        try:
            decisions.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return decisions


def is_decision_expired(decision: dict) -> bool:
    """Retourne True si la décision a dépassé sa date d'expiration."""
    expires_at = decision.get("expires_at")
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        return datetime.now(timezone.utc) > exp
    except Exception:
        return False


def mark_patch_notified(name: str, version: str, arch: str = "amd64") -> bool:
    """
    Marque une décision 'upgrade_required' comme ayant déjà déclenché la
    notification "correctif disponible", pour éviter de la renvoyer chaque jour.
    """
    path = _decision_path(name, version, arch)
    if not path.exists():
        return False
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision["patch_available_notified"] = True
    path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


def resolve_decision(
    name: str, version: str, arch: str, resolved_by: str, note: str = "",
) -> dict | None:
    """
    Clôture manuellement une décision 'upgrade_required' une fois le correctif
    déployé, pour la sortir de la file d'action tout en gardant l'historique.
    """
    path = _decision_path(name, version, arch)
    if not path.exists():
        return None
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision["resolved_at"] = datetime.now(timezone.utc).isoformat()
    decision["resolved_by"] = resolved_by
    decision["resolution_note"] = note
    path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    return decision


def get_sla_status(decision: dict) -> dict:
    """Calcule le statut SLA d'une décision (jours restants, dépassé, etc.)."""
    expires_at = decision.get("expires_at")
    if not expires_at:
        return {"has_sla": False}

    try:
        exp = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        remaining = (exp - now).days
        return {
            "has_sla":   True,
            "expires_at": expires_at,
            "remaining_days": remaining,
            "expired": remaining < 0,
            "warning": 0 <= remaining <= 7,  # alerte J-7
        }
    except Exception:
        return {"has_sla": False}
