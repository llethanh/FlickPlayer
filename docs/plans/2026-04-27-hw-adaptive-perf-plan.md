# Hardware-adaptive playback performance — Plan d'implémentation

**Date :** 2026-04-27
**Spec de référence :** [`2026-04-26-hw-adaptive-perf-design.md`](../specs/2026-04-26-hw-adaptive-perf-design.md)
**Plan connexe :** [`2026-04-26-ui-reskin-charter-plan.md`](2026-04-26-ui-reskin-charter-plan.md) — le badge FPS dans la status bar y est planifié ; ce plan ne fait qu'exposer la donnée.
**Statut :** actif
**Estimation totale :** ~4–5 jours, répartis sur 6 slices.
**Convention de branche :** `feat/hw-adaptive-perf`. Une PR par slice, mergées dans l'ordre.

---

## Principes de travail

1. **Un slice est mergeable indépendamment.** Après chaque slice l'app boote, lit une séquence, et est au moins aussi rapide que `main` — jamais pire. Le path PBO arrive derrière un flag pour qu'un chemin partiellement câblé ne puisse pas régresser le path sync par défaut.
2. **Logique pure d'abord, câblage avec effets de bord ensuite.** `perf/hardware.py`, `perf/runtime_state.py` et la couche persistance de `perf/calibration.py` sont testables sans Qt ni GL. Ils arrivent avant le code qui les importe.
3. **Le gate de non-régression (bench C, iGPU 780M) est rejouable dès slice 2.** C'est la ceinture de sécurité — au moment où on touche `app.py` pour brancher `compute_tune`, on doit pouvoir relancer bench C et confirmer pas de régression vs `perf/baseline_igpu_780m.json`.
4. **Les overrides CLI sont le filet utilisateur et gagnent toujours.** L'ordre de précédence est fixé en slice 2 et respecté par toutes les slices suivantes (calibration > heuristiques ; CLI > calibration).
5. **Le logging est un livrable, pas un debug aid.** Les lignes `[hw-tune]` et `[runtime]` font partie du contrat — un power user (ou l'auteur, six mois plus tard) les lit pour comprendre ce que l'app a décidé.

---

## Slice 1 — `HardwareProfile` + `compute_tune` (logique pure, pas de câblage)

**Objectif :** créer `perf/hardware.py` avec les dataclasses, `classify_gpu`, `compute_tune`, et les fallbacks `psutil` / `os.cpu_count()`. Aucun import de Qt, OIIO ou GL. Comportement de l'app inchangé.

### Fichiers
- **Nouveau :** `src/img_player/perf/__init__.py`, `src/img_player/perf/hardware.py`
- **Nouveau :** `tests/unit/test_hw_profile.py`

### Choix d'implémentation
- **`GpuKind` comme `Literal`** plutôt qu'`Enum` — moins cher à comparer dans les heuristiques, sérialisable trivialement vers le JSON de calibration ensuite. Le spec nomme déjà les valeurs ; on les fige comme source de vérité.
- **`classify_gpu` est ordre-sensible.** "Radeon RX" doit matcher avant le "radeon" générique pour qu'une Radeon RX 7900 XTX ne soit pas prise pour un iGPU. Ajouter un cas dans la table de test.
- **Factory `detect_hardware()`** qui retourne un `HardwareProfile` complet — prend `gpu_renderer: str | None` pour que le viewport (slice 4) puisse passer la vraie string, et que les tests passent des strings synthétiques.
- **`psutil`** — confirmer dans `pyproject.toml` qu'il est listé ; sinon l'ajouter dans cette slice. Fallback selon le spec si l'appel raise.
- **Aucun parsing CLI ici.** Le module reste argv-agnostique.

### Tests (gate)
- `test_hw_profile.py` : 12+ strings fixtures → résultats `classify_gpu`, dont "Radeon RX 7900", "AMD Radeon Graphics", "Intel UHD Graphics 770", "NVIDIA GeForce RTX 5070 Laptop GPU", "Mesa Intel(R) Arc(tm) A770", `""`, et une string gibberish délibérée → `unknown`.
- `compute_tune` paramétré sur les 3 setups de référence du spec (laptop iGPU, laptop dGPU, workstation dGPU) plus inputs extrêmes : `cpu_threads=2 ram=2GB`, `cpu_threads=128 ram=512GB`. Asserte les clamps documentés.
- `detect_hardware()` avec `psutil` et `os.cpu_count` monkey-patchés retournant `None` — doit donner `total_ram_gb` et `cpu_threads` non-zero.

### Effort
**Demi-journée** (3–4 h).

### Dépendances
Aucune. C'est la fondation.

---

## Slice 2 — Câblage de l'auto-tune au boot + overrides CLI + gate non-régression

**Objectif :** faire consommer `compute_tune` par `app.py` et `__main__.py` pour dimensionner `FrameCache(num_workers, budget_bytes)` et `oiio.attribute("threads", ...)` au démarrage. Ajouter les flags CLI `--no-pbo` / `--force-pbo` (parsés mais pas encore agis — ils settent juste `tune.use_pbo`, que rien ne lit encore). Ajouter la ligne de log `[hw-tune]`. **Première slice runnable avec impact mesurable** — bench C devient pertinent ici.

### Fichiers
- **Modifié :** `src/img_player/__main__.py` (ajouter les flags, parser, plomber les valeurs override)
- **Modifié :** `src/img_player/app.py` (remplacer l'usage des trois constantes `DEFAULT_*` par l'output de `compute_tune`, garder les constantes comme tier *fallback* selon le spec)
- **Nouveau :** `tests/unit/test_cli_overrides.py`
- **Modifié :** `tests/unit/test_smoke.py` peut nécessiter un petit update s'il asserte directement le budget par défaut.

### Choix d'implémentation
- **Le merge des overrides dans un helper unique**, ex. `apply_cli_overrides(tune, args) -> PerformanceTune`, dans `perf/hardware.py`. Fonction pure, facile à tester, réutilisable pour la slice calibration plus tard. Retourne un nouveau `PerformanceTune` (frozen dataclass + `dataclasses.replace`).
- **D'où vient `gpu_renderer` pour `detect_hardware()` ?** À la slice 2, **on ne l'a pas encore** — le contexte GL n'existe pas quand `app.py` décide la taille du cache. Résolution : passer `gpu_renderer=None`, ce qui fait retourner `"unknown"` par `classify_gpu` et fait que les heuristiques tombent sur les defaults "safe" (1 OIIO thread, no PBO). Garde le comportement iGPU 780M identique à aujourd'hui (matche bench C). Slice 4 introduit le signal `gpu_renderer_detected` et le **late-bind tune update** qui re-évalue `oiio_threads` et `use_pbo` une fois le renderer connu. À documenter clairement dans `app.py`.
- **Les constantes `DEFAULT_*` restent** comme tier fallback absolu — `compute_tune` peut maintenant les retourner sous "unknown", c'est OK. Ajouter un commentaire sur le nouveau rôle.
- **Le mutex `--no-pbo` / `--force-pbo`** est implémenté via `add_mutually_exclusive_group()`. Argparse gère l'erreur.

### Tests (gate)
- `test_cli_overrides.py` : paramétré sur `(argv, expected_tune_field, expected_value)`. Cas : pas de flags = auto, `--workers 12` explicite gagne, `--no-pbo` force `use_pbo=False`, `--force-pbo` force `use_pbo=True` même sur input integrated, les deux flags = `argparse` SystemExit.
- Les smoke tests existants doivent toujours passer.
- **Gate manuel (= bench C) :** lancer `--benchmark` sur l'iGPU 780M avec le protocole spec et confirmer ±5 % de `perf/baseline_igpu_780m.json`. Sauver comme `perf/postopti_igpu_autotune.json`. **C'est le gate de non-régression.**

### Effort
**3–4 h** pour le code, **+30 min** pour le bench C.

### Dépendances
Slice 1.

---

## Slice 3 — `RuntimeState` + health check au boot (safeguard pression mémoire)

**Objectif :** avant d'instancier le cache, snapshot `RuntimeState`, lancer `apply_runtime_constraints` pour clamper `cache_gb` contre la RAM réellement disponible, logger si réduit. Implémente §6 du spec.

### Fichiers
- **Nouveau :** `src/img_player/perf/runtime_state.py` (logique pure — pas de Qt)
- **Modifié :** `src/img_player/app.py` (un appel supplémentaire entre `compute_tune` et `FrameCache(...)`)
- **Nouveau :** `tests/unit/test_runtime_state.py`
- **Nouveau :** `scripts/ram_eater.py` (~10 lignes, pour rendre bench D reproductible)

### Choix d'implémentation
- **`apply_runtime_constraints` est une fonction pure** de `(tune, state)` — pas d'import `psutil` à l'intérieur. Le caller (dans `app.py`) construit le `RuntimeState`. Évite le mocking `psutil` dans les unit tests.
- **`RuntimeState.snapshot()` vit dans le même module** mais c'est la seule fonction qui importe `psutil`, gardée par try/except qui retombe sur des chiffres optimistes (RAM totale dispo, pas de swap). Le fallback ne doit *pas* shrinker le cache — mieux risquer le swap qu'estropier inutilement une machine où psutil est cassé.
- **Facteur de headroom** : le spec dit 60 % de la RAM dispo va au cache (40 % de marge pour les autres apps). Hard-coder `0.6` et le plancher `2.0 GB` comme constantes module.
- **Le warning de swap est log-only à cette slice** — la toast user-facing appartient à la slice 5 (runtime monitor) qui owne les signaux Qt.
- **La ligne de log de cache réduit** utilise le format exact du spec (`"reduced cache from X→Y GB (only Z GB available, leaving headroom for other apps)"`) pour qu'un script tail-the-log puisse grep dessus.

### Tests (gate)
- `test_runtime_state.py` : RAM ample = no-op ; RAM serrée shrinke ; RAM très serrée clampée au plancher 2 GB ; les autres champs de tune restent intacts ; `RuntimeState.snapshot()` avec `psutil.virtual_memory` monkey-patché retournant des valeurs canned ; erreur d'import `psutil` retourne snapshot optimiste.
- **Gate manuel (= bench D, partiel) :** lancer le script `ram_eater.py` qui alloue 6 GB et `time.sleep(...)`, puis `--benchmark`. Vérifier que la ligne `[hw-tune]` montre le cache réduit et que `psutil.swap_memory().used` ne croît pas pendant le bench. Sauver comme `perf/postopti_memory_pressure.json`.

### Effort
**Demi-journée** (3–4 h, dont le dry-run manuel de bench D).

### Dépendances
Slice 2.

---

## Slice 4 — Ring de 3 PBOs + late-bind GPU detection + `upload_gpu_us`

**Objectif :** la slice la plus risquée. Ajouter le path PBO ring à `gl_viewport.py`, gated sur `tune.use_pbo`. Capturer `gpu_renderer` au premier `initializeGL()` et émettre un signal Qt pour que `app.py` puisse re-lancer `compute_tune` avec la vraie classification GPU (puis re-appliquer les overrides CLI). Ajouter `upload_gpu_us` au bench recorder via `glFenceSync`.

### Fichiers
- **Modifié :** `src/img_player/render/gl_viewport.py` (état PBO sur le widget, branche `_upload_image_pbo`, ring de fences, signal `gpu_renderer_detected`)
- **Modifié :** `src/img_player/app.py` (subscribe au signal, re-compute `tune`, re-appliquer OIIO threads, push `use_pbo` au viewport via setter)
- **Modifié :** `src/img_player/bench/recorder.py` (étendre `PaintSample` avec `upload_gpu_us` et `upload_gpu_pending` ; ajouter constante `bench_format_version`)
- **Modifié :** `src/img_player/bench/summarize.py` (surface le nouveau champ, bumper `bench_format_version: 2`)
- **Nouveau :** `tests/unit/test_pbo_ring.py` (GL mocké — vérifie l'avance d'index, re-allocation sur changement de résolution, exception path désactive PBO pour la session)

### Choix d'implémentation
- **Le viewport owne une classe helper `_PboRing`** (définie dans `gl_viewport.py`, pas un module séparé — tightly coupled à l'état GL). Garde le corps de `paintGL` lisible : `if self._pbo_ring is not None: self._upload_image_pbo(...) else: self._upload_image_sync(...)`.
- **Le path sync reste bit-pour-bit identique** à l'actuel `_upload_image`. Seul son caller change. C'est ce qui protège bench C.
- **Timing de l'allocation PBO** : le spec dit "au premier `attach_to_sequence` après qu'on connaît la résolution" — mais `gl_viewport` ne connaît pas les sequences, juste les frames. Décision concrète : allouer paresseusement à l'intérieur de `_upload_image_pbo` au premier appel, et re-allouer quand `(width, height, channels)` change (mirroir le pattern existant `self._tex_alloc`). Pas besoin d'API `attach()`.
- **Taille du ring de fences = même que ring PBO (3)** — stocker une fence par slot PBO. Lire la fence prev-prev au paint suivant avec `glClientWaitSync(timeout=0)`. Si `GL_TIMEOUT_EXPIRED`, marquer le sample `upload_gpu_pending=True` et ne pas enregistrer `upload_gpu_us`. Compter les samples pending dans le report.
- **Flow late-bind tune** dans `app.py` :
  ```
  on gpu_renderer_detected(renderer):
      hw2 = detect_hardware(gpu_renderer=renderer)
      tune2 = apply_cli_overrides(compute_tune(hw2), self._cli_args)
      tune2 = apply_runtime_constraints(tune2, RuntimeState.snapshot())
      if tune2.use_pbo: viewport.enable_pbo()
      if tune2.oiio_threads != current: configure_oiio(tune2.oiio_threads)
      log [hw-tune] resolved (post-GL): ...
      # cache_gb / num_workers ne sont PAS ré-appliqués — le cache est déjà vivant
      # et on perdrait son contenu. Documenter ce caveat dans le log.
  ```
  C'est **le** point load-bearing de la slice — à signaler dans la description de PR.
- **Gestion d'échec PBO** : toute exception GL dans `_upload_image_pbo` (pas juste NULL map) → log warning → flip `self._pbo_ring = None` pour la session → le paint suivant prend le path sync. Pas de retry. Mandé par le spec.
- **Le bump de `bench_format_version`** est breaking pour les outils downstream. Documenter dans la PR que les anciens reports (v1) n'ont pas le nouveau champ.

### Tests (gate)
- `test_pbo_ring.py` avec `OpenGL.GL` mocké : index cycle 0→1→2→0 ; resize ré-alloue chaque PBO ; `glMapBufferRange` retournant 0 raise et désactive le PBO ; les paints suivants ne touchent pas au code PBO.
- Un nouveau `tests/integration/test_gl_smoke.py` (`pytest.mark.skipif(not has_display)`) — instancie `GLViewport`, paint une fois avec `use_pbo=False`, paint encore avec `use_pbo=True`. Ne valide pas les timings, juste "ça crashe pas".
- **Gates manuels :**
  - Bench A (`perf/postopti_rtx5070_autotune.json`, PBO **OFF** via `--no-pbo`) — confirme que slices 1–3 livrent à elles seules le gain auto-tune sur RTX 5070. Critère de pass de la table du spec.
  - Bench B (`perf/postopti_rtx5070_autotune_pbo.json`, PBO ON) — le gate spécifique au PBO. `upload_cpu_mean ≤ 8 ms`, `paint p99 ≤ 20 ms`, `effective fps paint ≥ 23.5`, aucun GPU sample pending.
  - Re-rouler bench C et confirmer pas de régression vs le résultat slice-2.

### Effort
**Journée pleine** (6–8 h). Fence + late-bind + bench wiring demandent attention. Prévoir une demi-journée tampon pour les surprises driver.

### Dépendances
Slices 1–3. **C'est la slice la plus risquée et elle arrive en quatrième position délibérément** — auto-tune + safeguard mémoire sont mergés et validés, le gate bench C est en place, mais on n'est pas encore dans le scaffolding calibration / runtime-monitor qui dépend de la stabilité de `upload_gpu_us`.

---

## Slice 5 — Runtime monitor avec auto-correction + signaux controller pour le badge FPS

**Objectif :** ajouter `RuntimeMonitor` (QTimer 1 Hz, fenêtre glissante 5 s), le câbler aux `play_started` / `play_stopped` du controller, exposer les trois signaux Qt de warning, et le laisser shrinker le cache sous pression de swap. Aussi exposer les signaux Qt `effective_fps_changed` et `cache_hit_rate_changed` sur le controller (§8 du spec) pour que le badge du plan UI re-skin puisse les consommer. Implémente §7 + §8 du spec.

### Fichiers
- **Nouveau :** `src/img_player/perf/runtime_monitor.py`
- **Modifié :** `src/img_player/player/controller.py` (ajouter méthode `cache_hit_rate()` à fenêtre glissante + signaux `effective_fps_changed(float)` et `cache_hit_rate_changed(float)`, émis au plus 1 Hz depuis un petit QTimer interne)
- **Modifié :** `src/img_player/cache/frame_cache.py` (ajouter méthode `shrink_budget(new_bytes)` qui re-lance `_evict_if_over_budget` avec un budget plus petit ; nécessaire pour l'auto-correction runtime)
- **Modifié :** `src/img_player/app.py` (instancier le monitor, hook les signaux vers les lignes de log + `MainWindow.set_status` pour l'instant — le badge orange réel vit dans le plan UI re-skin)
- **Nouveau :** `tests/unit/test_runtime_monitor.py`
- **Nouveau :** `tests/unit/test_controller_fps_signals.py` (petit, builds sur `test_controller_fps.py`)

### Choix d'implémentation
- **Trois signaux warning** (`playback_struggle`, `memory_pressure`, `frame_pacing_drop`) selon spec ; chacun porte une string en français prête à afficher. Mettre le message dans le payload du signal (plutôt qu'un seul enum) permet à `app.py` de router vers `set_status` sans que le monitor connaisse le vocabulaire UI.
- **Pas d'auto-grow** — une fois que le monitor shrinke le cache il reste shrinké pour la session. C'est dans le spec ; le mentionner explicitement dans la docstring pour qu'un futur contributeur ne "fixe" pas ça.
- **Toast `playback_struggle` une seule fois par sequence load** — tracker un booléen interne "fired this load", reset en écoutant `controller.frame_changed` et détectant un changement de sequence (mismatch de résolution / first frame). Plus propre que polling l'identité de sequence.
- **Fenêtre glissante `cache_hit_rate`** sur le controller utilise la même fenêtre `_TICK_WINDOW = 24` que `effective_fps`. Réutiliser le pattern `_tick_timestamps` deque existant ; ajouter un `_tick_hits: deque[bool]` parallèle. Émettre le signal à 1 Hz nécessite un petit QTimer dans le controller — ou, moins cher, throttler dans `_tick` (émettre seulement si `monotonic() - self._last_emit > 1.0`).
- **`shrink_budget` sur `FrameCache`** : juste `self._budget = new_bytes` puis `self._evict_if_over_budget()` sous le lock existant. Trivial. Tester que bytes-used baisse comme attendu.
- **Le monitor lit `psutil.swap_memory().used`** directement. On ne réutilise pas `RuntimeState.snapshot()` parce que celui-ci retourne une dataclass frozen ; le monitor a besoin du *delta* vs `swap_at_boot`, qui est son état propre.

### Tests (gate)
- `test_runtime_monitor.py` : samples synthétiques driver la fenêtre glissante ; les seuils crossings émettent exactement une fois ; la récupération n'auto-grow pas ; le monitor stoppe sur `play_stopped`. Mocker `psutil` et le cache.
- `test_controller_fps_signals.py` : 24 ticks simulés à 41.7 ms émettent `effective_fps_changed` proche de 24, et `cache_hit_rate_changed` proche du ratio seedé. Asserte aussi le throttle 1 Hz.
- Manuel : lancer un playback réel et tail le log pour les lignes `[runtime]` ; forcer un scénario de pression mémoire en allouant en arrière-plan.

### Effort
**Journée pleine** (6–8 h). Le monitor est conceptuellement simple mais il a beaucoup de petits morceaux Qt qui bougent (signaux, lifecycle de QTimer, hooking sur `play_started`).

### Dépendances
Slices 1–4. Le monitor lit `paint_p99` du bench recorder, qui a la nouvelle forme `upload_gpu_us` de slice 4.

---

## Slice 6 — Self-bench de calibration au premier lancement

**Objectif :** implémenter `calibration.ensure_profile()` selon §9 du spec. Au démarrage, chercher `~/.cache/img_player/profile.json` ; si la signature matche, utiliser le tune persisté ; sinon afficher un splash, lancer le mini-bench synthétique 10 frames, persister. Ajouter les flags `--skip-calibration` et `--recalibrate`. Les corrections de calibration s'intercalent **entre** les heuristiques et les overrides CLI selon la règle de précédence du spec.

### Fichiers
- **Nouveau :** `src/img_player/perf/calibration.py`
- **Modifié :** `src/img_player/__main__.py` (deux nouveaux flags)
- **Modifié :** `src/img_player/app.py` (appeler `ensure_profile()` après `compute_tune` et avant `apply_cli_overrides`)
- **Nouveau :** `tests/unit/test_calibration.py`

### Choix d'implémentation
- **Localisation du cache** : `Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / "profile.json"` — Qt-blessed, cross-platform, fonctionne dans les builds PyInstaller frozen. Fallback sur `Path.home() / ".cache" / "img_player" / "profile.json"` si Qt n'est pas init (ex. unit tests).
- **`hw_signature` est un SHA-1 de `(cpu_model, ram_gb_rounded, gpu_renderer)`** stocké à la fois en raw fields (pour inspection humaine) et en digest (pour comparaison cheap). Le spec montre la forme raw — la garder mais aussi stocker le digest comme top-level key.
- **Le splash utilise `QSplashScreen`** avec une `QProgressBar`. Modal-ish mais ne bloque pas l'event loop — la calibration tourne via `QTimer.singleShot(0, ...)` avec `QApplication.processEvents()` périodique pour que le splash repaint. Ou : lancer la calibration synchrone et update progress entre les 10 frames ; l'utilisateur n'attend que ~3 s.
- **Les frames synthétiques sont des arrays NumPy float16 random** générés en-mémoire (pas d'I/O disque — le spec veut mesurer decode/upload, pas le stockage). L'étape "decode" mentionnée par le spec est en fait le path `_upload_image` sur des données format-real-ish ; on n'a pas vraiment de décodeur pour des bytes en-mémoire. **Interprétation pragmatique** : skipper le bench decode dans la calibration et ne mesurer que `upload_cpu_mean` et `paint_total_mean`. Documenter cette divergence avec le spec dans la docstring du module calibration (c'est mineur ; le spec assume qu'un decoder existe pour input synthétique, mais `read_frame` ne lit que des fichiers ; sérialiser 10 EXR sur un temp dir ajouterait 1–2 s et user du disque pour un signal additionnel marginal).
- **Les corrections de calibration sont conservatrices** : si `upload_cpu_mean > 15 ms` flip `use_pbo=False` (exemple §9 du spec) ; si `decode_mean` indispo (per la simplification ci-dessus), ne pas toucher la fenêtre de prefetch — la laisser en future raffinement.
- **Gestion d'échec** : toute exception dans `ensure_profile()` est catched, loggée, et la fonction retourne le `tune` d'entrée inchangé. L'app doit booter.
- **`--skip-calibration`** bypass le profile lookup entièrement (utilise toujours heuristiques + CLI). **`--recalibrate`** delete (ou ignore) le profile existant et lance fresh.
- **Schema version 1** — bump sur tout changement de champ. JSON malformé ou mauvaise version = traité comme manquant.

### Tests (gate)
- `test_calibration.py` : égalité de signature, mismatch de signature trigger re-run, fichier manquant trigger run, JSON malformé traité comme manquant (warning loggé), `--skip-calibration` short-circuit, `--recalibrate` ignore l'existant, exception calibration fallback aux heuristiques sans crasher l'app. Utilise un fixture `tmp_path` pour la localisation du profile et mocke le bench réel (juste injecter des timings canned).
- Manuel : delete `profile.json`, lancer la GUI, vérifier que le splash apparaît, vérifier que le fichier est créé.

### Effort
**Demi à journée pleine** (4–6 h). La majorité du travail c'est l'UX du splash et le wiring soigneux de précédence `--recalibrate` / `--skip-calibration`.

### Dépendances
Slices 1–5. Dépend spécifiquement de la slice 4 (le path de mesure `upload_cpu_us` à travers le viewport GL) et de la slice 5 (les signaux du controller — le splash écoute `frame_changed` pour détecter que les 10 frames synthétiques ont été render).

---

## Risques et questions ouvertes

À flagger maintenant pour ne pas être surpris en plein milieu d'une slice :

1. **`gpu_renderer` n'est connu qu'après le premier `initializeGL()`** — mais le cache doit être dimensionné avant que le contexte GL existe. Slice 2 gère ça avec l'approche "deux phases" (boot avec `unknown` + late-bind sur le signal). Le risque : le path late-bind ne peut ajuster que `oiio_threads` et `use_pbo` sans perturber le cache vivant. Si un futur coefficient de tune dépend de la taille du cache *et* de la classification GPU, il faudra un refactor plus invasif. Documenter cette contrainte dans `app.py` pour qu'un futur contributeur n'essaie pas de redimensionner le cache mid-run.

2. **Sémantique `glClientWaitSync(timeout=0)` sur PyOpenGL** — les constantes `GL_ALREADY_SIGNALED`, `GL_CONDITION_SATISFIED`, `GL_TIMEOUT_EXPIRED` doivent venir de `OpenGL.GL.ARB.sync` ou similaire. Vaut un spike de 30 min avant de démarrer slice 4 pour confirmer le path d'import et que le binding retourne effectivement la valeur enum (certaines fonctions PyOpenGL retournent `None` au timeout). Si c'est cassé sur Windows + NVIDIA, fallback sur enregistrer juste `upload_cpu_us` (le spec §5 accepte déjà que `upload_gpu_us` soit diagnostique-only).

3. **PBO upload `GL_HALF_FLOAT`** — question ouverte de la fin du spec. Mitigation : le critère de pass de bench B inclut "no pending GPU samples". Si on voit > 5 % de samples pending, logger un `WARN` et envisager un opt-out automatique `use_pbo=False` par driver. Différer l'implémentation en follow-up sauf si bench B échoue franchement.

4. **`shrink_budget` et LRU live** — appeler `_evict_if_over_budget` pendant qu'un worker est mid-decode pourrait laisser la boucle d'éviction incapable de suivre si le worker continue d'ajouter des bytes. Le lock du cache sérialise tout donc c'est correct, mais vaut un stress test manuel en slice 5 (lancer playback avec cache 50 GB, shrinker à 5 GB en plein play, vérifier pas de crash et que `bytes_used` converge à ≤ 5 GB en ~2 s).

5. **Splash screen pendant les builds PyInstaller frozen** — `QStandardPaths.CacheLocation` retourne le bon truc mais les assets du splash (s'il référence une image) doivent être bundlés. Slice 6 : garder le splash text-only (un `QLabel` sur un `QSplashScreen`) pour éviter le terrier à lapin du bundling d'assets.

6. **Bench D (« gate sécurité graphiste »)** est plus délicat que les autres — il faut un process allocateur 6 GB qui tourne en parallèle de manière coordonnée. Fournir un petit `scripts/ram_eater.py` (~10 lignes) dans la slice 3 pour que le bench soit reproductible.

7. **Sémantique `controller.cache_hit_rate`** : le `cache.stats()` existant report un hit rate process-lifetime. Le controller a besoin d'un hit rate *recent-window*. §7 du spec dit que le runtime monitor le lit pour la détection de seuil — confirmer en slice 5 que la version rolling-window est ce que veut le monitor (oui) et qu'on ne double-compte pas les hits entre cache et controller. Documenter la différence dans les deux docstrings.

8. **Message d'erreur argparse mutex `--no-pbo` / `--force-pbo`** est le default argparse ("not allowed with"). Adéquat mais pas friendly. À décider en slice 2 : laisser tel quel ou wrapper dans un validator custom. Recommandation : laisser. Les flags power-user n'ont pas besoin de texte d'erreur poli.

---

## Justification de l'ordre des slices

| # | Slice | Justification de la position |
|---|---|---|
| 1 | HardwareProfile + compute_tune (logique pure) | Fondation ; rien d'autre ne compile sans. |
| 2 | Câblage auto-tune + CLI + gate bench C | Premier gain user-visible ; gate non-régression actif à partir d'ici. |
| 3 | Health check au boot | Safeguard low-risk ; arrive avant PBO pour que les scénarios swap-pressure soient déjà couverts quand on touche GL. |
| 4 | **PBO ring + late-bind GPU detection** | Slice la plus risquée ; arrive quand le scaffolding (auto-tune, override flow, gate bench) existe, mais avant que la calibration dépende de la forme de son timing. |
| 5 | Runtime monitor + signaux controller | Builds sur la forme du bench-recorder de slice 4 ; consommé par le splash de slice 6. |
| 6 | Calibration premier lancement | Dernière parce qu'elle dépend du path complet decode→upload instrumenté (slice 4) et des signaux per-tick du controller (slice 5). |

Selon le briefing : bench C (non-régression) est gated dès slice 2 ; la slice la plus risquée (PBO) est en quatrième (ni première ni dernière) ; la calibration est dernière.

### Fichiers critiques pour l'implémentation
- `src/img_player/app.py`
- `src/img_player/__main__.py`
- `src/img_player/render/gl_viewport.py`
- `src/img_player/bench/recorder.py`
- `src/img_player/player/controller.py`
