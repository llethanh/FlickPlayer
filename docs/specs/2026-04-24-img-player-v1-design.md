# img_player — Design spec V1

**Date :** 2026-04-24
**Statut :** validé en brainstorming, prêt pour implémentation
**Version cible :** V1 (Niveau 2)

---

## 1. Vision

**img_player** est un lecteur de séquences d'images pour contextes VFX/compositing, écrit en Python. Il vise un équilibre entre simplicité (usage quotidien personnel) et qualité pro (formats techniques, gestion colorimétrique sérieuse).

Inspiration : DJV / RV / mrViewer — sans leur complexité. On s'inspire conceptuellement d'OpenRV sans le cloner.

### Roadmap fonctionnelle

| Version | Niveau | Contenu |
|---------|--------|---------|
| **V1** (ce spec) | Niveau 2 | Viewer sérieux : play/scrub, OCIO, channels EXR, exposure/gamma, loop, in/out, raccourcis, drag&drop |
| V2 | Niveau 3 | Comparaison A/B (wipe/diff), scopes (histogramme/waveform/vectorscope), pixel inspector, playlists |
| V3 | Niveau 4 | Annotations/dessin sur frame, commentaires pour workflow de review (retakes supervisor) |
| V4+ | — | Lecture vidéo |

### Cible de performance

- **HD (1920×1080) @ 24/25 fps** — cible prioritaire, doit être fluide sur laptop moderne.
- **4K (3840×2160) @ 24 fps** — l'architecture doit tenir sans s'effondrer ; proxy downscale envisageable plus tard si besoin.
- **Plateforme** : Windows en priorité (poste de dev actuel), mais code portable Linux/macOS (pas de dépendance Windows-only).

---

## 2. Stack technique

| Couche | Techno | Raison |
|--------|--------|--------|
| Langage | **Python 3.11+** | Productivité, écosystème VFX |
| UI | **PySide6** (Qt 6.6+) | Standard UI desktop, QOpenGLWidget natif |
| Décodage images | **OpenImageIO (OIIO)** | Référence VFX : EXR multichannel, DPX, TIFF, PNG, JPG, TGA, etc. |
| Couleur | **OpenColorIO (OCIO)** | Standard VFX : sRGB, Rec709, ACES. GPU shader API intégrée. |
| Rendu | **OpenGL 3.3+** via QOpenGLWidget + PyOpenGL | Upload texture + OCIO GPU shader |
| Données | **numpy** | Buffers frames (float32 HxWxC) |
| Env mgmt | **conda / mamba** (conda-forge) | OIIO et OCIO n'ont pas de wheels pip Windows fiables |
| Tests | **pytest** + **pytest-qt** | Standard Python |
| Lint/type | **ruff** + **mypy** | Qualité code |
| VCS | **Git + GitHub** (repo privé) | Branches, PRs, historique |
| Licence | **MIT** | Permissive, standard |

---

## 3. Architecture

### 3.1 Schéma en couches

```
┌──────────────────────────────────────────────┐
│  UI (PySide6)                                │
│  MainWindow / ViewerWidget / Timeline /      │
│  Controls / Panels                           │
└────────────┬─────────────────────────────────┘
             │
┌────────────▼─────────────────────────────────┐
│  PlayerController (orchestrateur)            │
│  state machine : play/pause/seek/loop        │
└────┬───────────┬────────────┬────────────────┘
     │           │            │
┌────▼────┐  ┌───▼────┐  ┌────▼─────────┐
│ Cache   │  │ Render │  │ Sequence     │
│ (RAM)   │  │ (GPU)  │  │ (metadata)   │
└────┬────┘  └───┬────┘  └──────────────┘
     │           │
┌────▼───────────▼─────────────────────────────┐
│  I/O backend (OIIO)     │   Color (OCIO)     │
│  decode EXR/DPX/PNG...  │   GPU shader LUT   │
└──────────────────────────────────────────────┘
```

### 3.2 Règles de threading

