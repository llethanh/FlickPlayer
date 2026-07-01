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

**~1261 tests**, ~40 s en local. La suite doit passer au vert **sauf
3 échecs pré-existants connus** (juillet 2026), sans rapport avec le
travail courant — ne pas paniquer :

- `test_reader.py::test_read_exr_returns_rgba` + `test_read_multichannel_exr_default_is_rgba`
  attendent 4 canaux (RGBA) mais le reader renvoie 3 (RGB) **par
  design** — commit `ae8998f perf(io): default to RGB over RGBA for
  8x cold-decode speedup`. L'alpha se lit à la demande via le groupe
  RGBA du menu canaux. Tests périmés, pas un bug.
- `test_burnin_renderer.py::...test_disabled_bar_records_nothing`
  (top vs bottom) — pré-existant.
- `test_burnin_overlay.py` : erreur de collection (`Signal` non
  importé) — pré-existant, bloque la collecte de ce fichier seul
  (`pytest --ignore=tests/unit/test_burnin_overlay.py` pour le reste).

Lancer pytest depuis l'env conda activé (sinon PyAV/OIIO DLLs
manquantes) — `FlickPlayer.bat` ou `conda activate img_player` d'abord.

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

## Workflow de release (quand user dit "release", "bump une version", "lance tout")

"**version mineure**" / "**une version**" = bump **PATCH** (3e chiffre),
pas le 2e. Séquence complète, tout depuis `C:\dev\FlickPlayer\` :

1. `git pull --ff-only origin main`.
2. Bump la version dans **2 fichiers** : `pyproject.toml` (`version =`)
   + `src/img_player/__init__.py` (`__version__ =`).
3. Regen le splash : `python tools/regen_splash.py` (bake la version
   dans `src/img_player/assets/splash.png` — sinon splash périmé).
4. Website `docs/website/` : `index.html` (bouton download href +
   label + lede "Latest release") et `changelog.html` (nouveau bloc
   `<article class="release">` en TÊTE, classes `new`/`fix`/`perf`).
5. Commit "release: vX.Y.Z" + `git push origin main`.
6. Build : tuer `FlickPlayer.exe`, `rmdir /s /q build` (**garder
   `dist/`** — chaque version y coexiste), puis
   `cmd /c "C:\dev\FlickPlayer\build_exe.bat < nul"`. **Vérifier que
   le dossier produit = `FlickPlayer_vX.Y.Z`** (piège cache PyInstaller
   stale → shippe sous l'ancien nom). Sortie : dossier + `.zip` ~136 MB.
7. `gh release create vX.Y.Z dist\FlickPlayer_vX.Y.Z.zip --target main
   --title "..." --notes "..."`. Vérifier l'URL asset (HTTP 200).

Pièges commit multi-lignes en PowerShell : les here-strings `@'...'@`
et les caractères `>` / `"` cassent le parsing → écrire le message
dans un fichier et `git commit -F <fichier>`. Footer commit :
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## État courant (juillet 2026) — **v1.8.9** sur main

Historique récent (v1.8.4 → v1.8.9), toutes releasées sur GitHub :

- **v1.8.9 — snapshot-on-load + Reload manuel.** Une séquence chargée
  est un **snapshot** à l'instant du load : plus d'auto-grow quand un
  rendu écrit des frames. `SourceWatcher` (QFileSystemWatcher) tourne
  toujours mais `_on_source_watcher_fired` est gated sur la pref
  `source.auto_reload` (**défaut False**, `Preferences → General →
  Loading`). Reload à la demande : clic-droit layer → "Reload from
  disk" (smart) / "(force)", = Ctrl+R / Ctrl+Shift+R.
- **v1.8.8 — export séquence d'images ~5× plus rapide.** Le tail
  post-decode (OCIO + resize + bake + encode) est fanned out sur un
  `ThreadPoolExecutor` dans `export/engine.py::_run_parallel` (les 2
  étapes lourdes libèrent le GIL). Decode reste séquentiel (le
  `VideoSource` PyAV n'est pas thread-safe). Video output (mov/mp4)
  + petits exports restent serial. Mesuré 1080p+ACES : 2.0 → 10.2 fps.
  PNG `compressionLevel=1`.
- **v1.8.6/1.8.7** — export vidéo via PyAV (plus OIIO), EXR réseau
  plus rapide (AOV prefetch throttle + OIIO channel-subset GIL-free).
- **EXR reader = RGB par défaut** (3 canaux), pas RGBA — perf, alpha
  à la demande. Commit `ae8998f`. (Voir note tests plus haut.)
- **Politique d'éviction cache** (choix user, ne pas re-proposer FIFO
  pour les images) : `VideoSource` = FIFO strict ; `MasterFrameCache`
  (images) = score distance-au-playhead, fenêtre contiguë, timeline
  start vidé en premier.

Socle (livré avant v1.8.4, toujours valide) : disk cache 3-tiers
(RAM → lz4+half-float → decode), 3-tier prefs (user TOML > site TOML >
hardcodé), 8 OCIO builtin (défaut ACES 1.3 CG), lecture vidéo+audio,
PlayerController wall-clock anti-drift, PyAV+FFmpeg bundlés via
`img_player.spec`, Inno Setup dans `installer/`, site + changelog à jour.

**graphify** : graphe de connaissance dans `graphify-out/` (commité,
trackée). Rebuild = `/graphify` (ou skill graphify). Dernier : 5974
nodes / 295 communities, god node `ImgPlayerApp`.

## Mémoire transverse (~/.claude/MEMORY.md)

Mémoire user locale dans `C:\Users\<user>\.claude\projects\<session-key>\memory\`
qui couvre : profil user, charte design, feature log, comparaisons
avec OpenRV, etc. Locale à la machine, ne voyage pas avec git —
manuellement maintenue. Le `<session-key>` est dérivé du `cwd` au
moment du `claude` initial — depuis le switch C:\dev (juin 2026),
les nouvelles sessions devraient être keyed sur `C--dev-FlickPlayer`.
