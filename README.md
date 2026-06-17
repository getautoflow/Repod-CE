<p align="center">
  <img src="logo.png" alt="Repod" width="80" />
</p>

<h1 align="center">Repod — Community Edition</h1>

> **FR** | Gestionnaire de dépôt APT/RPM/APK privé avec interface web, contrôle d'accès par rôles et sécurité intégrée.
> **EN** | Private APT/RPM/APK repository manager with web UI, role-based access control, and built-in security scanning.

---

## Fonctionnalités principales / Key Features

| FR | EN |
|----|----|
| Interface web React + Tailwind | React + Tailwind web UI |
| API REST FastAPI, auth JWT | FastAPI REST API, JWT auth |
| 5 rôles RBAC (admin, maintainer, uploader, auditor, reader) | 5 RBAC roles (admin, maintainer, uploader, auditor, reader) |
| Auth locale PostgreSQL/bcrypt + LDAP/Active Directory optionnel | Local PostgreSQL/bcrypt auth + optional LDAP/AD |
| Scan antivirus ClamAV à chaque upload | ClamAV antivirus scan on every upload |
| Scan CVE Grype avec politique configurable | Grype CVE scan with configurable policy |
| Export SBOM (CycloneDX v1.5 + SPDX v2.3) | SBOM export (CycloneDX v1.5 + SPDX v2.3) |
| Journal d'audit immuable (JSONL) | Append-only audit trail (JSONL) |
| Statistiques de téléchargement | Download statistics |
| Import de paquets depuis sources APT amont | Package import from upstream APT sources |
| Dashboard de surveillance | Health monitoring dashboard |

---

## Community vs Enterprise

