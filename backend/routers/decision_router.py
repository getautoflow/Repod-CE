# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2024-present repod contributors
# See LICENSE for terms. Commercial use: LICENSE-COMMERCIAL.md
"""
Routes de décision RSSI :
- GET  /security/packages/{name}/{version}/decision   → manifest + décision + SLA
- POST /security/packages/{name}/{version}/decide     → enregistre une décision RSSI
- POST /security/packages/{name}/{version}/quarantine → mise en quarantaine immédiate
"""
import os
import shutil
import subprocess
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.dependencies import get_current_user, get_maintainer_user
from routers.security_common import POOL_DIR, STAGING_QUARANTINE
from services.audit import log as audit_log
from services.distributions import remove_package as _repo_remove_package
from services.format_router import (
    ACCEPTED_EXTENSIONS as _ACCEPTED_EXTS,
)
from services.format_router import (
    DEFAULT_DISTRIBUTION as _DEFAULT_DISTRIBUTION,
)
from services.format_router import (
    FORMAT_LABEL as _FORMAT_LABEL,
)
from services.format_router import (
    is_apt as _is_apt,
)
from services.manifest import list_manifests, load_manifest, save_manifest
from services.path_safety import PathTraversalError, safe_path_join, safe_path_join_http
from services.security_decisions import (
    ACTION_TO_STATUS,
    get_sla_status,
    list_all_decisions,
    load_decision,
    resolve_decision,
    save_decision,
)

router = APIRouter(prefix="/security", tags=["Security"])


class DecisionRequest(BaseModel):
    action:          str           # accept_risk | exception | reject | upgrade_required
    justification:   str           # obligatoire
    expires_in_days: int | None = None   # pour accept_risk et exception
    target_version:  str | None = None   # pour upgrade_required
    cve_ids:         list[str] = []      # CVE IDs couverts (vide = tous)
    arch:            str = "amd64"


@router.get("/packages/{name}/{version}/decision")
def get_package_decision(
    name: str,
    version: str,
    arch: str = "amd64",
    current_user: str = Depends(get_current_user),
):
    """Retourne le manifest + la décision RSSI + le statut SLA pour un paquet."""
    manifest = load_manifest(name, version, arch)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"{name} {version} introuvable")
    decision = load_decision(name, version, arch)
    sla = get_sla_status(decision) if decision else None
    return {
        "manifest": manifest,
        "decision": decision,
        "sla": sla,
        "status": manifest.get("status", "unknown"),
    }


@router.post("/packages/{name}/{version}/decide")
def decide_package(
    name: str,
    version: str,
    body: DecisionRequest,
    current_user: str = Depends(get_maintainer_user),
):
    """
    Enregistre la décision RSSI pour un paquet en attente.

    Actions :
      accept_risk      → accepte les CVE existantes, paquet promu dans APT
      exception        → exception temporaire (même effet + date d'expiration)
      reject           → quarantaine définitive
      upgrade_required → paquet bloqué jusqu'à la version cible
    """
    VALID_ACTIONS = {"accept_risk", "exception", "reject", "upgrade_required"}
    if body.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400,
                            detail=f"Action invalide. Valeurs : {sorted(VALID_ACTIONS)}")

    if not body.justification.strip():
        raise HTTPException(status_code=400, detail="La justification est obligatoire")

    # Charger le manifest
    manifest = load_manifest(name, version, body.arch)
    if not manifest:
        for m in list_manifests():
            if m["name"] == name and m.get("version") == version:
                manifest = m
                body.arch = m.get("arch", body.arch)
                break
    if not manifest:
        raise HTTPException(status_code=404,
                            detail=f"Manifest introuvable pour {name} {version}")

    current_status = manifest.get("status", "validated")
    if current_status not in ("pending_review", "blocked", "accepted_risk",
                               "exception", "upgrade_required"):
        raise HTTPException(status_code=409,
                            detail=f"Ce paquet n'est pas en révision (statut: {current_status})")

    # Persister la décision
    decision = save_decision(
        name=name, version=version, arch=body.arch,
        action=body.action,
        justification=body.justification,
        decided_by=current_user,
        expires_in_days=body.expires_in_days,
        target_version=body.target_version,
        cve_ids=body.cve_ids or [c["id"] for c in manifest.get("cve_results", []) if c.get("id")],
    )

    # Mettre à jour le manifest
    new_status = ACTION_TO_STATUS[body.action]
    manifest["status"]        = new_status
    manifest["decision"]      = decision
    save_manifest(manifest)

    # Actions système selon la décision
    _pkg_ext = next(iter(_ACCEPTED_EXTS))  # ".deb" ou ".rpm"
    _sep     = "_" if _is_apt() else "-"    # séparateur nom_version (APT) vs nom-version (RPM)

    if body.action in ("accept_risk", "exception"):
        # Promouvoir dans le dépôt physique
        distrib  = manifest.get("distribution", _DEFAULT_DISTRIBUTION)
        filename = manifest.get("filename", f"{name}{_sep}{version}{_sep}{body.arch}{_pkg_ext}")
        pool_pkg = safe_path_join_http(POOL_DIR, filename)
        if pool_pkg.exists():
            if _is_apt():
                ADD_DEB_SCRIPT = os.getenv("ADD_DEB_SCRIPT", "/scripts/add-deb.sh")
                subprocess.run(
                    ["sh", ADD_DEB_SCRIPT, distrib, filename],
                    capture_output=True, text=True,
                )
            else:
                from services.distributions_rpm import add_rpm_to_distrib
                add_rpm_to_distrib(filename, distrib)

    elif body.action == "reject":
        # Déplacer vers quarantaine
        STAGING_QUARANTINE.mkdir(parents=True, exist_ok=True)
        filename = manifest.get("filename", f"{name}{_sep}{version}{_sep}{body.arch}{_pkg_ext}")
        pool_pkg = safe_path_join_http(POOL_DIR, filename)
        if pool_pkg.exists():
            shutil.move(str(pool_pkg), str(STAGING_QUARANTINE / pool_pkg.name))
        # Retirer du dépôt physique (reprepro en APT, createrepo_c en RPM)
        _repo_remove_package(name)

    audit_log(
        "SECURITY_DECISION", current_user, "SUCCESS",
        package=name, version=version,
        detail=(
            f"Action : {body.action} | "
            f"Justification : {body.justification[:100]} | "
            f"Expire : {decision.get('expires_at') or 'jamais'}"
        ),
    )

    return {
        "status":   "ok",
        "package":  name,
        "version":  version,
        "action":   body.action,
        "new_status": new_status,
        "decision": decision,
        "message": {
            "accept_risk":      f"{name} accepté avec risque — publié dans {_FORMAT_LABEL}",
            "exception":        f"{name} exception accordée — publié dans {_FORMAT_LABEL}",
            "reject":           f"{name} rejeté — déplacé en quarantaine",
            "upgrade_required": f"{name} en attente de mise à jour vers {body.target_version}",
        }.get(body.action, "Décision enregistrée"),
    }


