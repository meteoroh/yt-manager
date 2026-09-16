from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, HttpUrl

from yt_manager.config import Settings, get_settings
from yt_manager.db import Database
from yt_manager.extractor import extract_from_url
from yt_manager.metube import MeTubeClient
from yt_manager.scanner import get_scan_stats, sync_disks_to_db_and_archive

router = APIRouter()


class CheckVideoRequest(BaseModel):
    url: str
    auto_download: bool = False
    quality: str = "best"


class CheckVideoResponse(BaseModel):
    exists: bool
    extractor: Optional[str] = None
    video_id: Optional[str] = None
    folder: Optional[str] = None
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    message: str
    download_triggered: bool = False
    download_result: Optional[dict] = None


class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"
    format_type: str = "any"
    folder: str = ""


class DownloadResponse(BaseModel):
    success: bool
    message: str
    details: Optional[dict] = None


class ScanResponse(BaseModel):
    status: str
    total_files: int
    deleted_files: int
    duration_seconds: float
    last_scanned_at: Optional[str]


class StatusResponse(BaseModel):
    status: str
    total_media_in_db: int
    last_scanned_at: Optional[str]
    last_scan_duration_sec: float
    downloads_dir: str
    media_dir: str
    archive_file: str


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return Database(settings.db_path)


def get_metube(settings: Settings = Depends(get_settings)) -> MeTubeClient:
    return MeTubeClient(settings.metube_url)


@router.post("/check-video", response_model=CheckVideoResponse)
async def check_video(
    req: CheckVideoRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    metube: MeTubeClient = Depends(get_metube),
):
    parsed = extract_from_url(req.url)
    if not parsed:
        return CheckVideoResponse(
            exists=False,
            message="해당 URL에서 비디오 ID를 추출할 수 없습니다.",
        )

    extractor, video_id = parsed
    file_path = db.find_by_id(extractor, video_id)

    # Fallback lookup in case extractor differed slightly in DB
    if not file_path:
        fb = db.find_by_video_id_only(video_id)
        if fb:
            extractor, file_path = fb

    if file_path:
        p = Path(file_path)
        folder_name = p.parent.name
        return CheckVideoResponse(
            exists=True,
            extractor=extractor,
            video_id=video_id,
            folder=folder_name,
            file_name=p.name,
            file_path=file_path,
            message=f"이미 저장된 영상입니다. (위치: {file_path})",
        )

    # Not found
    resp = CheckVideoResponse(
        exists=False,
        extractor=extractor,
        video_id=video_id,
        message="저장되어 있지 않은 영상입니다.",
    )

    if req.auto_download:
        dl_res = await metube.add_download(req.url, quality=req.quality)
        resp.download_triggered = True
        resp.download_result = dl_res

    return resp


@router.post("/download", response_model=DownloadResponse)
async def download_video(
    req: DownloadRequest,
    metube: MeTubeClient = Depends(get_metube),
):
    res = await metube.add_download(
        url=req.url,
        quality=req.quality,
        format_type=req.format_type,
        folder=req.folder,
    )
    if res.get("success"):
        return DownloadResponse(
            success=True,
            message="MeTube에 다운로드 요청을 성공적으로 전송했습니다.",
            details=res,
        )
    return DownloadResponse(
        success=False,
        message=res.get("error", "다운로드 요청 실패"),
        details=res,
    )


@router.post("/scan", response_model=ScanResponse)
def trigger_scan(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    stats = sync_disks_to_db_and_archive(
        directories=[settings.downloads_dir, settings.media_dir],
        archive_file_path=settings.archive_file_path,
        db=db,
        exclude_dirs=settings.parsed_exclude_dirs,
        exclude_patterns=settings.parsed_exclude_patterns,
    )
    return ScanResponse(
        status="ok",
        total_files=stats.total_files,
        deleted_files=stats.deleted_files,
        duration_seconds=stats.duration_seconds,
        last_scanned_at=stats.last_scanned_at,
    )


@router.get("/status", response_model=StatusResponse)
def get_status(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    stats = get_scan_stats()
    return StatusResponse(
        status="ok",
        total_media_in_db=db.count(),
        last_scanned_at=stats.last_scanned_at,
        last_scan_duration_sec=stats.duration_seconds,
        downloads_dir=str(settings.downloads_dir),
        media_dir=str(settings.media_dir),
        archive_file=str(settings.archive_file_path),
    )


@router.get("/health")
def health_check():
    return {"status": "ok"}
