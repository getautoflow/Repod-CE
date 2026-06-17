"""
Gestionnaire de jobs de mirroir planifié.

Pour une source upstream donnée, télécharge en masse tous les paquets indexés
via le pipeline complet de validation (ClamAV + Grype + GPG + dépendances) et
les ajoute au repo interne (reprepro / createrepo_c / APK).

Architecture calquée sur services.sync_manager (SyncJob/SyncManager) :
  - MirrorJob     : état d'un job (logs, compteurs, status, threading.Event)
  - MirrorManager : singleton gérant les jobs actifs + historique (1h)
  - Un seul job de mirroir actif à la fois (toutes sources confondues), pour
    ne pas saturer ClamAV/Grype ni la bande passante upstream.
"""
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional


class MirrorJob:
    """État complet d'un job de mirroir."""

    def __init__(self, job_id: str, source_id: str, label: str, distribution: str):
        self.job_id = job_id
        self.source_id = source_id
        self.label = label
        self.distribution = distribution
        self.total = 0
        self.done_count = 0
        self.added_count = 0
        self.pending_count = 0
        self.blocked_count = 0
        self.skipped_count = 0
        self.error_count = 0
        self.status = "running"      # "running" | "done" | "error" | "cancelled"
        self.logs: list[str] = []    # format "level|message"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: Optional[str] = None
        self._lock = threading.Lock()
        self._new_log = threading.Event()
        self._stop = threading.Event()  # set() pour demander l'arrêt

    def cancel(self) -> bool:
        """Demande l'arrêt propre du job. Retourne True si le job était actif."""
        if self.status == "running":
            self._stop.set()
            self._new_log.set()  # débloquer les SSE en attente
            self.emit("warning", "⏹️ Arrêt demandé — le paquet en cours sera le dernier...")
            return True
        return False

    def emit(self, level: str, msg: str) -> None:
        line = f"{level}|{msg}"
        with self._lock:
            self.logs.append(line)
        self._new_log.set()

    def iter_stream(self, from_index: int = 0):
        """
        Générateur SSE reconnectable.
        Yield tous les logs depuis from_index, puis attend les futurs logs.
        Se termine quand le job est fini et tous les logs ont été émis.
        """
        idx = from_index
        while True:
            with self._lock:
                while idx < len(self.logs):
                    yield f"data: {self.logs[idx]}\n\n"
                    idx += 1
                is_done = self.status != "running"

            if is_done:
                yield "data: done|DONE\n\n"
                return

            self._new_log.wait(timeout=1.0)
            self._new_log.clear()

    def to_dict(self) -> dict:
        return {
            "job_id":         self.job_id,
            "source_id":      self.source_id,
            "label":          self.label,
            "distribution":   self.distribution,
            "status":         self.status,
            "total":          self.total,
            "done_count":     self.done_count,
            "added_count":    self.added_count,
            "pending_count":  self.pending_count,
            "blocked_count":  self.blocked_count,
            "skipped_count":  self.skipped_count,
            "error_count":    self.error_count,
            "started_at":     self.started_at,
            "finished_at":    self.finished_at,
            "log_count":      len(self.logs),
            "cancelling":     self._stop.is_set() and self.status == "running",
        }


