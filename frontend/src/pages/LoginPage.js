import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { login, mfaAuthenticate, requestPasswordReset, getSetupStatus, runSetup, getSetupPreflight } from "../api";
import { useAuth } from "../context/AuthContext";

export default function LoginPage() {
  const [username, setUsername]     = useState("");
  const [password, setPassword]     = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState("");
  const [showForgot, setShowForgot] = useState(false);

  // ── MFA step 2 ──────────────────────────────────────────────────────────────
  const [mfaToken, setMfaToken]   = useState(null);
  const [totpCode, setTotpCode]   = useState("");

  // ── Assistant de première installation ────────────────────────────────────
  const [needsSetup, setNeedsSetup] = useState(null); // null = loading, true/false

  useEffect(() => {
    getSetupStatus()
      .then((data) => setNeedsSetup(!!data.needs_setup))
      .catch(() => setNeedsSetup(false));
  }, []);

  const { signIn } = useAuth();
  const navigate = useNavigate();

  // ── Connexion step 1 ───────────────────────────────────────────────────────
  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!username || !password) {
      setError("Veuillez remplir tous les champs.");
      return;
    }
    setLoading(true);
    try {
      const { data } = await login(username, password);
      if (data.mfa_required && data.mfa_token) {
        // MFA activé → passer en step 2
        setMfaToken(data.mfa_token);
        setTotpCode("");
      } else {
        signIn(data.access_token);
        navigate("/");
      }
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401) {
        setError("Identifiant ou mot de passe incorrect.");
      } else if (status === 429) {
        setError("Trop de tentatives. Réessayez dans quelques minutes.");
      } else if (!err?.response) {
        setError("Le serveur n'est pas encore prêt. Patientez quelques secondes et réessayez.");
      } else {
        setError(`Erreur serveur (${status}). Réessayez dans quelques instants.`);
      }
    } finally {
      setLoading(false);
    }
  };

  // ── Connexion step 2 (TOTP) ────────────────────────────────────────────────
  const handleMfaSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!totpCode || totpCode.length !== 6) {
      setError("Saisissez le code à 6 chiffres de votre application.");
      return;
    }
    setLoading(true);
    try {
      const data = await mfaAuthenticate(mfaToken, totpCode);
      signIn(data.access_token);
      navigate("/");
    } catch (err) {
      const status = err?.response?.status;
      if (status === 401) {
        setError("Code invalide ou expiré. Réessayez.");
      } else {
        setError("Erreur lors de la vérification du code.");
      }
    } finally {
      setLoading(false);
    }
  };

  // ── Premier démarrage : aucun admin créé → assistant de configuration ──────
  if (needsSetup === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 to-gray-800">
        <svg className="animate-spin w-8 h-8 text-white" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
        </svg>
      </div>
    );
  }

  if (needsSetup) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 to-gray-800 py-8 px-4">
        <div className="w-full max-w-lg">
          <SetupWizard
            onDone={(accessToken) => {
              signIn(accessToken);
              navigate("/");
            }}
          />
        </div>
      </div>
    );
  }

  // ── Rendu step 2 : saisie code TOTP ───────────────────────────────────────
  if (mfaToken) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 to-gray-800">
        <div className="w-full max-w-sm">
          <div className="bg-white rounded-2xl shadow-2xl p-8">
            <div className="text-center mb-6">
              <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-blue-100 mb-4">
                <svg className="w-7 h-7 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
              </div>
              <h2 className="text-xl font-bold text-gray-900">Vérification en deux étapes</h2>
              <p className="text-sm text-gray-500 mt-1">
                Ouvrez votre application d'authentification et saisissez le code à 6 chiffres.
              </p>
            </div>

            <form onSubmit={handleMfaSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Code TOTP</label>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]{6}"
                  maxLength={6}
                  value={totpCode}
                  onChange={(e) => { setTotpCode(e.target.value.replace(/\D/g, "")); setError(""); }}
                  className={`w-full border rounded-lg px-4 py-3 text-center text-2xl font-mono tracking-widest
                    focus:outline-none focus:ring-2 focus:ring-blue-500
                    ${error ? "border-red-400 bg-red-50" : "border-gray-300"}`}
                  placeholder="000000"
                  autoFocus
                  autoComplete="one-time-code"
                />
              </div>

              {error && (
                <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                  <svg className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm-1-9v4a1 1 0 102 0V9a1 1 0 10-2 0zm0-4a1 1 0 112 0 1 1 0 01-2 0z" clipRule="evenodd"/>
                  </svg>
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={loading || totpCode.length !== 6}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                           disabled:cursor-not-allowed text-white font-medium py-2.5 rounded-lg
                           transition-colors text-sm"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                    </svg>
                    Vérification…
                  </span>
                ) : "Vérifier le code"}
              </button>

              <button
                type="button"
                onClick={() => { setMfaToken(null); setError(""); setTotpCode(""); }}
                className="w-full text-sm text-gray-500 hover:text-gray-700 py-1"
              >
                ← Retour à la connexion
              </button>
            </form>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-gray-900 to-gray-800">
      <div className="w-full max-w-sm space-y-3">

        {/* Carte principale */}
        <div className="bg-white rounded-2xl shadow-2xl p-8">
          {/* Logo */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center mb-4">
              <img src="/logo.png" alt="Repod" className="w-16 h-16 object-contain" />
            </div>
            <h1 className="text-2xl font-black tracking-wider text-gray-900 uppercase">Repod</h1>
            <p className="text-sm text-gray-500 mt-1">Connectez-vous pour continuer</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Utilisateur
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => { setUsername(e.target.value); setError(""); }}
                className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2
                  focus:ring-blue-500 focus:border-transparent
                  ${error ? "border-red-400 bg-red-50" : "border-gray-300"}`}
                placeholder="admin"
                autoFocus
                autoComplete="username"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Mot de passe
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(""); }}
                  className={`w-full border rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2
                    focus:ring-blue-500 focus:border-transparent
                    ${error ? "border-red-400 bg-red-50" : "border-gray-300"}`}
                  placeholder="••••••••"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-600 transition-colors"
                  tabIndex={-1}
                  aria-label={showPassword ? "Masquer le mot de passe" : "Afficher le mot de passe"}
                >
                  {showPassword ? (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"/>
                    </svg>
                  ) : (
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
                    </svg>
                  )}
                </button>
              </div>
            </div>

            {/* Erreur inline — toujours visible, ne disparaît pas */}
            {error && (
              <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                <svg className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm-1-9v4a1 1 0 102 0V9a1 1 0 10-2 0zm0-4a1 1 0 112 0 1 1 0 01-2 0z" clipRule="evenodd"/>
                </svg>
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                         disabled:cursor-not-allowed text-white font-medium py-2 rounded-lg
                         transition-colors text-sm mt-1"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                  </svg>
                  Connexion…
                </span>
              ) : "Se connecter"}
            </button>
          </form>

          {/* Lien mot de passe oublié */}
          <div className="mt-4 text-center">
            <button
              onClick={() => { setShowForgot(!showForgot); setError(""); }}
              className="text-sm text-blue-600 hover:text-blue-700 hover:underline"
            >
              Mot de passe oublié ?
            </button>
          </div>
        </div>

        {/* Panneau de réinitialisation (accordéon) */}
        {showForgot && (
          <ForgotPasswordPanel onClose={() => setShowForgot(false)} />
        )}
      </div>
    </div>
  );
}


// ── Assistant de première installation (création du compte admin) ────────────

const PREFLIGHT_LABELS = {
  database:   "Base de données",
  disk_space: "Espace disque",
  clamav:     "Antivirus (ClamAV)",
  grype:      "Scanner CVE (Grype)",
  secrets:    "Secrets applicatifs",
  tls:        "Certificat TLS",
};

function PreflightChecks() {
  const [checks, setChecks]   = useState(null); // null = loading
  const [errMsg, setErrMsg]   = useState("");

  useEffect(() => {
    getSetupPreflight()
      .then((data) => setChecks(data.checks))
      .catch(() => setErrMsg("Impossible de contacter le serveur pour le diagnostic."));
  }, []);

  if (errMsg) {
    return (
      <div className="flex items-start gap-2 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2.5 text-sm text-yellow-800">
        <svg className="w-4 h-4 text-yellow-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd"/>
        </svg>
        <span>{errMsg}</span>
      </div>
    );
  }

  if (!checks) {
    return (
      <div className="flex items-center justify-center py-4 text-gray-400 text-sm gap-2">
        <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
        </svg>
        Diagnostic en cours...
      </div>
    );
  }

  const allOk   = Object.values(checks).every((c) => c.ok);
  const failCnt = Object.values(checks).filter((c) => !c.ok).length;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-1.5">
        {Object.entries(checks).map(([key, check]) => (
          <div
            key={key}
            className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm
              ${check.ok ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"}`}
          >
            <div className="flex items-center gap-2">
              <span className="text-base leading-none">{check.ok ? "✅" : "❌"}</span>
              <span className="font-medium">{PREFLIGHT_LABELS[key] || key}</span>
            </div>
            <span className="text-xs opacity-75 text-right max-w-[50%] truncate" title={check.detail}>
              {check.detail}
            </span>
          </div>
        ))}
      </div>

      {!allOk && (
        <div className="flex items-start gap-2 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2.5 text-sm text-yellow-800">
          <svg className="w-4 h-4 text-yellow-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd"/>
          </svg>
          <span>
            {failCnt} {failCnt === 1 ? "point" : "points"} non satisfait{failCnt > 1 ? "s" : ""}.
            Vous pouvez continuer l'installation et corriger plus tard.
          </span>
        </div>
      )}

      {allOk && (
        <div className="flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-3 py-2.5 text-sm text-green-800">
          <svg className="w-4 h-4 text-green-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd"/>
          </svg>
          <span>Tous les pré-requis sont satisfaits.</span>
        </div>
      )}
    </div>
  );
}

