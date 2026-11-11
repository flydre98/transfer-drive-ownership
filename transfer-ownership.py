#!/usr/bin/env python3
"""
Trasferisce ricorsivamente la proprietà di una cartella Google Drive (e di tutto
il suo contenuto: sottocartelle, file, scorciatoie) a un altro utente della
stessa organizzazione Google Workspace.

Requisiti:
    pip install google-api-python-client google-auth-oauthlib

Setup (una tantum):
    1. Google Cloud Console -> crea (o scegli) un progetto
    2. Abilita "Google Drive API"
    3. Credenziali -> Crea ID client OAuth -> tipo "App desktop"
    4. Scarica il JSON e salvalo come credentials.json accanto allo script

Uso:
    # prova senza modificare nulla
    python transfer_drive_ownership.py <ID_CARTELLA> nuovo.owner@azienda.it --dry-run

    # trasferimento effettivo
    python transfer_drive_ownership.py <ID_CARTELLA> nuovo.owner@azienda.it

L'ID cartella è la parte finale dell'URL:
    https://drive.google.com/drive/folders/<ID_CARTELLA>

Note:
    - Vengono trasferiti solo gli elementi di cui SEI proprietario
      (quelli di altri utenti vengono saltati e segnalati nel log).
    - Dopo il trasferimento resti come editor su ogni elemento.
    - Non funziona su elementi che stanno in un Drive condiviso
      (lì il proprietario è l'organizzazione).
    - Google invia un'email di notifica al nuovo proprietario per ogni
      elemento: l'API non permette di disattivarla per i cambi di proprietà.
"""

import argparse
import csv
import random
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"
RETRYABLE_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "backendError",
    "internalError",
    "sharingRateLimitExceeded",
}


# --------------------------------------------------------------------------- #
# Autenticazione
# --------------------------------------------------------------------------- #
def get_service(credentials_file: str, token_file: str):
    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_file).write_text(creds.to_json())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


# --------------------------------------------------------------------------- #
# Retry con backoff esponenziale
# --------------------------------------------------------------------------- #
def execute_with_retry(request, max_retries: int = 7):
    for attempt in range(max_retries):
        try:
            return request.execute()
        except HttpError as e:
            status = e.resp.status
            reason = ""
            try:
                reason = e.error_details[0].get("reason", "") if e.error_details else ""
            except Exception:
                pass
            retryable = status in (429, 500, 502, 503, 504) or (
                status == 403 and reason in RETRYABLE_REASONS
            )
            if not retryable or attempt == max_retries - 1:
                raise
            wait = min(2 ** attempt + random.random(), 64)
            print(f"   ...limite/errore temporaneo ({status} {reason}), riprovo tra {wait:.1f}s")
            time.sleep(wait)