@router.post("/packages/{name}/{version}/quarantine")
def quarantine_package(
    name: str,
    version: str,
    arch: str = Query("amd64"),
    current_user: str = Depends(get_maintainer_user),
):
    """
    Met un paquet en quarantaine immédiatement :
    1. Déplace le .deb du pool vers staging/quarantine/
    2. Retire de reprepro (toutes distributions)
    3. Met à jour le manifest (status = quarantined)
    4. Audit log
    """
    STAGING_QUARANTINE.mkdir(parents=True, exist_ok=True)

    _pkg_ext  = next(iter(_ACCEPTED_EXTS))  # ".deb" ou ".rpm"
    _sep      = "_" if _is_apt() else "-"   # séparateur APT vs RPM

    # Trouver le fichier paquet dans le pool
    version_safe = version.replace(":", "_").replace("/", "_")
    pkg_path = None
    for pat in [
        f"{name}{_sep}{version}{_sep}{arch}{_pkg_ext}",
        f"{name}{_sep}{version_safe}{_sep}{arch}{_pkg_ext}",
    ]:
        try:
            p = safe_path_join(POOL_DIR, pat)
        except PathTraversalError:
            continue
        if p.exists():
            pkg_path = p
            break
    if pkg_path is None:
        # Recherche large
        candidates = list(POOL_DIR.glob(f"{name}{_sep}*{_pkg_ext}"))
        pkg_path = next(
            (c for c in candidates
             if f"{_sep}{version}{_sep}" in c.name
             or f"{_sep}{version_safe}{_sep}" in c.name),
            None
        )

    # Retirer du dépôt physique (reprepro APT ou createrepo_c RPM)
    _repo_remove_package(name)

    # Déplacer le paquet si trouvé
    moved_deb = None
    if pkg_path and pkg_path.exists():
        dest = STAGING_QUARANTINE / pkg_path.name
        shutil.move(str(pkg_path), str(dest))
        moved_deb = pkg_path.name

    # Mettre à jour le manifest
    manifest = load_manifest(name, version, arch)
    if not manifest:
        for m in list_manifests():
            if m["name"] == name and m.get("version") == version:
                manifest = m
                arch = m.get("arch", arch)
                break

    if manifest:
        manifest["status"] = "quarantined"
        manifest["quarantined_at"] = datetime.now(timezone.utc).isoformat()
        manifest["quarantined_by"] = current_user
        save_manifest(manifest)

    audit_log(
        "QUARANTINE", current_user, "SUCCESS",
        package=name, version=version,
        detail=f"Mis en quarantaine manuellement — .deb: {moved_deb or 'non trouvé dans pool'}",
    )

    return {
        "status": "quarantined",
        "package": name,
        "version": version,
        "deb_moved": moved_deb,
        "message": f"{name} {version} déplacé en quarantaine",
    }


@router.get("/decisions")
def list_decisions(current_user: str = Depends(get_current_user)):
    """Retourne toutes les décisions RSSI enregistrées, pour le suivi/audit."""
    decisions = []
    for decision in list_all_decisions():
        entry = dict(decision)
        entry["sla"] = get_sla_status(decision)
        # CE : pas d'inventaire machine
        entry["install_count"] = 0
        entry["install_clients"] = []
        decisions.append(entry)

    decisions.sort(key=lambda d: d.get("decided_at") or "", reverse=True)
    return {"decisions": decisions, "count": len(decisions)}


class ResolveRequest(BaseModel):
    arch: str = "amd64"
    note: str = ""


@router.post("/packages/{name}/{version}/decision/resolve")
def resolve_package_decision(
    name: str,
    version: str,
    body: ResolveRequest,
    current_user: str = Depends(get_maintainer_user),
):
    """Clôture manuellement une décision 'upgrade_required' une fois le correctif déployé."""
    decision = resolve_decision(name, version, body.arch, current_user, body.note)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"Décision introuvable pour {name} {version}")
    audit_log(
        "DECISION_RESOLVED", current_user, "SUCCESS",
        package=name, version=version,
        detail=f"Note : {body.note[:100]}",
    )
    return {"status": "resolved", "decision": decision}