function SetupWizard({ onDone }) {
  const [username, setUsername]   = useState("admin");
  const [password, setPassword]   = useState("");
  const [confirm, setConfirm]     = useState("");
  const [email, setEmail]         = useState("");
  const [appUrl, setAppUrl]       = useState(window.location.origin);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (username.trim().length < 3) {
      setError("Le nom d'utilisateur doit contenir au moins 3 caractères.");
      return;
    }
    if (password.length < 8) {
      setError("Le mot de passe doit contenir au moins 8 caractères.");
      return;
    }
    if (password !== confirm) {
      setError("Les mots de passe ne correspondent pas.");
      return;
    }

    setLoading(true);
    try {
      const data = await runSetup({
        admin_username: username.trim(),
        admin_password: password,
        admin_email: email.trim(),
        app_url: appUrl.trim(),
      });
      onDone(data.access_token);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        setError("La configuration initiale a déjà été effectuée. Rechargez la page.");
      } else {
        setError(err?.response?.data?.detail || "Impossible de finaliser la configuration.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Welcome header */}
      <div className="bg-white rounded-2xl shadow-2xl p-8">
        <div className="text-center mb-2">
          <div className="inline-flex items-center justify-center mb-4">
            <img src="/logo.png" alt="Repod" className="w-16 h-16 object-contain" />
          </div>
          <h1 className="text-2xl font-black tracking-wider text-gray-900 uppercase">Bienvenue sur Repod</h1>
          <p className="text-sm text-gray-500 mt-2 leading-relaxed max-w-md mx-auto">
            Gestionnaire de dépôts APT/RPM avec analyse de sécurité intégrée,
            inventaire machines et déploiement à distance.
          </p>
        </div>
      </div>

      {/* Preflight checks */}
      <div className="bg-white rounded-2xl shadow-xl p-6">
        <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
          <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Diagnostic pré-installation
        </h2>
        <PreflightChecks />
      </div>

      {/* Setup form */}
      <div className="bg-white rounded-2xl shadow-xl p-6">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Admin account section */}
          <div>
            <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
              Compte administrateur
            </h2>
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Nom d'utilisateur
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); setError(""); }}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                             focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  autoFocus
                  autoComplete="username"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Mot de passe
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(""); }}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                               focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="8 caractères min."
                    autoComplete="new-password"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Confirmer
                  </label>
                  <input
                    type="password"
                    value={confirm}
                    onChange={(e) => { setConfirm(e.target.value); setError(""); }}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                               focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    placeholder="Retapez le mot de passe"
                    autoComplete="new-password"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  E-mail <span className="text-gray-400 font-normal">(optionnel)</span>
                </label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                             focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="admin@example.com"
                  autoComplete="email"
                />
              </div>
            </div>
          </div>

          {/* Separator */}
          <div className="border-t border-gray-100" />

          {/* Configuration section */}
          <div>
            <h2 className="text-sm font-semibold text-gray-800 mb-3 flex items-center gap-2">
              <svg className="w-4 h-4 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              Configuration
            </h2>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                URL publique de l'application
              </label>
              <input
                type="url"
                value={appUrl}
                onChange={(e) => setAppUrl(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="https://repod.example.com"
              />
              <p className="text-xs text-gray-400 mt-1">
                Utilisée pour les notifications email et les liens. Modifiable plus tard dans les paramètres.
              </p>
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
              <svg className="w-4 h-4 text-red-500 mt-0.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm-1-9v4a1 1 0 102 0V9a1 1 0 10-2 0zm0-4a1 1 0 112 0 1 1 0 01-2 0z" clipRule="evenodd"/>
              </svg>
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                       disabled:cursor-not-allowed text-white font-semibold py-2.5 rounded-lg
                       transition-colors text-sm"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
                </svg>
                Configuration en cours...
              </span>
            ) : "Finaliser l'installation"}
          </button>
        </form>
      </div>
    </div>
  );
}


