# img_player V1 — Plan d'implémentation

**Date :** 2026-04-24
**Spec de référence :** [2026-04-24-img-player-v1-design.md](../specs/2026-04-24-img-player-v1-design.md)
**Statut :** actif

---

## Principes de travail

1. **Un jalon = une branche Git = un PR (ou un merge si repo solo privé).** Ça te permet de reviewer avant d'intégrer.
2. **Chaque tâche a un critère d'acceptation vérifiable** (test qui passe, commande qui fonctionne, écran qui s'affiche).
3. **On ne passe au jalon suivant que quand le précédent est "done done"** (tests verts + smoke test manuel OK).
4. **Si une tâche bloque > 30min**, on l'isole et on demande/cherche avant de continuer (plutôt que de forcer).

Convention de branches : `jalon-N-description-courte` (ex: `jalon-1-setup`).

---

## Jalon 1 — Setup projet

**Branche :** `jalon-1-setup`
**Objectif :** avoir un projet Python installable, un env conda reproductible, un repo GitHub privé, prêt à recevoir du code.

### Tâches

#### 1.1 — `pyproject.toml`
- [ ] Créer `pyproject.toml` avec métadonnées (name, version=0.1.0, python>=3.11, description, authors)
- [ ] Déclarer le package `img_player` en layout `src/`
- [ ] Deps runtime : PySide6, numpy, PyOpenGL (pas OIIO/OCIO — ils viennent de conda)
- [ ] Deps optionnelles `[dev]` : pytest, pytest-qt, ruff, mypy
- [ ] Entry point `img_player = "img_player.__main__:main"`

**Acceptance :** `pip install -e .` fonctionne dans l'env conda.

#### 1.2 — `environment.yml`
- [ ] Env conda nommé `img_player`, channel `conda-forge`
- [ ] python=3.11, openimageio, opencolorio, pyside6, numpy, pyopengl
- [ ] Section dev : pytest, pytest-qt, ruff, mypy

**Acceptance :** `conda env create -f environment.yml` crée un env fonctionnel, `python -c "import OpenImageIO; import PyOpenColorIO; import PySide6; print('ok')"` imprime `ok`.

#### 1.3 — Structure minimale du code
- [ ] Créer tous les dossiers de la section 4 du spec (`src/img_player/{sequence,io,cache,color,render,player,ui}`)
- [ ] Chaque dossier contient un `__init__.py` vide
- [ ] `src/img_player/__main__.py` avec une fonction `main()` qui affiche "img_player V0.1.0" et quitte
- [ ] `src/img_player/app.py` avec un stub qui crée un `QApplication` + `QMainWindow` vide affichant "img_player" et quitte après 2s (smoke test UI)

**Acceptance :**
- `python -m img_player` affiche le message et sort avec code 0
- (optionnel) lancement d'une version `--gui` affiche une fenêtre vide 2s

#### 1.4 — `tests/` squelette
- [ ] `tests/conftest.py` vide
- [ ] `tests/unit/test_smoke.py` avec un `def test_import_package(): import img_player` qui passe

**Acceptance :** `pytest` retourne 1 passed.

#### 1.5 — Lint & type
- [ ] `.ruff.toml` ou section dans `pyproject.toml` avec config ruff (line length 100, target py311)
- [ ] `mypy.ini` ou section pyproject avec config mypy (strict sur `src/img_player/`)

**Acceptance :** `ruff check .` passe, `mypy src/img_player/` passe sans erreur.

#### 1.6 — README
- [ ] Sections : intro, install (conda env create + pip install -e), lancement (`python -m img_player`), features V1 (copie de la roadmap du spec), licence, troubleshooting (OIIO installation conda)
- [ ] Lien vers le spec en bas

**Acceptance :** README lisible, install reproductible par un tiers.

#### 1.7 — LICENSE
- [ ] Fichier `LICENSE` avec texte MIT officiel, année 2026, nom user à confirmer

#### 1.8 — GitHub repo privé
- [ ] Vérifier que `gh` CLI est installé et authentifié (`gh auth status`)
- [ ] `gh repo create img_player --private --source=. --remote=origin`
- [ ] `git push -u origin main`

