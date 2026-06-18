# Flick Player — notes Claude Code

Lu automatiquement à chaque session par Claude Code. Ce fichier
voyage avec le repo via git. Mets-le à jour en commitant quand
le workflow change.

## Au démarrage de chaque session

**Toujours commencer par** :
```bash
cd /c/dev/FlickPlayer
git pull
```

## Workflow — TOUT depuis `C:\dev\FlickPlayer\`

**Un seul emplacement de travail** : `C:\dev\FlickPlayer\`.
Édition du code, sessions Claude Code, tests pytest, builds
PyInstaller, benchs, scripts ad-hoc, `gh release create` —
tout tourne depuis ce dossier.

GitHub (`https://github.com/llethanh/FlickPlayer.git`) est la
source de vérité. Le clone local pousse / tire depuis là.

⚠️ **Drive (`G:\Mon Drive\…\img_player_V001\`) n'est plus utilisé**
pour le travail courant (juin 2026). Drive Stream corrompt les
bundles PyInstaller, ses chemins avec espaces + accents (`_PERSO`)
cassent certains outils, et la séparation "édite sur Drive, run
sur C:\dev" introduisait trop d'ambiguïté. Si un ancien clone
Drive existe encore, le laisser en lecture seule.

## Setup machine neuve

Une seule fois par machine :

```bash
git clone https://github.com/llethanh/FlickPlayer.git C:\dev\FlickPlayer
cd C:\dev\FlickPlayer
conda env create -f environment.yml
conda activate img_player
```

Ensuite, sessions normales = `git pull` puis on code.

## Lancer les tests

```bash
conda activate img_player
pytest tests/
```

**Toute la suite doit passer au vert** (971 tests, ~20 s en local).
Pas de deselects pré-existants : la dette d'anciens tests obsolètes
qui traînait jusqu'à mai 2026 a été nettoyée dans le pass
"Mock-fragility + behavior-drift" — voir le commit `tests: revive 36
broken tests` pour le détail (FrameCache duck-typing + master-frame
vs source-frame confusion + obsolete UI feature removed).

## Builder un bundle

```cmd
cd C:\dev\FlickPlayer
git pull
build_exe.bat
```

Le `.bat` détecte les chemins Drive / OneDrive / Dropbox et
refuse de tourner — protection contre une rechute accidentelle.

Output : `dist\FlickPlayer_v<X.Y.Z>\` (~380 MB depuis v1.8.0,
PyInstaller 6.20 dédup plus agressif que les anciennes versions)
+ `dist\FlickPlayer_v<X.Y.Z>.zip`. Le zip part directement sur
GitHub via `gh release create` — plus de copie vers Drive/dist/.

Pour wrap en installer Inno Setup voir `installer/README.md`.

## État courant (mai 2026)

- **v1.5.13** sur main — release "Smoother playback + code health"
- **Perf hot path (Tier 1)** : OpenGL uniform-location caching +
  LUT bind-once → paint mean −25 % (6 802 → 5 089 µs), paint max
  −92 % (593 → 47 ms, plus de spikes visibles). OCIO shader bundle
  LRU. Composite math `np.multiply(out=tmp)` scratch buffer.
  Scanner `os.scandir`. Lazy imports hot-path hoistés.
- **Refacto structurel (Tier 2)** : 180-LOC `_evict_if_over_budget`
  split en 25 + 5 helpers ; `_on_frame_changed` + `_refresh_after_stack_change`
  splittés en helpers nommés ; `cache/_common.py` extrait ; canonical
  `enrich_with_header` ; `_signature_token` helper.
- **Dette de tests purgée** : suite 971/0/0 (avant : 20 failed,
  16 errors, 3 deselected). Causes racines : Mock `spec=FrameCache`
  rejetait nouvelles méthodes ; master-frame vs source-frame
  confusion ; comportement changé non répercuté ; features UI
  obsolètes.
- Disk cache 3-tiers (RAM → disque lz4+half-float → source decode)
  livré v1.5.5. Survit close/reopen. Pre-paint timeline en orange
  clair pour les frames disponibles disque.
- **Disk cache roadmap E + F livrée** :
  E1 shutdown drain 10s + FlushIndicator, E2 sweep blobs orphelins,
  E3 auto-reload via QFileSystemWatcher, E4 PRAGMA user_version
  migration, F lock cross-process + read-only fallback.
- **Perf disk-cache** : format v2 struct-header (1.5× faster) +
  v3 no-compression option pour NVMe rapides (5.3× faster, toggle
  dans Preferences > Disk cache > Storage).
- **3-tier prefs system** (v1.5.8+) : user TOML > site TOML >
  hardcoded. `flick.toml` à côté de `FlickPlayer.exe` ou dans
  `%APPDATA%\FlickPlayer\flick.toml`.
- **8 builtin OCIO configs** (v1.5.10) : ACES 1.3 / 2.0, CG / Studio,
  default ACES 1.3 matchant Nuke / Maya / OpenRV.
- Lecture vidéo (mp4/mov/mkv/m4v/avi) + audio sounddevice opérationnels
- Toggles M/S par layer pour mute/solo audio
- PlayerController en mode wall-clock (anti-drift A/V)
- PyAV + FFmpeg DLLs bundlés via `img_player.spec`
- Inno Setup template prêt dans `installer/`
- Site `docs/website/index.html` à jour avec hero / features / changelog

## Mémoire transverse (~/.claude/MEMORY.md)

Mémoire user locale dans `C:\Users\<user>\.claude\projects\<session-key>\memory\`
qui couvre : profil user, charte design, feature log, comparaisons
avec OpenRV, etc. Locale à la machine, ne voyage pas avec git —
manuellement maintenue. Le `<session-key>` est dérivé du `cwd` au
moment du `claude` initial — depuis le switch C:\dev (juin 2026),
les nouvelles sessions devraient être keyed sur `C--dev-FlickPlayer`.
