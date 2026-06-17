# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2024-present repod contributors
# See LICENSE for terms. Commercial use: LICENSE-COMMERCIAL.md
"""
tests/test_router_integration.py — Tests d'intégration HTTP

Couvre, via FastAPI TestClient, les trois routers identifiés comme
prioritaires pour la couverture (tâche "long terme") :

  - routers/upload.py             → POST /upload/ (validations, pipeline, doublons)
  - routers/security_router.py    → agrégat cve_router + decision_router + scan_router

Approche :
  - Une app FastAPI minimale par router, montée avec `dependency_overrides`
    pour court-circuiter l'authentification (rôle injecté directement).
  - Les opérations système (subprocess, SSH, reprepro, grype, notifications)
    sont mockées — seule la logique HTTP/métier du router est exercée.
  - Quelques tests RBAC utilisent de vrais JWT (auth.jwt) sans override pour
    vérifier que get_maintainer_user / get_admin_user
    rejettent correctement les rôles insuffisants (403).
"""

import os
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Environnement AVANT tout import applicatif ────────────────────────────────
_TMP = tempfile.mkdtemp(prefix="repod_router_it_")
os.environ.setdefault("MANIFEST_DIR",       _TMP)
os.environ.setdefault("POOL_DIR",           os.path.join(_TMP, "pool"))
os.environ.setdefault("STAGING_INCOMING",   os.path.join(_TMP, "staging", "incoming"))
os.environ.setdefault("STAGING_QUARANTINE", os.path.join(_TMP, "staging", "quarantine"))
os.environ.setdefault("INDEX_PATH",         os.path.join(_TMP, "index.json"))
os.environ.setdefault("AUDIT_DIR",          _TMP)
os.environ.setdefault("SECURITY_CACHE_DIR", os.path.join(_TMP, "security"))
os.environ.setdefault("JWT_SECRET_KEY",     "test-secret-router-integration")

for _d in ("pool", os.path.join("staging", "incoming"), os.path.join("staging", "quarantine")):
    Path(os.path.join(_TMP, _d)).mkdir(parents=True, exist_ok=True)

import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

import routers.security_common as security_common
import routers.upload as upload_mod
import services.indexer as indexer_mod
from auth.dependencies import (
    get_admin_user,
    get_current_user,
    get_maintainer_user,
    get_uploader_user,
)
from auth.jwt import create_access_token
from limiter import limiter
from routers.security_router import router as security_router
from services.manifest import save_manifest
from services.rate_limits import rate_limit_exceeded_handler

# NB: routers/__init__.py fait `from .upload import router as upload`, ce qui
# remplace l'attribut `routers.upload` (le package) par l'APIRouter lui-même.
# `unittest.mock.patch("routers.upload.xxx")` résout donc `routers.upload`
# vers l'APIRouter (pas le module) et échoue avec AttributeError. On récupère
# le vrai module via sys.modules pour patcher dessus avec patch.object().
upload_module = sys.modules["routers.upload"]


# ═══════════════════════════════════════════════════════════════════════════════
# Apps de test
# ═══════════════════════════════════════════════════════════════════════════════

def _make_app(router, overrides: dict, with_limiter: bool = False) -> TestClient:
    app = FastAPI()
    if with_limiter:
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(router)
    for dep, value in overrides.items():
        app.dependency_overrides[dep] = (lambda v=value: v)
    return TestClient(app, raise_server_exceptions=False)


# Security : un seul utilisateur "admin" pour current_user/admin/maintainer
_sec_overrides = {
    get_current_user:    "rssi_admin",
    get_admin_user:      "rssi_admin",
    get_maintainer_user: "rssi_admin",
}
sec_client = _make_app(security_router, _sec_overrides)