# --------------------------------------------------------------------------- #
# Visita ricorsiva della cartella
# --------------------------------------------------------------------------- #
def walk_folder(service, root_id: str):
    """Restituisce (in ordine BFS) tutti gli elementi sotto root_id, root inclusa."""
    root = execute_with_retry(
        service.files().get(
            fileId=root_id,
            fields="id, name, mimeType, ownedByMe, owners(emailAddress), driveId",
            supportsAllDrives=True,
        )
    )
    if root.get("driveId"):
        sys.exit("La cartella si trova in un Drive condiviso: lì non esiste un proprietario da trasferire.")
    if root["mimeType"] != FOLDER_MIME:
        sys.exit(f"L'ID indicato non è una cartella ({root['mimeType']}).")

    root["path"] = root["name"]
    items = [root]
    queue = deque([root])
    seen = {root["id"]}

    while queue:
        folder = queue.popleft()
        page_token = None
        while True:
            resp = execute_with_retry(
                service.files().list(
                    q=f"'{folder['id']}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, ownedByMe, owners(emailAddress))",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
            for f in resp.get("files", []):
                if f["id"] in seen:  # evita cicli / file con più genitori
                    continue
                seen.add(f["id"])
                f["path"] = f"{folder['path']}/{f['name']}"
                items.append(f)
                if f["mimeType"] == FOLDER_MIME:
                    queue.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        print(f"   scansionati {len(items)} elementi...", end="\r")
    print()
    return items


# --------------------------------------------------------------------------- #
# Trasferimento di un singolo elemento
# --------------------------------------------------------------------------- #
def find_user_permission(service, file_id: str, email: str):
    page_token = None
    while True:
        resp = execute_with_retry(
            service.permissions().list(
                fileId=file_id,
                fields="nextPageToken, permissions(id, type, role, emailAddress)",
                pageToken=page_token,
                supportsAllDrives=True,
            )
        )
        for p in resp.get("permissions", []):
            if p.get("type") == "user" and (p.get("emailAddress") or "").lower() == email.lower():
                return p
        page_token = resp.get("nextPageToken")
        if not page_token:
            return None


def transfer_one(service, file_id: str, new_owner: str):
    existing = find_user_permission(service, file_id, new_owner)
    if existing and existing.get("role") == "owner":
        return "già proprietario"
    if existing:
        execute_with_retry(
            service.permissions().update(
                fileId=file_id,
                permissionId=existing["id"],
                body={"role": "owner"},
                transferOwnership=True,
                supportsAllDrives=True,
            )
        )
    else:
        execute_with_retry(
            service.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": "owner", "emailAddress": new_owner},
                transferOwnership=True,
                supportsAllDrives=True,
            )
        )
    return "trasferito"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Trasferimento ricorsivo proprietà Google Drive")
    parser.add_argument("folder_id", help="ID della cartella radice")
    parser.add_argument("new_owner", help="Email del nuovo proprietario (stessa organizzazione)")
    parser.add_argument("--dry-run", action="store_true", help="Mostra cosa farebbe senza modificare nulla")
    parser.add_argument("--credentials", default="credentials.json", help="File client OAuth")
    parser.add_argument("--token", default="token.json", help="File in cui salvare il token")
    parser.add_argument("--log", default=None, help="CSV di log (default: transfer_log_<timestamp>.csv)")
    args = parser.parse_args()

    service = get_service(args.credentials, args.token)
    me = execute_with_retry(service.about().get(fields="user(emailAddress)"))["user"]["emailAddress"]
    print(f"Autenticato come: {me}")
    if me.lower() == args.new_owner.lower():
        sys.exit("Il nuovo proprietario coincide con l'account autenticato.")

    print("Scansione della cartella in corso...")
    items = walk_folder(service, args.folder_id)

    mine = [i for i in items if i.get("ownedByMe")]
    others = [i for i in items if not i.get("ownedByMe")]
    print(f"Elementi totali: {len(items)} | di tua proprietà: {len(mine)} | di altri (saltati): {len(others)}")

    if args.dry_run:
        print("\n[DRY RUN] Verrebbero trasferiti:")
        for i in mine:
            print(f"  {i['path']}")
        if others:
            print("\n[DRY RUN] Saltati perché non tuoi:")
            for i in others:
                owner = ", ".join(o.get("emailAddress", "?") for o in i.get("owners", []))
                print(f"  {i['path']}  (proprietario: {owner})")
        return

    answer = input(f"\nTrasferire {len(mine)} elementi a {args.new_owner}? [s/N] ").strip().lower()
    if answer not in ("s", "si", "sì", "y", "yes"):
        print("Annullato.")
        return

    log_path = args.log or f"transfer_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
    ok = fail = 0
    with open(log_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "path", "mimeType", "esito", "dettaglio"])

        for i in others:
            owner = ", ".join(o.get("emailAddress", "?") for o in i.get("owners", []))
            writer.writerow([i["id"], i["path"], i["mimeType"], "saltato", f"proprietario: {owner}"])

        for n, item in enumerate(mine, 1):
            try:
                result = transfer_one(service, item["id"], args.new_owner)
                ok += 1
                writer.writerow([item["id"], item["path"], item["mimeType"], result, ""])
                print(f"[{n}/{len(mine)}] OK   {item['path']}")
            except HttpError as e:
                fail += 1
                msg = e.reason if hasattr(e, "reason") else str(e)
                writer.writerow([item["id"], item["path"], item["mimeType"], "errore", msg])
                print(f"[{n}/{len(mine)}] ERR  {item['path']} -> {msg}")
            fh.flush()

    print(f"\nFatto. Trasferiti: {ok} | Errori: {fail} | Saltati: {len(others)}")
    print(f"Log: {log_path}")
    if fail:
        print("Puoi rilanciare lo script: gli elementi già trasferiti non risultano più tuoi e vengono saltati.")


if __name__ == "__main__":
    main()