> **FR** — Cette édition **Community** est complète et utilisable en
> production pour la gestion d'un dépôt APT/RPM/APK avec scan ClamAV/Grype,
> RBAC, audit, import de paquets et tableau de bord. Certaines pages avancées
> (inventaire & scan SSH de flotte, déploiement distant, SBOM, sauvegarde
> automatisée, LDAP/OIDC/MFA, webhooks, alertes SLA, mirroring planifié,
> haute disponibilité) sont **visibles dans l'interface mais verrouillées** —
> elles nécessitent une licence **Enterprise**. Voir
> [repod.getautoflow.dev/#pricing](https://repod.getautoflow.dev/#pricing).
>
> **EN** — This **Community** edition is full-featured and production-ready
> for managing an APT/RPM/APK repository with ClamAV/Grype scanning, RBAC,
> audit logging, package import, and a health dashboard. Some advanced pages
> (fleet inventory & SSH scanning, remote deployment, SBOM, automated backups,
> LDAP/OIDC/MFA, webhooks, SLA alerts, scheduled mirroring, high availability)
> are **visible in the UI but locked** — they require an **Enterprise**
> license. See [repod.getautoflow.dev/#pricing](https://repod.getautoflow.dev/#pricing).

---

## Démarrage rapide / Quick Start

```bash
# 1. Cloner le dépôt / Clone the repository
git clone https://github.com/getautoflow/Repod-CE && cd Repod-CE

# 2. Configurer l'environnement / Configure environment
cp .env.example .env
# Editer .env : PUBLIC_URL, etc.
# Edit .env: PUBLIC_URL, etc.
cp backend.env.example backend.env
# Editer backend.env : JWT_SECRET_KEY (OBLIGATOIRE en prod)
# Edit backend.env: JWT_SECRET_KEY (REQUIRED in prod)

# 3. Lancer les conteneurs / Start containers
# Tire les images pré-construites depuis ghcr.io/getautoflow/repod-ce/*
# Pulls pre-built images from ghcr.io/getautoflow/repod-ce/*
docker compose -f docker-compose.yaml up -d
```

> **Compiler depuis les sources / Build from source:**
> ```bash
> docker compose -f docker-compose.yaml -f docker-compose.build.yml up -d --build
> ```

> **Developpement / Development:**
> ```bash
> docker compose -f docker-compose.yaml -f docker-compose.build.yml -f docker-compose.dev.yml up --build
> ```

---

## Déploiement avec TLS / TLS Deployment

**FR** — Le fichier `docker-compose.tls.yml` ajoute un reverse proxy nginx
(`repod-proxy`) qui termine le TLS sur les ports `443`/`8443` et redirige le
port `80`. Le frontend, le backend et l'apt-repo restent accessibles en local
(`apt-repo` passe sur le port `8085`).

```bash
# Certificat auto-signé (génère repos/certs/tls/) :
bash scripts/gen-selfsigned-certs.sh
docker compose -f docker-compose.yaml -f docker-compose.tls.yml up -d
```

**EN** — `docker-compose.tls.yml` adds an nginx reverse proxy (`repod-proxy`)
that terminates TLS on ports `443`/`8443` and redirects port `80`. The
frontend, backend, and apt-repo remain reachable locally (`apt-repo` moves to
port `8085`).

```bash
# Self-signed certificate (generates repos/certs/tls/):
bash scripts/gen-selfsigned-certs.sh
docker compose -f docker-compose.yaml -f docker-compose.tls.yml up -d
```

> **Let's Encrypt** (domaine public requis / public domain required) :
> ```bash
> export REPOD_DOMAIN=repod.example.com
> export CERTBOT_EMAIL=admin@example.com
>
> docker compose -f docker-compose.yaml -f docker-compose.tls.yml \
>                -f docker-compose.letsencrypt.yml up -d
> docker compose -f docker-compose.yaml -f docker-compose.tls.yml \
>                -f docker-compose.letsencrypt.yml run --rm certbot certonly
> ```
> Renouvellement / Renewal (cron / systemd timer) :
> ```bash
> docker compose -f docker-compose.yaml -f docker-compose.tls.yml \
>                -f docker-compose.letsencrypt.yml run --rm certbot renew
> docker compose restart repod-proxy
> ```

---

## ⚠ Avertissement securite / Security Warning

> **FR** — Aucun identifiant par defaut n'est fourni. Au premier demarrage,
> ouvrez l'interface web : si aucun compte admin n'existe, l'assistant de
> premiere installation (`/api/v1/setup`) s'affiche et vous permet de creer
> le premier compte administrateur (nom d'utilisateur + mot de passe).
>
> **EN** — No default credentials are shipped. On first start, open the web
> UI: if no admin account exists, the first-run setup wizard
> (`/api/v1/setup`) appears and lets you create the first administrator
> account (username + password).

Pour les deploiements automatises (sans wizard), un compte admin peut etre
pre-provisionne via `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` dans
`backend.env` (voir `backend.env.example`).
For automated deployments (no wizard), an admin account can be
pre-provisioned via `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` in
`backend.env` (see `backend.env.example`).

Pour generer un hash bcrypt / To generate a bcrypt hash:

```bash
docker run --rm python:3.10-slim python3 -c \
  "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('YourPass1!'))"
```

---

## Documentation

| Document | FR | EN |
|----------|----|----|
| Guide complet / Full guide | [docs.repod.getautoflow.dev/fr](https://docs.repod.getautoflow.dev/fr/) | [docs.repod.getautoflow.dev](https://docs.repod.getautoflow.dev/) |
| Architecture | [Architecture (FR)](https://docs.repod.getautoflow.dev/fr/explanation/architecture/) | [Architecture (EN)](https://docs.repod.getautoflow.dev/explanation/architecture/) |
| Installation | [Démarrage rapide (FR)](https://docs.repod.getautoflow.dev/fr/getting-started/) | [Getting started (EN)](https://docs.repod.getautoflow.dev/getting-started/) |
| Roles et acces | [Rôles & permissions (FR)](https://docs.repod.getautoflow.dev/fr/reference/roles/) | [Roles & permissions (EN)](https://docs.repod.getautoflow.dev/reference/roles/) |
| Securite | [Pipeline de sécurité (FR)](https://docs.repod.getautoflow.dev/fr/explanation/security-pipeline/) | [Security pipeline (EN)](https://docs.repod.getautoflow.dev/explanation/security-pipeline/) |

---

*3 conteneurs Docker : `frontend` (Nginx/React :3003) · `backend` (FastAPI :8000) · `apt-repo` (Nginx+reprepro :80)*
*3 Docker containers: `frontend` (Nginx/React :3003) · `backend` (FastAPI :8000) · `apt-repo` (Nginx+reprepro :80)*

---

## Licenses / Licences

The repod source code (backend and frontend) is licensed under the
**GNU Affero General Public License v3.0 (AGPL-3.0-only)** — see [LICENSE](./LICENSE).
A commercial license without the AGPL obligations is available — see
[LICENSE-COMMERCIAL.md](./LICENSE-COMMERCIAL.md).

Le code source de repod (backend et frontend) est distribué sous la
**GNU Affero General Public License v3.0 (AGPL-3.0-only)** — voir [LICENSE](./LICENSE).
Une licence commerciale sans les obligations de l'AGPL est disponible —
voir [LICENSE-COMMERCIAL.md](./LICENSE-COMMERCIAL.md).

### Third-party components / Composants tiers

repod's Docker image integrates the following third-party tools,
each distributed under their own license:

| Component | License | Usage | Source |
|-----------|---------|-------|--------|
| [reprepro](https://salsa.debian.org/brlink/reprepro) | GPL v2 | APT repo management — subprocess exec | [Source](https://salsa.debian.org/brlink/reprepro) |
| [ClamAV](https://www.clamav.net/) | GPL v2 | Antivirus scanning — Unix socket (clamd) | [Source](https://github.com/Cisco-Talos/clamav) |
| [Grype](https://github.com/anchore/grype) | Apache 2.0 | CVE vulnerability scanning | [Source](https://github.com/anchore/grype) |
| [Syft](https://github.com/anchore/syft) | Apache 2.0 | SBOM generation | [Source](https://github.com/anchore/syft) |
| [FastAPI](https://fastapi.tiangolo.com/) | MIT | Backend web framework | [Source](https://github.com/tiangolo/fastapi) |
| [React](https://react.dev/) | MIT | Frontend UI library | [Source](https://github.com/facebook/react) |
| [Tailwind CSS](https://tailwindcss.com/) | MIT | Frontend CSS framework | [Source](https://github.com/tailwindlabs/tailwindcss) |

reprepro and ClamAV are invoked as **independent processes** (subprocess
exec and Unix socket respectively) and are **not statically or dynamically
linked** against repod's code. Full GPL v2 source is available at the
upstream repositories listed above.

See [NOTICES](./NOTICES) for the complete list of third-party attributions
and [LICENSES/](./LICENSES/) for the full license texts.