- **Un seul thread UI** (main/Qt). Il ne fait jamais de décodage ni d'I/O disque bloquante.
- **Thread pool de workers** (N = `min(8, cpu_count)`) qui décodent via OIIO. Résultat = numpy array poussé dans le cache.
- **Cache RAM** central, thread-safe (lock interne), partagé entre workers et renderer.
- **Renderer GPU** (QOpenGLWidget) pull les frames du cache (main thread Qt) et upload en texture GPU.
- **Communication** :
  - UI ↔ Controller : signaux Qt.
  - Controller → workers : `queue.Queue` de requêtes de décodage.
  - Workers → Cache : insertion avec lock.

### 3.3 Pipeline de rendu (GPU)

Décidé : **pipeline GPU** (pas CPU). Standard VFX (RV, DJV, Nuke).

- Chaque frame décodée = numpy float32 HxWxC (RGB ou RGBA), scene-linear (ou espace d'origine selon config).
- Upload via `glTexSubImage2D` en `GL_RGBA32F` (ou `GL_RGB16F` pour économiser la VRAM).
- OCIO génère dynamiquement un shader GLSL qui applique : `src_colorspace → exposure → display_colorspace → gamma`.
- Une seule draw call par frame (quad plein écran + sampler).
- Changement exposure/gamma/colorspace = régénération shader (< 10 ms), pas de re-décode.

---

## 4. Découpage en modules

```
img_player/
├── pyproject.toml
├── environment.yml             # env conda
├── README.md
├── LICENSE                     # MIT
├── .gitignore
├── docs/
│   └── specs/
│       └── 2026-04-24-img-player-v1-design.md   # ce doc
├── src/img_player/
│   ├── __init__.py
│   ├── __main__.py             # python -m img_player
│   ├── app.py                  # bootstrap Qt, wire l'app
│   │
│   ├── sequence/               # détection & métadonnées
│   │   ├── scanner.py
│   │   └── models.py
│   │
│   ├── io/                     # décodage via OIIO
│   │   ├── reader.py
│   │   └── formats.py
│   │
│   ├── cache/                  # cache RAM thread-safe
│   │   ├── frame_cache.py
│   │   └── worker_pool.py
│   │
│   ├── color/                  # OCIO
│   │   ├── ocio_manager.py
│   │   └── gpu_processor.py
│   │
│   ├── render/                 # GPU display
│   │   ├── gl_viewport.py
│   │   └── shaders/
│   │
│   ├── player/                 # orchestration
│   │   ├── controller.py
│   │   └── state.py
│   │
│   └── ui/
│       ├── main_window.py
│       ├── viewer_widget.py    # host gl_viewport + overlays (préparé V3)
│       ├── timeline.py
│       ├── transport.py
│       ├── channel_panel.py
│       └── color_panel.py
│
└── tests/
    ├── conftest.py             # génération fixtures
    ├── fixtures/
    ├── unit/
    └── integration/
```

### 4.1 Interfaces publiques entre modules

| Module | Fonction / classe | Contrat |
|--------|-------------------|---------|
| `sequence.scanner` | `scan(path: Path) -> SequenceInfo` | Prend fichier ou dossier. Détecte pattern `name.####.ext`. Retourne liste ordonnée + fps par défaut. |
| `io.reader` | `read_frame(path, channels=None) -> np.ndarray` | float32 HxWxC. Ne fait AUCUNE conversion colorspace. Lève `FrameReadError` en cas d'échec. |
| `cache.FrameCache` | `get(idx) -> np.ndarray \| None` | Non-bloquant. `None` si miss. |
| `cache.FrameCache` | `request(idx, priority)` | Enqueue un décodage async. |
| `cache.FrameCache` | `request_range(start, end, direction)` | Prefetch directionnel. |
| `color.OCIOManager` | `list_colorspaces() -> list[str]` | Liste des colorspaces de la config active. |
| `color.GPUProcessor` | `build_shader(src, dst, exposure, gamma) -> str` | Retourne GLSL. |
| `render.GLViewport` | `set_frame(np.ndarray)` | Upload + redraw. Main thread Qt uniquement. |
| `render.GLViewport` | `set_color_params(src, dst, exposure, gamma)` | Recompile le shader OCIO. |
| `player.PlayerController` | signaux : `frame_changed(int)`, `state_changed(PlaybackState)` | Communication UI. |

### 4.2 Préparation pour V2/V3

- `ui/viewer_widget.py` est un **container** au-dessus de `gl_viewport`. Il sert à empiler des overlays Qt (annotations, pixel inspector, scopes en picture-in-picture) sans toucher au rendu image. Cela permettra V3 (annotations) sans refonte.
- `cache/frame_cache.py` expose une interface abstraite `IFrameCache`. Le backend actuel est RAM. En V2, on pourra ajouter un backend GPU-résident (textures persistantes) sans toucher au Controller.

---

## 5. Flux de données

### 5.1 Ouverture d'une séquence (drag & drop)

```
Drag&drop sur MainWindow
  → sequence.scanner.scan(path)                    [main thread, rapide]
  → SequenceInfo { frames: [...], fps, w, h, channels_available }
  → PlayerController.load_sequence(info)
  → FrameCache.attach(info)                         # reset cache, configure taille
  → Controller passe en état "Stopped" sur frame 0
  → request_range(0, prefetch_ahead)
  → signal frame_changed(0) émis → ViewerWidget update
```

### 5.2 Lecture (play)

```
User clique Play
  → Controller démarre un QTimer cadencé à 1000/fps ms
  → à chaque tick :
      1. current_frame += 1 (ou loop/ping-pong selon état)
      2. cache.get(current_frame)
         ├─ HIT  → set_frame(array) → GLViewport upload + redraw
         └─ MISS → dropped frame counter++, log, tick suivant
      3. cache.request_range(current+1, current+prefetch_ahead)

[en parallèle, thread pool de workers] :
  - consomme la queue de requests
  - pour chaque frame : oiio.ImageInput → numpy float32
  - push dans FrameCache (avec lock)
  - éviction LRU si cache > budget RAM
```

### 5.3 Changement colorspace / exposure / gamma

```
User change display → Rec709 (ou bouge slider exposure)
  → ColorPanel émet signal
  → OCIOManager.build_processor(src, dst, exposure, gamma)
  → GPUProcessor génère nouveau shader GLSL
  → GLViewport.set_shader(glsl) (compile < 10ms)
  → redraw avec la frame courante déjà en VRAM (pas de re-décode)
```

---

## 6. Stratégie de cache

- **Type** : cache RAM LRU avec prefetch directionnel.
- **Unité cachée** : frame complète en `np.ndarray` float32 HxWxC (scene-linear, sans transform couleur).
- **Budget par défaut** : 8 Go (configurable via settings).
- **Politique d'éviction** : LRU pondéré par distance à la tête de lecture (les frames éloignées sont candidates en premier, même si récemment accédées).
- **Prefetch** :
  - Direction = sens de lecture (forward par défaut, backward si play reverse).
  - `prefetch_ahead` = 150 frames par défaut, `prefetch_behind` = 50 frames.
  - Sur seek : flush des requests en vol, re-prioritize autour de la nouvelle position.
- **Drop de frames plutôt que ralentir** : si miss pendant play, on skippe, on incrémente un compteur, on garde le temps réel.
- **Stats exposées** : hit ratio, RAM utilisée, frames dropped — visibles dans une status bar optionnelle pour debug.

---

## 7. Gestion des erreurs

### I/O & séquences
- **Fichier corrompu** → placeholder "BROKEN" rouge, log path. Pas de crash.
- **Séquence avec trous** → scanner propose : "combler avec frame précédente" (défaut) ou "gap noir".
- **Format non supporté** → message UI clair, pas d'ouverture.
- **Fichier > budget cache / 2** → warning loggé, on tente quand même.

### Couleur / OCIO
- **Pas de config OCIO** → fallback sur config built-in embarquée (ACES CG + sRGB + Rec709).
- **Colorspace input inconnu** → heuristique : EXR=scene-linear, PNG/JPG=sRGB, DPX=Cineon. Override manuel possible.
- **Shader OCIO qui ne compile pas** (GPU trop vieux) → fallback CPU + notif.

### Cache & mémoire
- **Budget atteint** → LRU évince les frames éloignées.
- **OOM (`MemoryError`)** → catch, libère 50% cache, retry. Second échec = erreur claire UI.

### GPU / OpenGL
- **Pas d'OpenGL 3.3+** → check démarrage, message clair, quit propre.
- **Upload texture fail** (VRAM pleine) → downscale affichage + warning.

### Principe général
Jamais de crash silencieux. Logs dans `~/.img_player/logs/`. Pas de dialogues modaux bloquants en V1 — status bar + log file.

---

## 8. Tests

### Unit (sans Qt, rapides)
- `sequence/scanner.py` : détection pattern, trous, unicité, tri.
- `io/reader.py` : ouverture EXR/PNG/DPX, handling de channels, erreurs.
- `cache/frame_cache.py` : LRU, éviction, thread-safety (stress test).
- `color/ocio_manager.py` : liste colorspaces, build processor.

### Integration (pytest-qt)
- `player/controller.py` : state machine play/pause/seek/loop, émission signaux.
- Cache + controller : prefetch déclenché correctement, drop compté sur miss.
- Pas de vrai OpenGL (mock du viewport).

### Manuel / smoke
- Rendu GPU testé visuellement avec séquences fixtures livrées.
- Scénarios de review manuels documentés dans `docs/testing/`.

### Fixtures
- 3-4 petites séquences générées dans `conftest.py` via OIIO :
  - EXR single channel RGB
  - EXR multichannel (RGBA + depth + cryptomatte)
  - PNG 8-bit
  - DPX 10-bit

### Objectif couverture V1
- `sequence/`, `io/`, `cache/`, `color/` : > 70%.
- `render/`, `ui/` : testés manuellement.

---

## 9. Git / GitHub / jalons

### Setup initial
- `git init -b main` ✅ (fait)
- `.gitignore` Python + conda + IDE ✅ (fait)
- `README.md` : install conda, lancement, features, troubleshooting
- `LICENSE` : MIT
- Repo GitHub **privé** via `gh repo create` (à faire après accord user)

### Jalons (1 branche + 1 PR par jalon)
1. **Setup** — structure projet, `pyproject.toml`, `environment.yml`, README, LICENSE, repo GitHub privé. CI GitHub Actions reportée après V1 fonctionnelle.
2. **sequence + io** — scanner + reader + tests unit. Commande CLI temporaire `img_player scan <path>` pour valider.
3. **cache + player** — FrameCache + Controller, scriptable sans UI (test headless)
4. **render + color** — GLViewport + OCIO GPU shader, fenêtre Qt minimale qui affiche UNE frame avec color transform
5. **ui** — MainWindow assemblée, transport, timeline, panels
6. **polish** — raccourcis clavier, drag&drop, settings persistantes

Chaque jalon ≈ une semaine de travail équivalent, ajustable selon rythme perso.

---

## 10. Décisions hors-scope V1 (notées pour plus tard)

- Lecture audio (inexistant en V1 — image/séquence uniquement).
- Lecture vidéo (mp4/mov) — V4+.
- Export / rendering (pas de "save as").
- Proxy disque (calcul de thumbnails demi-résolution sur disque) — à envisager si V1 montre ses limites sur 4K.
- Support GPU decoding (NVDEC etc.) — pas pertinent en V1, OIIO CPU suffit.
- Plugins externes / scripting utilisateur — non.

---

## 11. Risques identifiés

| Risque | Impact | Mitigation |
|--------|--------|-----------|
| OIIO/OCIO installation conda capricieuse sous Windows | bloquant | Documenter une procédure conda-forge testée dans README ; fournir `environment.yml` lockfile |
| Perfs Python/numpy insuffisantes 4K | cible dégradée | Profiling dès jalon 3 ; fallback proxy downscale en V1.1 si besoin |
| OpenGL drivers obsolètes sur la machine | crash au démarrage | Check version au démarrage, message clair |
| Taille cache RAM mal calibrée | drop frames constant | Stats visibles, budget ajustable, doc de sizing selon RAM |
| Scope creep vers Niveau 3/4 prématurément | retard V1 | Discipline : V1 = Niveau 2 uniquement. Les préparations archi (viewer_widget container, IFrameCache) rendent V2/V3 possibles sans tout refaire. |
