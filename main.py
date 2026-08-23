#!/usr/bin/env python3
"""
GR0UT — Scout de recrutement.

Surveille une liste de clans FR (watchlist.json), détecte les joueurs qui
les quittent et deviennent SANS CLAN, calcule leur WN8 global + vérifie
qu'ils possèdent assez de chars meta, et poste une fiche candidat dans Discord.

Méthode : snapshot quotidien des rosters -> diff -> filtres -> fiche.
La détection démarre au 2e run (il faut deux snapshots pour comparer).
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

APP_ID = os.environ.get("WG_APP_ID", "").strip()
WEBHOOK_URL = os.environ.get("RECRUIT_WEBHOOK_URL", "").strip()
API_BASE = os.environ.get("WG_API_BASE", "https://api.worldoftanks.eu")
PORTAL = os.environ.get("WG_PORTAL", "https://worldoftanks.eu")
# Région tomato.gg pour le lien de profil (EU / NA / ASIA).
TOMATO_REGION = os.environ.get("TOMATO_REGION", "EU")

WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "watchlist.json")
ROSTER_FILE = os.environ.get("ROSTER_FILE", "rosters.json")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

# --- Critères de recrutement ------------------------------------------------
MIN_WN8 = int(os.environ.get("MIN_WN8", "1500"))
MIN_TANKS = int(os.environ.get("MIN_TANKS", "3"))
PRIORITY_TANK = 7169  # IS-7
REQUIRED_TANKS = {
    7169: "IS-7", 19009: "EBR 105", 15617: "Obj. 907", 58369: "Obj. 260",
    7281: "Dravec", 5265: "CS-63", 16897: "Obj. 140", 14609: "Leopard 1",
    58641: "VK 72.01 (K)", 9489: "E 100",
}
WN8_EXP_URL = "https://static.modxvm.com/wn8-data-exp/json/wn8exp.json"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "gr0ut-recruit-scout/1.0"})
TRANSIENT = {"SOURCE_NOT_AVAILABLE", "REQUEST_LIMIT_EXCEEDED"}


def api_get(path, _retries=3, **params):
    params["application_id"] = APP_ID
    url = f"{API_BASE}/{path.strip('/')}/"
    last = None
    for attempt in range(_retries):
        try:
            r = SESSION.get(url, params=params, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if payload.get("status") == "ok":
                return payload["data"]
            err = payload.get("error") or {}
            last = RuntimeError(f"API error on {path}: {err}")
            if err.get("message") not in TRANSIENT:
                raise last
        except requests.RequestException as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise last


# --- WN8 ---------------------------------------------------------------------

def load_wn8_expected():
    """{tank_id: {expDamage, expDef, expFrag, expSpot, expWinRate}}."""
    r = SESSION.get(WN8_EXP_URL, timeout=30)
    r.raise_for_status()
    return {row["IDNum"]: row for row in r.json()["data"]}


def compute_wn8(tank_rows, exp):
    """WN8 agrégé à partir des stats par char (all)."""
    tot = {"dmg": 0.0, "spot": 0.0, "frag": 0.0, "def": 0.0, "win": 0.0,
           "battles": 0, "exp_dmg": 0.0, "exp_spot": 0.0, "exp_frag": 0.0,
           "exp_def": 0.0, "exp_win": 0.0}
    for t in tank_rows:
        e = exp.get(t["tank_id"])
        a = t.get("all") or {}
        b = a.get("battles") or 0
        if not e or b <= 0:
            continue
        tot["battles"] += b
        tot["dmg"] += a.get("damage_dealt", 0)
        tot["spot"] += a.get("spotted", 0)
        tot["frag"] += a.get("frags", 0)
        tot["def"] += a.get("dropped_capture_points", 0)
        tot["win"] += a.get("wins", 0)
        tot["exp_dmg"] += e["expDamage"] * b
        tot["exp_spot"] += e["expSpot"] * b
        tot["exp_frag"] += e["expFrag"] * b
        tot["exp_def"] += e["expDef"] * b
        tot["exp_win"] += e["expWinRate"] * b
    if tot["battles"] == 0 or tot["exp_dmg"] == 0:
        return 0
    rDAMAGE = tot["dmg"] / tot["exp_dmg"]
    rSPOT = tot["spot"] / tot["exp_spot"]
    rFRAG = tot["frag"] / tot["exp_frag"]
    rDEF = tot["def"] / tot["exp_def"]
    rWIN = tot["win"] * 100 / tot["exp_win"]
    rWINc = max(0, (rWIN - 0.71) / (1 - 0.71))
    rDAMAGEc = max(0, (rDAMAGE - 0.22) / (1 - 0.22))
    rFRAGc = max(0, min(rDAMAGEc + 0.2, (rFRAG - 0.12) / (1 - 0.12)))
    rSPOTc = max(0, min(rDAMAGEc + 0.1, (rSPOT - 0.38) / (1 - 0.38)))
    rDEFc = max(0, min(rDAMAGEc + 0.1, (rDEF - 0.10) / (1 - 0.10)))
    wn8 = (980 * rDAMAGEc + 210 * rDAMAGEc * rFRAGc + 155 * rFRAGc * rSPOTc
           + 75 * rDEFc * rFRAGc + 145 * min(1.8, rWINc))
    return round(wn8)


# --- Rosters -----------------------------------------------------------------

def load_watchlist():
    with open(WATCHLIST_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def fetch_rosters(clan_ids):
    """{clan_id(str): set(account_ids)} pour tous les clans (lots de 100)."""
    rosters = {}
    for i in range(0, len(clan_ids), 100):
        chunk = clan_ids[i:i + 100]
        data = api_get("wgn/clans/info", clan_id=",".join(map(str, chunk)),
                       game="wot", fields="members.account_id")
        for cid, c in (data or {}).items():
            members = (c or {}).get("members") or []
            rosters[str(cid)] = [m["account_id"] for m in members]
    return rosters


def load_snapshot():
    try:
        with open(ROSTER_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_snapshot(rosters):
    with open(ROSTER_FILE, "w", encoding="utf-8") as fh:
        json.dump(rosters, fh)


# --- Évaluation des candidats ------------------------------------------------

def account_infos(ids):
    """account/info par lots -> {id: {clan_id, nickname, last_battle_time, global_rating}}."""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = api_get("wot/account/info", account_id=",".join(map(str, chunk)),
                       fields="nickname,clan_id,last_battle_time,global_rating")
        out.update({int(k): v for k, v in (data or {}).items() if v})
    return out


def evaluate(account_id, exp):
    """Renvoie une fiche candidat si critères OK, sinon None."""
    data = api_get(
        "wot/tanks/stats", account_id=account_id,
        fields=("tank_id,all.battles,all.damage_dealt,all.frags,all.spotted,"
                "all.dropped_capture_points,all.wins"),
    )
    tanks = (data or {}).get(str(account_id)) or []
    owned = {t["tank_id"] for t in tanks
             if (t.get("all") or {}).get("battles", 0) > 0}
    matched = [REQUIRED_TANKS[t] for t in REQUIRED_TANKS if t in owned]
    if len(matched) < MIN_TANKS:
        return None
    wn8 = compute_wn8(tanks, exp)
    if wn8 < MIN_WN8:
        return None
    return {"wn8": wn8, "tanks": matched, "has_priority": PRIORITY_TANK in owned}


# --- Discord -----------------------------------------------------------------

def post_candidate(cand, info, former_tag):
    name = info.get("nickname") or str(cand["account_id"])
    star = "⭐ " if cand["has_priority"] else ""
    profile = f"https://tomato.gg/stats/{name}-{cand['account_id']}/{TOMATO_REGION}"
    tanks = ", ".join(("**IS-7**" if t == "IS-7" else t) for t in cand["tanks"])
    lbt = info.get("last_battle_time")
    seen = f"<t:{lbt}:R>" if lbt else "—"
    embed = {
        "title": f"{star}🎯 Recrue potentielle : {name}",
        "url": profile,
        "description": (
            f"**WN8 global : {cand['wn8']}** · Cote perso : {info.get('global_rating', '?')}\n"
            f"A quitté **{former_tag}** · dernière bataille {seen}\n"
            f"Chars meta ({len(cand['tanks'])}) : {tanks}"
            + ("\n\n⭐ **Possède l'IS-7 (priorité)**" if cand["has_priority"] else "")
        ),
        "color": 0x1ABC9C if cand["has_priority"] else 0x95A5A6,
        "footer": {"text": "GR0UT • Scout recrutement"},
    }
    body = {"embeds": [embed]}
    if DRY_RUN or not WEBHOOK_URL:
        print("[DRY-RUN]", json.dumps(body, ensure_ascii=False))
        return
    SESSION.post(WEBHOOK_URL, json=body, timeout=20).raise_for_status()


# --- Entrée ------------------------------------------------------------------

def main():
    if not APP_ID:
        sys.exit("WG_APP_ID manquant.")

    watch = load_watchlist()
    tag_by_id = {str(c["clan_id"]): c["tag"] for c in watch}
    clan_ids = [c["clan_id"] for c in watch]

    try:
        current = fetch_rosters(clan_ids)
    except RuntimeError as exc:
        print(f"[warn] API indisponible, run ignoré : {exc}")
        return

    prev = load_snapshot()
    save_snapshot(current)

    if not prev:
        print("Snapshot initial des rosters enregistré. Détection dès le prochain run.")
        return

    # Départs = présents avant, absents maintenant (par clan).
    leavers = {}
    for cid, members in prev.items():
        gone = set(members) - set(current.get(cid, []))
        for aid in gone:
            leavers.setdefault(aid, cid)  # 1er clan quitté qui compte
    print(f"{len(leavers)} départ(s) détecté(s) sur {len(watch)} clans.")
    if not leavers:
        return

    # On ne garde que ceux désormais SANS clan.
    infos = account_infos(leavers.keys())
    clanless = {aid: infos[aid] for aid in leavers
                if aid in infos and not infos[aid].get("clan_id")}
    print(f"{len(clanless)} désormais sans clan.")

    exp = load_wn8_expected()
    posted = 0
    for aid, info in clanless.items():
        try:
            cand = evaluate(aid, exp)
        except RuntimeError:
            continue
        if not cand:
            continue
        cand["account_id"] = aid
        post_candidate(cand, info, tag_by_id.get(leavers[aid], "?"))
        posted += 1
    print(f"{posted} fiche(s) candidat postée(s).")


if __name__ == "__main__":
    main()