**Acceptance :** le repo est visible sur github.com/<user>/img_player en privé, avec le commit de spec + le commit de setup.

### Fin de jalon 1
- [ ] Merger `jalon-1-setup` dans `main` (via PR ou merge local si repo solo)
- [ ] Tag `v0.1.0-setup`

---

## Jalon 2 — Sequence detection & image I/O

**Branche :** `jalon-2-sequence-io`
**Objectif :** détecter une séquence d'images depuis un chemin et décoder une frame en numpy array.

### Livrables principaux

- `src/img_player/sequence/models.py` : dataclasses `FrameInfo(path, frame_number)`, `SequenceInfo(frames, fps_default, base_name, extension, width, height, channels_available)`
- `src/img_player/sequence/scanner.py` : `scan(path: Path) -> SequenceInfo`
  - Si fichier : détecte pattern `basename.####.ext` et scanne le dossier parent
  - Si dossier : cherche toutes les séquences, retourne la première (ou lève si multiple — à raffiner)
  - Supporte padding variable (`frame.1.exr` et `frame.0001.exr`)
  - Détecte trous dans la séquence et les expose dans `SequenceInfo`
- `src/img_player/io/formats.py` : liste des extensions supportées (query OIIO au démarrage)
- `src/img_player/io/reader.py` : `read_frame(path, channels=None) -> np.ndarray`
  - Retourne float32 HxWxC
  - Support multichannel EXR (sélection channels par nom)
  - Exception `FrameReadError` sur échec

### Tests

- `tests/unit/test_scanner.py` : patterns, padding, trous, extensions mixtes, cas erreurs
- `tests/unit/test_reader.py` : lecture PNG/EXR/DPX depuis fixtures, validation shape/dtype, gestion channels EXR
- `tests/conftest.py` : génération de fixtures via OIIO (petites séquences 32×32, variées)

### Smoke test manuel

CLI temporaire `python -m img_player.sequence.scanner <path>` qui imprime les infos de la séquence détectée.

### Fin de jalon 2
- Tous tests verts
- Scanner + reader scriptables, documentés dans le README

---

## Jalon 3 — Cache RAM + Player controller

**Branche :** `jalon-3-cache-player`
**Objectif :** cache RAM fonctionnel avec prefetch async, controller qui orchestre play/pause/seek — **sans UI** encore (pilotable en script).

### Livrables principaux

- `src/img_player/cache/frame_cache.py` : classe `FrameCache`
  - Interface `IFrameCache` (protocol)
  - Backend RAM LRU + prefetch directionnel
  - `get(idx)`, `request(idx, prio)`, `request_range(start, end, direction)`
  - Budget RAM configurable, éviction pondérée par distance tête de lecture
  - Thread-safe (lock interne)
- `src/img_player/cache/worker_pool.py` : `ThreadPoolExecutor` de decoders, queue prioritaire
- `src/img_player/player/state.py` : `PlaybackState` (dataclass immutable : frame, playing, loop_mode, in_out, fps)
- `src/img_player/player/controller.py` : `PlayerController`
  - `load_sequence(SequenceInfo)`, `play()`, `pause()`, `stop()`, `seek(frame)`, `set_loop(mode)`
  - Signaux Qt `frame_changed(int)`, `state_changed(PlaybackState)`, `dropped_frame(int)`
  - Utilise un `QTimer` pour le tick

### Tests

- `tests/unit/test_frame_cache.py` : LRU, éviction, prefetch, hits/misses, thread-safety (stress test 100 threads)
- `tests/integration/test_controller.py` (pytest-qt) : state machine, signaux émis au bon moment, comportement loop/ping-pong
- `tests/integration/test_cache_prefetch.py` : que le prefetch charge bien en avance, que le cache libère quand on dépasse

### Smoke test manuel

