from __future__ import annotations

from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from rpg_rules_search.drive import FOLDER_MIME_TYPE, DriveItem


def drive_item_from_response(item: dict[str, Any]) -> DriveItem:
    shortcut_details = item.get("shortcutDetails", {})
    return DriveItem(
        id=item["id"],
        name=item["name"],
        mime_type=item["mimeType"],
        modified_time=item.get("modifiedTime"),
        trashed=item.get("trashed", False),
        shortcut_target_id=shortcut_details.get("targetId"),
        shortcut_target_mime_type=shortcut_details.get("targetMimeType"),
    )


class GoogleDriveGateway:
    def __init__(self, service: Any) -> None:
        self._service = service

    def list_children(self, folder_id: str) -> list[DriveItem]:
        items: list[DriveItem] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed = false"

        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields=(
                        "nextPageToken, files("
                        "id,name,mimeType,modifiedTime,trashed,"
                        "shortcutDetails(targetId,targetMimeType))"
                    ),
                    pageToken=page_token,
                    pageSize=1000,
                    orderBy="name_natural",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            items.extend(drive_item_from_response(item) for item in response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return items

    def list_folders(self) -> list[DriveItem]:
        items: list[DriveItem] = []
        page_token: str | None = None
        query = f"mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id,name,mimeType,modifiedTime,trashed)",
                    pageToken=page_token,
                    pageSize=1000,
                    orderBy="name_natural",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            items.extend(
                DriveItem(
                    id=item["id"],
                    name=item["name"],
                    mime_type=item["mimeType"],
                    modified_time=item.get("modifiedTime"),
                    trashed=item.get("trashed", False),
                )
                for item in response.get("files", [])
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return sorted(items, key=lambda item: (item.name.casefold(), item.id))

    def get_item(self, item_id: str) -> DriveItem:
        item = (
            self._service.files()
            .get(
                fileId=item_id,
                fields=(
                    "id,name,mimeType,modifiedTime,trashed,"
                    "shortcutDetails(targetId,targetMimeType)"
                ),
                supportsAllDrives=True,
            )
            .execute()
        )
        return drive_item_from_response(item)

    def download_file(self, file_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(fileId=file_id, supportsAllDrives=True)
        self._download(request, destination)

    def export_file_as_pdf(self, file_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().export_media(fileId=file_id, mimeType="application/pdf")
        self._download(request, destination)

    @staticmethod
    def _download(request: Any, destination: Path) -> None:
        with destination.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()


def create_google_drive_gateway(credentials: Any) -> GoogleDriveGateway:
    return GoogleDriveGateway(build("drive", "v3", credentials=credentials, cache_discovery=False))
