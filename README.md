# dour-planning

Scrape le line-up du **Dour Festival 2026** (15–19 juillet) et le pousse dans une
table Airtable partagée, pour préparer le planning avec l'équipe.

Pour chaque artiste : **nom · jour · scène · lien page Dour · lien Spotify**, plus
une colonne checkbox « 👍 je veux voir » par membre de l'équipe (Max, Kev, MyMy,
Levi, PM, Alex, Neavus).

## Particularités

- **Re-runnable** : l'upsert se fait sur `(Page Dour, Jour)`. Relancer le script
  met à jour les infos **sans jamais écraser les votes** de l'équipe.
- Les **horaires** ne sont pas encore publiés par Dour (sortie ~juin/juillet) :
  la colonne `Heure` reste vide jusque-là, on relance le script quand ça sort.
- La **scène** est détectée dans le texte de la page artiste. Les artistes sans
  scène détectée sont signalés dans les logs (`scene non detectee pour ...`) pour
  compléter la liste `STAGE_ALIASES` dans `main.py`.

## Lancer (sur T-600, jamais en local)

```bash
git clone https://github.com/Peaime/dour-planning.git
cd dour-planning
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseigner AIRTABLE_TOKEN + AIRTABLE_BASE_ID
set -a && . ./.env && set +a
python3 main.py
```

Pour rafraîchir plus tard (quand Dour publie horaires/scènes) :

```bash
cd dour-planning && git pull && . .venv/bin/activate
set -a && . ./.env && set +a && python3 main.py
```

La table `Line-up` est créée automatiquement au premier run. Ensuite, dans
Airtable, créer une **vue par personne** (filtre `=coché` sur sa colonne,
groupée par `Jour`) avec le champ `Spotify` affiché pour écouter directement.