// ── Formulaire de demande de reset ────────────────────────────────────────────
function ForgotPasswordPanel({ onClose }) {
  const [username, setUsername] = useState("");
  const [loading, setLoading]   = useState(false);
  const [sent, setSent]         = useState(false);

  const handleRequest = async (e) => {
    e.preventDefault();
    if (!username.trim()) return;
    setLoading(true);
    try {
      await requestPasswordReset(username.trim());
      setSent(true);
    } catch {
      // L'API renvoie toujours 200 — une erreur ici = problème réseau
      toast.error("Impossible de contacter le serveur.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl shadow-xl p-6 border border-blue-100">
      {sent ? (
        <div className="text-center space-y-3">
          <div className="inline-flex items-center justify-center w-12 h-12 bg-green-100 rounded-full">
            <svg className="w-6 h-6 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7"/>
            </svg>
          </div>
          <p className="text-sm font-medium text-gray-800">Demande envoyée</p>
          <p className="text-xs text-gray-500">
            Si ce compte existe et dispose d'un email, un lien de réinitialisation
            a été envoyé. Il est valable <strong>30 minutes</strong>.
          </p>
          <button
            onClick={onClose}
            className="text-sm text-blue-600 hover:underline"
          >
            Retour à la connexion
          </button>
        </div>
      ) : (
        <>
          <h3 className="text-sm font-semibold text-gray-800 mb-1">
            Réinitialiser le mot de passe
          </h3>
          <p className="text-xs text-gray-500 mb-4">
            Entrez votre nom d'utilisateur. Si un email est associé à ce compte,
            vous recevrez un lien de réinitialisation.
          </p>
          <form onSubmit={handleRequest} className="space-y-3">
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Nom d'utilisateur"
              autoFocus
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                         focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={loading || !username.trim()}
                className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                           text-white text-sm font-medium py-2 rounded-lg transition-colors"
              >
                {loading ? "Envoi…" : "Envoyer le lien"}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-2 border border-gray-300 rounded-lg text-sm
                           text-gray-600 hover:bg-gray-50 transition-colors"
              >
                Annuler
              </button>
            </div>
          </form>

          {/* Fallback CLI pour les admins sans email */}
          <details className="mt-4">
            <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none">
              Pas d'email configuré ? (accès CLI)
            </summary>
            <div className="mt-2 bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-600 leading-relaxed">
              <p className="text-gray-400 mb-1"># Depuis le serveur :</p>
              <p className="break-all">
                docker exec backend-api python3 -c
                <br />"from auth.users import change_password;
                <br />change_password('admin', 'NouveauMotDePasse')"
              </p>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