# Upload : utilisateur "uploader_bob"
# `upload_mod` est déjà l'APIRouter lui-même (cf. note ci-dessus).
upload_client = _make_app(upload_mod, {get_uploader_user: "uploader_bob"}, with_limiter=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_manifest(name="demo-pkg", version="1.0.0", arch="amd64", **extra) -> dict:
    manifest = {
        "name": name,
        "version": version,
        "arch": arch,
        "distribution": "jammy",
        "filename": f"{name}_{version}_{arch}.deb",
        "status": "validated",
        "source": {"imported_by": "ci", "imported_at": "2026-01-01T00:00:00+00:00"},
        "integrity": {"sha256": "deadbeef"},
        "cve_results": [],
        "validation_steps": [],
    }
    manifest.update(extra)
    save_manifest(manifest)
    return manifest


def _role_headers(role: str, username: str = "user1") -> dict:
    """
    Génère un header Authorization Bearer pour un rôle donné.

    `_parse_token()` (auth/dependencies.py) vérifie que l'utilisateur existe
    toujours en base (`get_user`) après décodage du JWT — l'utilisateur doit
    donc exister dans la base de test (db_test_engine, autouse) avant
    génération du token, sinon la dépendance lève 401 au lieu de 403.
    """
    from auth.users import VALID_ROLES, create_user, get_user_any
    if not get_user_any(username):
        # Le rôle stocké en base doit être valide même si le rôle du JWT
        # (utilisé par _require_role) est volontairement invalide
        # (ex. "guest" pour tester le rejet 403 d'un rôle inconnu).
        create_user(username, "Str0ngP@ssw0rd!", role=role if role in VALID_ROLES else "reader")
    tok = create_access_token({"sub": username, "role": role})
    return {"Authorization": f"Bearer {tok}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Security router — CVE visibility (cve_router)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityCveRouter:

    def test_vulnerabilities_empty(self):
        r = sec_client.get("/security/vulnerabilities")
        assert r.status_code == 200
        body = r.json()
        assert "summary" in body
        assert "vulnerabilities" in body

    def test_vulnerabilities_with_cve(self):
        _seed_manifest(
            name="vuln-pkg", version="2.0.0",
            cve_results=[{
                "id": "CVE-2026-1111", "severity": "Critical", "cvss": 9.8,
                "description": "demo", "fix_state": "fixed",
                "fix_versions": ["2.0.1"], "urls": [],
                "package_name": "vuln-pkg", "package_version": "2.0.0",
            }],
        )
        r = sec_client.get("/security/vulnerabilities")
        assert r.status_code == 200
        body = r.json()
        assert body["summary"]["critical"] >= 1
        ids = [v["id"] for v in body["vulnerabilities"]["items"]] if "items" in body["vulnerabilities"] \
            else [v["id"] for v in body["vulnerabilities"]]
        assert "CVE-2026-1111" in ids

    def test_vulnerabilities_severity_filter(self):
        r = sec_client.get("/security/vulnerabilities", params={"severity": "critical"})
        assert r.status_code == 200
        for v in (r.json()["vulnerabilities"].get("items") or r.json()["vulnerabilities"]):
            assert v["severity"].lower() == "critical"

    def test_packages_posture(self):
        _seed_manifest()  # demo-pkg / 1.0.0
        r = sec_client.get("/security/packages-posture")
        assert r.status_code == 200
        body = r.json()
        assert "packages" in body
        names = [p["name"] for p in body["packages"]]
        assert "demo-pkg" in names

    def test_package_cve_found(self):
        _seed_manifest()  # demo-pkg / 1.0.0
        r = sec_client.get("/security/packages/demo-pkg/1.0.0/cve")
        assert r.status_code == 200
        assert r.json()["package"] == "demo-pkg"

    def test_package_cve_not_found(self):
        r = sec_client.get("/security/packages/no-such-pkg/9.9.9/cve")
        assert r.status_code == 404

    def test_review_queue_lists_pending(self):
        _seed_manifest(name="pending-pkg", version="1.0.0", status="pending_review",
                        cve_results=[{"id": "CVE-2026-2222", "severity": "High",
                                       "cvss": 7.5, "fix_state": "unknown"}])
        r = sec_client.get("/security/review-queue")
        assert r.status_code == 200
        body = r.json()
        items = body["packages"].get("items", body["packages"])
        names = [p["name"] for p in items]
        assert "pending-pkg" in names
        assert body["review_count"] >= 1

    def test_security_report(self):
        r = sec_client.get("/security/report")
        assert r.status_code == 200
        body = r.json()
        assert "summary" in body
        assert body["generated_by"] == "rssi_admin"

    def test_check_sla_requires_admin(self):
        with patch("services.sla_alerts.run_sla_check", return_value={"checked": 0}):
            r = sec_client.post("/security/check-sla")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Security router — décisions RSSI (decision_router)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityDecisionRouter:

    def test_get_decision_not_found(self):
        r = sec_client.get("/security/packages/missing-pkg/1.0.0/decision")
        assert r.status_code == 404

    def test_get_decision_found(self):
        _seed_manifest(name="dec-pkg", version="1.0.0")
        r = sec_client.get("/security/packages/dec-pkg/1.0.0/decision")
        assert r.status_code == 200
        body = r.json()
        assert body["manifest"]["name"] == "dec-pkg"
        assert body["decision"] is None

    def test_decide_invalid_action(self):
        _seed_manifest(name="dec-pkg2", version="1.0.0", status="pending_review")
        r = sec_client.post(
            "/security/packages/dec-pkg2/1.0.0/decide",
            json={"action": "not_a_real_action", "justification": "x"},
        )
        assert r.status_code == 400

    def test_decide_missing_justification(self):
        _seed_manifest(name="dec-pkg3", version="1.0.0", status="pending_review")
        r = sec_client.post(
            "/security/packages/dec-pkg3/1.0.0/decide",
            json={"action": "accept_risk", "justification": "   "},
        )
        assert r.status_code == 400

    def test_decide_package_not_in_review(self):
        _seed_manifest(name="dec-pkg4", version="1.0.0", status="validated")
        r = sec_client.post(
            "/security/packages/dec-pkg4/1.0.0/decide",
            json={"action": "accept_risk", "justification": "validé manuellement"},
        )
        assert r.status_code == 409

    def test_decide_accept_risk_success(self):
        _seed_manifest(name="dec-pkg5", version="1.0.0", status="pending_review",
                        cve_results=[{"id": "CVE-2026-3333", "severity": "High"}])
        with patch("routers.decision_router.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            r = sec_client.post(
                "/security/packages/dec-pkg5/1.0.0/decide",
                json={"action": "accept_risk", "justification": "Risque accepté pour ce sprint"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["new_status"] == "accepted_risk"

    def test_decide_reject_moves_to_quarantine(self):
        manifest = _seed_manifest(name="dec-pkg6", version="1.0.0", status="pending_review",
                                   filename="dec-pkg6_1.0.0_amd64.deb")
        # Crée le fichier dans le pool pour vérifier le déplacement
        pool_dir = security_common.POOL_DIR
        pool_dir.mkdir(parents=True, exist_ok=True)
        pkg_file = pool_dir / manifest["filename"]
        pkg_file.write_bytes(b"fake-deb-content")

        with patch("routers.decision_router._repo_remove_package") as mock_remove:
            r = sec_client.post(
                "/security/packages/dec-pkg6/1.0.0/decide",
                json={"action": "reject", "justification": "CVE critique non corrigée"},
            )
        assert r.status_code == 200
        assert r.json()["new_status"] == "quarantined"
        mock_remove.assert_called_once_with("dec-pkg6")
        assert not pkg_file.exists()

    def test_quarantine_package(self):
        manifest = _seed_manifest(name="dec-pkg7", version="1.0.0",
                                   filename="dec-pkg7_1.0.0_amd64.deb")
        pool_dir = security_common.POOL_DIR
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / manifest["filename"]).write_bytes(b"fake-deb-content")

        with patch("routers.decision_router._repo_remove_package") as mock_remove:
            r = sec_client.post("/security/packages/dec-pkg7/1.0.0/quarantine")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "quarantined"
        assert body["deb_moved"] == "dec-pkg7_1.0.0_amd64.deb"
        mock_remove.assert_called_once_with("dec-pkg7")

    def test_decide_rbac_maintainer_required(self):
        """Un rôle 'reader' ne peut pas appeler /decide (403)."""
        app = FastAPI()
        app.include_router(security_router)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/security/packages/whatever/1.0.0/decide",
            json={"action": "accept_risk", "justification": "x"},
            headers=_role_headers("reader"),
        )
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# Security router — scan / bases de sécurité (scan_router)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityScanRouter:

    def test_clamav_status(self):
        with patch("routers.scan_router.get_clamav_status",
                   return_value={"installed": True, "db_version": "27000"}):
            r = sec_client.get("/security/clamav/status")
        assert r.status_code == 200
        assert r.json()["db_version"] == "27000"

    def test_grype_status_not_installed(self):
        with patch("routers.scan_router.subprocess.run", side_effect=FileNotFoundError()):
            r = sec_client.get("/security/grype/status")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "grype non installé"

    def test_feeds_status(self):
        r = sec_client.get("/security/feeds/status")
        assert r.status_code == 200
        body = r.json()
        assert "kev" in body and "epss" in body

    def test_rescan_package_not_found(self):
        r = sec_client.post("/security/packages/no-such-pkg/9.9.9/rescan")
        assert r.status_code == 404

    def test_rescan_package_success(self):
        manifest = _seed_manifest(name="rescan-pkg", version="1.0.0",
                                   filename="rescan-pkg_1.0.0_amd64.deb")
        pool_dir = security_common.POOL_DIR
        pool_dir.mkdir(parents=True, exist_ok=True)
        (pool_dir / manifest["filename"]).write_bytes(b"fake-deb-content")

        grype_json = (
            '{"matches": [{'
            '"vulnerability": {"id": "CVE-2026-4444", "severity": "Medium", '
            '"description": "demo", "fix": {"state": "fixed", "versions": ["1.0.1"]}, '
            '"cvss": [], "urls": []}, '
            '"artifact": {"name": "rescan-pkg", "version": "1.0.0"}}]}'
        )
        # `_sp` est un alias local (`import subprocess as _sp`) défini dans le
        # corps de rescan_package() — non patchable via routers.scan_router._sp.
        # On patche directement subprocess.run (même module sous-jacent).
        with patch("subprocess.run") as mock_run, \
             patch("services.cve_enrichment.enrich_cve_list", side_effect=lambda x: x):
            mock_run.return_value = MagicMock(returncode=0, stdout=grype_json, stderr="")
            r = sec_client.post("/security/packages/rescan-pkg/1.0.0/rescan")
        assert r.status_code == 200
        body = r.json()
        assert body["cve_count"] == 1
        assert body["cve_counts"]["medium"] == 1

    def test_rbac_admin_required_for_clamav_update(self):
        app = FastAPI()
        app.include_router(security_router)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/security/clamav/update", headers=_role_headers("maintainer"))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# Upload router
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadRouter:

    @pytest.fixture(autouse=True)
    def _reset_rate_limit(self):
        """
        `upload_client` est partagé entre tous les tests de cette classe et
        POST /upload/ est limité à 5 req/min/IP (make_role_limit("upload")) —
        sans reset, les derniers tests de la classe reçoivent 429 au lieu du
        code attendu.
        """
        limiter.reset()
        yield
        limiter.reset()

    def test_invalid_distribution(self):
        r = upload_client.post(
            "/upload/",
            data={"distribution": "not-a-real-distrib"},
            files={"file": ("pkg.deb", b"content", "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_invalid_extension(self):
        r = upload_client.post(
            "/upload/",
            data={"distribution": "jammy"},
            files={"file": ("pkg.txt", b"content", "text/plain")},
        )
        assert r.status_code == 400

    def test_format_distribution_mismatch(self):
        """
        .deb sur une distribution Alpine (alpine3.18, attend .apk) → 422.

        NB: en mode REPO_FORMAT=apt, VALID_CODENAMES inclut jammy/noble/focal/
        bookworm + les codenames Alpine (alpine3.18..3.21) — mais pas les
        codenames RPM (almalinux9 etc.), qui sont rejetés en 400 (distribution
        invalide) avant même d'atteindre la vérification de cohérence
        format/distribution.
        """
        r = upload_client.post(
            "/upload/",
            data={"distribution": "alpine3.18"},
            files={"file": ("pkg.deb", b"content", "application/octet-stream")},
        )
        assert r.status_code == 422

    def _fake_validation(self, passed=True, cve_status="approved"):
        result = MagicMock()
        result.passed = passed
        result.cve_status = cve_status
        result.deps = []
        result.steps = [{"name": "format", "passed": True}]
        result.cve_results = []
        result.to_dict.return_value = {
            "passed": passed, "cve_status": cve_status, "steps": result.steps,
        }
        return result

    def test_upload_accepted(self):
        validation = self._fake_validation(passed=True, cve_status="approved")
        fake_manifest = {
            "name": "newpkg", "version": "1.0.0", "arch": "amd64",
            "integrity": {"sha256": "abc123"},
        }
        with patch.object(upload_module, "run_validation_pipeline", return_value=validation), \
             patch.object(upload_module, "generate_manifest", return_value=fake_manifest), \
             patch.object(upload_module, "save_manifest"), \
             patch.object(upload_module, "add_to_index"), \
             patch.object(upload_module, "_add_to_repo", return_value=True) as mock_add_repo:
            r = upload_client.post(
                "/upload/",
                data={"distribution": "jammy"},
                files={"file": ("newpkg_1.0.0_amd64.deb", b"hello-world", "application/octet-stream")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "accepted"
        assert body["package"] == "newpkg"
        mock_add_repo.assert_called_once()

    def test_upload_rejected_validation_failed(self):
        validation = self._fake_validation(passed=False, cve_status="approved")
        with patch.object(upload_module, "run_validation_pipeline", return_value=validation):
            r = upload_client.post(
                "/upload/",
                data={"distribution": "jammy"},
                files={"file": ("badpkg_1.0.0_amd64.deb", b"bad-content", "application/octet-stream")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "rejected"
        # Le fichier doit avoir été déplacé en quarantaine
        quarantine_dir = upload_module.STAGING_QUARANTINE
        assert (quarantine_dir / "badpkg_1.0.0_amd64.deb").exists()

    def test_upload_pending_review(self):
        validation = self._fake_validation(passed=True, cve_status="pending_review")
        fake_manifest = {
            "name": "reviewpkg", "version": "1.0.0", "arch": "amd64",
            "integrity": {"sha256": "def456"},
        }
        with patch.object(upload_module, "run_validation_pipeline", return_value=validation), \
             patch.object(upload_module, "generate_manifest", return_value=fake_manifest), \
             patch.object(upload_module, "save_manifest"), \
             patch.object(upload_module, "add_to_index"), \
             patch.object(upload_module, "_add_to_repo", return_value=True) as mock_add_repo, \
             patch.object(upload_module, "notify"):
            r = upload_client.post(
                "/upload/",
                data={"distribution": "jammy"},
                files={"file": ("reviewpkg_1.0.0_amd64.deb", b"review-content", "application/octet-stream")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "pending_review"
        # Pas de publication dans le dépôt physique tant que la révision n'est pas faite
        mock_add_repo.assert_not_called()

    def test_upload_duplicate_already_imported(self):
        # Place un fichier identique dans le pool, avec le même nom que l'upload
        pool_dir = upload_module.POOL_DIR
        pool_dir.mkdir(parents=True, exist_ok=True)
        filename = f"dup-pkg_1.0.0_amd64_{uuid.uuid4().hex[:6]}.deb"
        content = b"identical-bytes"
        (pool_dir / filename).write_bytes(content)

        r = upload_client.post(
            "/upload/",
            data={"distribution": "jammy"},
            files={"file": (filename, content, "application/octet-stream")},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "already_imported"

    def test_upload_duplicate_conflict(self):
        pool_dir = upload_module.POOL_DIR
        pool_dir.mkdir(parents=True, exist_ok=True)
        filename = f"conflict-pkg_1.0.0_amd64_{uuid.uuid4().hex[:6]}.deb"
        (pool_dir / filename).write_bytes(b"original-bytes")

        r = upload_client.post(
            "/upload/",
            data={"distribution": "jammy"},
            files={"file": (filename, b"different-bytes", "application/octet-stream")},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "duplicate_conflict"

    def test_upload_rbac_uploader_required(self):
        """Un rôle 'reader' ne peut pas uploader (403)."""
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
        app.include_router(upload_mod)
        client = TestClient(app, raise_server_exceptions=False)
        r = client.post(
            "/upload/",
            data={"distribution": "jammy"},
            files={"file": ("pkg.deb", b"content", "application/octet-stream")},
            headers=_role_headers("reader"),
        )
        assert r.status_code == 403

