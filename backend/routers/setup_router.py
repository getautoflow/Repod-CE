"""
Routes du wizard de première installation.

Ces endpoints sont SANS AUTHENTIFICATION — nécessaire pour le bootstrap initial.
Une fois l'application configurée (admin créé), POST /setup renvoie 409.

Endpoints :
  GET  /setup/status  → statut du wizard (setup_done, needs_setup)
  POST /setup         → exécute la configuration initiale et retourne un JWT

Sécurité (SETUP_TOKEN) :
  Entre le démarrage du conteneur et la création du premier admin, POST /setup
  est accessible à quiconque atteint le réseau du backend (course possible).
  Si la variable d'environnement SETUP_TOKEN est définie, POST /setup exige
  un header `X-Setup-Token` correspondant — sinon 403. Si SETUP_TOKEN n'est
  pas défini, le comportement historique (aucune vérification) est conservé,
  pour ne pas casser les déploiements existants.
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from services.setup import (
    SetupAlreadyDoneError,
    SetupError,
    get_setup_status,
    run_setup,
)

router = APIRouter(prefix="/setup", tags=["Setup"])


class SetupRequest(BaseModel):
    admin_username: str = Field(..., min_length=3, description="Nom du premier compte administrateur")
    admin_password: str = Field(..., min_length=8, description="Mot de passe (≥ 8 caractères)")
    admin_email: str = Field("", description="Adresse e-mail de l'administrateur (optionnel)")
    admin_full_name: str = Field("", description="Nom complet affiché (optionnel)")
    app_url: str = Field("", description="URL publique de l'application (optionnel)")


@router.get("/status")
def setup_status():
    """
    Retourne l'état du wizard de première installation.
    Endpoint public — aucune authentification requise.

    Réponse :
      {
        "setup_done":  bool,
        "needs_setup": bool,
        "checked_at":  str
      }
    """
    return get_setup_status()


@router.post("/")
def setup(
    body: SetupRequest,
    x_setup_token: str = Header(default="", alias="X-Setup-Token"),
):
    """
    Effectue la configuration initiale de l'application.

    - Crée le premier compte administrateur.
    - Configure l'URL publique si fournie.
    - Retourne un JWT valide pour connexion immédiate.

    Endpoint public — aucune authentification requise (sauf si SETUP_TOKEN
    est défini, auquel cas le header X-Setup-Token est requis).
    Retourne 409 si l'application est déjà configurée.

    Réponse :
      {
        "admin_username": str,
        "access_token":   str,
        "token_type":     "bearer",
        "message":        str
      }
    """
    expected_token = os.getenv("SETUP_TOKEN", "")
    if expected_token and not hmac.compare_digest(x_setup_token, expected_token):
        raise HTTPException(status_code=403, detail="X-Setup-Token invalide ou manquant.")

    try:
        result = run_setup(
            admin_username=body.admin_username,
            admin_password=body.admin_password,
            admin_email=body.admin_email,
            admin_full_name=body.admin_full_name,
            app_url=body.app_url,
        )
    except SetupAlreadyDoneError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return result
