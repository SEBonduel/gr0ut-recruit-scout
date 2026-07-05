# GR0UT — Scout de recrutement (WoT)

Détecte automatiquement des **recrues potentielles** pour GR0UT : des joueurs
qui **quittent un clan francophone** surveillé et deviennent **sans clan**,
filtrés sur leur **WN8 global** et les **chars meta** possédés. Poste une fiche
candidat dans Discord. Gratuit via GitHub Actions.

## Principe

1. `watchlist.json` = liste de **68 clans FR** à surveiller (détectés automatiquement
   par analyse de leurs descriptions ; ~5300 joueurs suivis).
2. Chaque jour : snapshot des rosters (`rosters.json`, committé d'un run à l'autre).
3. Diff avec la veille → **départs**. Pour chacun, si désormais **sans clan** :
   - calcul du **WN8 global** (stats par char + table de valeurs attendues WN8),
   - vérification des **chars requis**.
4. Si **WN8 ≥ 1500** **et** possède **≥ 3** chars de la liste → **fiche candidat**
   (⭐ mise en avant si IS-7).

> La détection démarre au **2ᵉ run** (il faut deux snapshots pour comparer).

## Critères (modifiables via variables d'env)

| Variable | Défaut | Rôle |
|----------|--------|------|
| `MIN_WN8` | `1500` | WN8 global minimum |
| `MIN_TANKS` | `3` | Nombre minimum de chars requis possédés |

Chars requis (dans `main.py`) : IS-7 ⭐(prio), EBR 105, Obj. 907, Obj. 260,
Dravec, CS-63, Obj. 140, Leopard 1, VK 72.01 (K), E 100.

## Mise en place

1. **Webhook Discord** dans le salon recrutement → copier l'URL.
2. Pousser sur GitHub (repo **public** = minutes Actions illimitées).
3. Secrets (*Settings → Secrets and variables → Actions*) :

   | Secret | Valeur |
   |--------|--------|
   | `WG_APP_ID` | `00eed50e0468215e87ec936f17c52d8f` |
   | `RECRUIT_WEBHOOK_URL` | URL du webhook Discord |

4. Le cron tourne chaque jour à 09h Paris. Test manuel : Actions → *Run workflow*.

## Limites (honnêteté)

- « Français » = **inféré** de la surveillance de clans FR (l'API n'expose pas la
  nationalité). Un joueur FR qui quitte un clan non-FR non surveillé ne sera pas vu.
- **WN8 global** (à vie), pas « récent 30j » (non fourni par l'API sans suivi long).
- Ne couvre que les **68 clans** de `watchlist.json` (élargissable).

## Gérer la watchlist

`watchlist.json` : liste de `{clan_id, tag, name, members}`. Ajoute/retire des
clans à la main, ou relance le script de détection FR pour la régénérer.

## Test local

```bash
pip install -r requirements.txt
export WG_APP_ID=xxxx DRY_RUN=1
python main.py     # 1er run = baseline ; relancer pour détecter les départs
```
