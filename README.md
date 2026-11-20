# Job Radar

Recherche d'offres en automatisation de laboratoire, bioproduction et sciences du vivant, en France, en Suisse ou dans le monde.

## Lancer

```bash
./launch_job_radar.sh
```

Ou directement :

```bash
.venv/bin/python -m streamlit run docs/job/job_radar_app.py
```

Sous Windows, utiliser `launch_job_radar.bat`.

## Interface

- **Offres** : tableau compact de 25 offres par page, avec l'annonce sélectionnée à droite. Recherche textuelle, filtre pays et tri par pertinence, date ou entreprise. Le menu **Filtres** contient les options secondaires. Le menu **Exporter** télécharge toute la sélection filtrée, pas uniquement la page affichée.
- **Sites carrière** : accès direct aux portails officiels, agences et sources de thèses, sans lancer de collecte. Les sites manuels sont présentés en premier. Les liens restent disponibles lorsqu'une collecte échoue. Une recherche préremplie est proposée lorsque le portail le permet.
- **Candidatures** : favoris, notes, statuts et dates de relance. Le journal reste disponible entre les lancements.

**Nouvelle recherche** regroupe le choix emploi/thèse, les pays, les familles de métiers, les employeurs et les requêtes. Les paramètres de collecte ne changent les résultats qu'après lancement. Le bouton de préférences permet d'ajuster les compétences valorisées et les pays préférés.

La vue par cartes, le panorama, le Top 20 et les compteurs multiples ont été retirés.

## Classement

Le classement mesure la proximité avec les métiers et les compétences du profil. Il ne présume ni zéro année d'expérience ni absence de droit au travail. Les années demandées, diplômes, langues et autorisations de travail ne provoquent aucune exclusion ni pénalité automatique. Les détails de l'annonce restent lisibles intégralement dans la fiche.

Seuls les intitulés explicitement seniors sont masqués par défaut : Senior, Sr., Principal, Staff Engineer/Scientist, Director, Head of, etc. Le filtre **Inclure les postes seniors** permet de les consulter. Les mots « manager » et « responsable », un intitulé Engineer II, ou les annonces ouvertes à plusieurs niveaux ne suffisent pas à exclure une offre.

Le score de proximité attribue jusqu'à 70 points aux compétences, 20 au métier et 10 à sa présence dans le titre. Les fonctions d'un autre domaine, comme les tests logiciels ou les fonctions commerciales, sont moins bien classées mais restent consultables. Une description partielle limite la quantité d'indices disponibles. Le tri combine ensuite cette proximité, la fraîcheur, les pays préférés et la source officielle. Il ne prédit pas la probabilité de réponse.

## Sources

Le registre est dans `docs/job/job_radar/companies.json`. Les connecteurs Workday, Workable, RSS, SmartRecruiters et Greenhouse sont utilisés par les employeurs configurés. Le connecteur Ashby reste disponible pour d'autres registres. Adaptyv est exclu de Job Radar.

Les portails marqués manuels restent accessibles dans **Sites carrière**, notamment Merck, UCB, Automata, Opentrons et Benchling. Les autres sites officiels restent également consultables directement, même lorsqu'ils disposent d'un connecteur automatique.

Le mode thèse utilise Doctorat.gouv.fr, ABG et myScience. Campus France, EURAXESS, Academic Positions et ANRT Cifre sont accessibles dans **Sites carrière → Thèses**. Indeed, Manpower, Adecco et les autres agences sont regroupés dans **Sites carrière → Agences**.

La collecte n'est pas exhaustive. Workday et SmartRecruiters sont plafonnés à 20 pages par requête. Les erreurs sont détaillées dans **Sites carrière**. Une annonce sauvegardée peut avoir fermé depuis la dernière recherche.

## Données locales et tests

La dernière collecte, les préférences et le journal sont stockés dans `.job_radar/radar.sqlite3`, exclu de Git. `JOB_RADAR_DB` permet de choisir un autre emplacement. **Préférences → Données locales → Vider les résultats** conserve les candidatures et les notes. Les dates de relance sont consultables dans le journal ; aucun message automatique n'est envoyé.

```bash
.venv/bin/python -m pytest -q tests/test_job_radar.py tests/test_career_site_coverage.py tests/test_job_radar_matching.py tests/test_job_radar_ui.py
```