Script `scripts/headless_player.py` qui charge une séquence, lance play, imprime les frames affichées dans la console (pas d'UI).

### Fin de jalon 3
- Tous tests verts (>70% couverture sur `cache/` et `player/`)
- Script headless joue une séquence sans drop sur HD

---

## Jalon 4 — GPU render + OCIO

**Branche :** `jalon-4-render-color`
**Objectif :** afficher UNE frame dans une fenêtre Qt, avec color transform OCIO sur GPU.

### Livrables principaux

- `src/img_player/color/ocio_manager.py` :
  - Charge config OCIO (var env `OCIO` ou config built-in fallback)
  - `list_colorspaces()`, `get_processor(src, dst)`
- `src/img_player/color/gpu_processor.py` :
  - `build_shader(src, dst, exposure, gamma) -> str` (GLSL généré par OCIO GPU API)
- `src/img_player/render/shaders/` : shaders vertex + fragment base (OCIO injecté)
- `src/img_player/render/gl_viewport.py` : `QOpenGLWidget`
  - Init GL 3.3 core
  - `set_frame(ndarray)` — upload GL_RGB16F ou RGBA32F
  - `set_color_params(src, dst, exposure, gamma)` — recompile shader
  - Zoom + pan (souris)

### Tests

- `tests/unit/test_ocio_manager.py` : liste colorspaces, fallback config built-in
- `tests/unit/test_gpu_processor.py` : génération shader (validation GLSL via mock)
- Test manuel : petite app `scripts/show_frame.py` qui charge UN EXR et l'affiche avec un combo colorspace

### Smoke test manuel
- Fenêtre avec EXR ACEScg affiché en Rec709 : les couleurs doivent être correctes
- Slider exposure : ±2 stops cohérent visuellement
- Changement display sRGB ↔ Rec709 : différence visible

### Fin de jalon 4
- Tests verts
- Fenêtre affichant une frame avec OCIO OK sur HD

---

## Jalon 5 — UI complète

**Branche :** `jalon-5-ui`
**Objectif :** assemblage de tous les composants dans une vraie app utilisable.

### Livrables principaux

- `src/img_player/ui/main_window.py` : `QMainWindow`, menu bar, status bar, drag&drop handler
- `src/img_player/ui/viewer_widget.py` : container qui embed `GLViewport` + couche overlay Qt vide (préparée pour V3)
- `src/img_player/ui/timeline.py` : slider custom, frame numbers, in/out points
- `src/img_player/ui/transport.py` : boutons play/pause/stop/prev/next/first/last
- `src/img_player/ui/channel_panel.py` : sélection channel/layer pour EXR multichannel
- `src/img_player/ui/color_panel.py` : combos input/display colorspace, sliders exposure/gamma
- `src/img_player/app.py` : wire tout (MainWindow + Controller + Cache + Viewport)

### Tests

- Integration pytest-qt : click play → signal reçu, drag&drop → séquence chargée
- Tests UI rendu → skip (manuel)

### Smoke test manuel
- Drag & drop d'une séquence EXR → ça joue à 24fps
- Scrub timeline → frame change instantanément
- Changement channel → on voit le bon canal
- Changement display Rec709 → l'image devient claire (vs linear qui est sombre)

### Fin de jalon 5
- App utilisable sur scénario "review de séquence EXR HD"

---

## Jalon 6 — Polish & UX

**Branche :** `jalon-6-polish`
**Objectif :** transformer l'app utilisable en app agréable.

### Livrables

- Raccourcis clavier (espace, J/K/L, flèches, home/end, 1-4 pour channels, +/- exposure)
- Persistance settings (dernière séquence ouverte, colorspace default, budget cache) via `QSettings`
- Status bar avec : frame courante / total, fps effectif, hit ratio cache, RAM utilisée, frames dropped
- Gestion propre de la fermeture (flush cache, stop workers)
- Handling erreurs UX : notifs status bar pour fichiers corrompus, fallbacks, etc.
- Icons, titre fenêtre, "À propos"

### Fin de jalon 6
- V1 livrable. Tag `v1.0.0`.

---

## Après la V1

- Profiling sur 4K réel, décision sur proxy downscale
- Démarrage jalon V2 (Niveau 3 : comparaison A/B, scopes, pixel inspector)
- Ajout CI GitHub Actions (tests pytest sur push, lint check)

---

## État actuel

- [x] Spec écrit et committé (commit c2e7994)
- [x] Plan d'implémentation écrit (ce doc)
- [ ] **Prochaine action : Jalon 1, tâche 1.1 (pyproject.toml)**