class MirrorManager:
    """Singleton gérant tous les jobs de mirroir planifié."""

    def __init__(self):
        self._jobs: Dict[str, MirrorJob] = {}
        self._lock = threading.Lock()

    # ─── Gestion des jobs ─────────────────────────────────────────────────────

    def start_job(self, source_id: str, distribution: str, user: str = "system",
                   limit: Optional[int] = None) -> MirrorJob:
        """
        Démarre un job de mirroir en arrière-plan pour une source donnée.
        Retourne le job actif existant si un mirroir est déjà en cours
        (un seul job de mirroir global à la fois).
        """
        from services.package_index import DEFAULT_SOURCES

        with self._lock:
            existing = self.active_job_obj()
            if existing:
                return existing

            source = next((s for s in DEFAULT_SOURCES if s["id"] == source_id), None)
            label = source["label"] if source else source_id

            job_id = str(uuid.uuid4())[:8]
            job = MirrorJob(job_id, source_id, label, distribution)
            self._jobs[job_id] = job
            self._cleanup_old_jobs()

        thread = threading.Thread(
            target=self._run_job,
            args=(job, user, limit),
            daemon=True,
            name=f"mirror-{job_id}",
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> Optional[MirrorJob]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            return job.cancel()
        return False

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.started_at,
                reverse=True,
            )[:limit]
        return [j.to_dict() for j in jobs]

    def active_job_obj(self) -> Optional[MirrorJob]:
        return next((j for j in self._jobs.values() if j.status == "running"), None)

    def active_job(self) -> Optional[dict]:
        job = self.active_job_obj()
        return job.to_dict() if job else None

    # ─── Corps du job ─────────────────────────────────────────────────────────

    def _run_job(self, job: MirrorJob, user: str, limit: Optional[int]) -> None:
        from services.audit import log as audit_log
        from services.importer import import_one
        from services.package_index import (
            DEFAULT_SOURCES,
            list_packages_by_source,
            sync_source,
        )
        from services.settings import get_settings

        try:
            source = next((s for s in DEFAULT_SOURCES if s["id"] == job.source_id), None)
            if not source:
                job.emit("error", f"Source '{job.source_id}' inconnue")
                job.status = "error"
                return

            cfg = get_settings().get("mirror", {})
            min_free_disk_gb = cfg.get("min_free_disk_gb", 5)

            # 1. Rafraîchir l'index local de la source
            job.emit("info", f"Synchronisation de l'index '{job.label}'...")
            try:
                sync_source(source)
            except Exception as exc:
                job.emit("warning", f"Synchronisation de l'index échouée (index existant utilisé) : {exc}")

            # 2. Lister tous les paquets indexés pour cette source
            packages = list_packages_by_source(job.source_id, limit=limit) if limit \
                else list_packages_by_source(job.source_id)
            job.total = len(packages)
            job.emit("info", f"{job.total} paquet(s) à mirrorer pour '{job.label}'")

            if not packages:
                job.emit("warning", "Aucun paquet indexé pour cette source")

            # 3. Boucle d'import paquet par paquet
            for pkg_row in packages:
                if job._stop.is_set():
                    break

                # Vérification de l'espace disque restant
                try:
                    usage = shutil.disk_usage(os.getenv("POOL_DIR", "/repos/pool"))
                    free_gb = usage.free / (1024 ** 3)
                    if free_gb < min_free_disk_gb:
                        job.emit("error",
                                 f"Espace disque insuffisant ({free_gb:.1f} Go restants < "
                                 f"{min_free_disk_gb} Go) — arrêt du mirroir")
                        break
                except OSError:
                    pass

                pkg_name = pkg_row.get("name", "?")
                result = import_one(pkg_row, job.distribution, user, group=f"mirror-{job.source_id}")
                job.done_count += 1

                status = result["status"]
                if status == "added":
                    job.added_count += 1
                    if result.get("warning"):
                        job.emit("warning", f"  ⚠ {pkg_name} {result.get('version', '')} — {result['message']}")
                    else:
                        job.emit("success", f"  [ADD] {pkg_name} {result.get('version', '')} — {result['message']}")
                elif status == "pending_review":
                    job.pending_count += 1
                    job.emit("warning", f"  ⏳ {pkg_name} {result.get('version', '')} — {result['message']}")
                elif status == "blocked":
                    job.blocked_count += 1
                    job.emit("error", f"  ⛔ {pkg_name} — {result['message']}")
                elif status == "skipped":
                    job.skipped_count += 1
                else:  # error
                    job.error_count += 1
                    job.emit("error", f"  [FAIL] {pkg_name} — {result['message']}")

            # 4. Résumé final
            if job._stop.is_set():
                job.emit("warning",
                         f"⏹️ Mirroir annulé — {job.done_count}/{job.total} paquet(s) traités")
                job.status = "cancelled"
            else:
                job.emit("success",
                         f"✅ Mirroir terminé — {job.added_count} ajouté(s), "
                         f"{job.pending_count} en revue, {job.blocked_count} bloqué(s), "
                         f"{job.skipped_count} déjà présent(s), {job.error_count} erreur(s)")
                job.status = "done"

            audit_log("MIRROR", user, job.status.upper(),
                      detail=f"Mirroir {job.label} ({job.distribution}) : "
                             f"{job.added_count} ajoutés, {job.pending_count} en revue, "
                             f"{job.blocked_count} bloqués, {job.error_count} erreurs sur "
                             f"{job.total} paquet(s)")

        except Exception as exc:
            job.emit("error", f"Erreur inattendue : {exc}")
            job.status = "error"
        finally:
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job._new_log.set()

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _cleanup_old_jobs(self) -> None:
        """Supprime les jobs terminés depuis > 1h (appelé sous _lock)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        to_del = []
        for jid, job in self._jobs.items():
            if job.status != "running" and job.finished_at:
                try:
                    if datetime.fromisoformat(job.finished_at) < cutoff:
                        to_del.append(jid)
                except Exception:
                    pass
        for jid in to_del:
            del self._jobs[jid]


# Singleton global
mirror_manager = MirrorManager()
