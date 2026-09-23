from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, HttpUrl

from yt_manager.config import Settings, get_settings
from yt_manager.db import Database
from yt_manager.disk import get_configured_disks_usage
from yt_manager.extractor import extract_from_url
from yt_manager.metube import MeTubeClient
from yt_manager.playlist import analyze_playlist, is_playlist_url
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


class CheckRequest(BaseModel):
    url: str
    auto_download: bool = False
    quality: str = "best"
    max_items: Optional[int] = None


class PlaylistItemResponse(BaseModel):
    video_id: str
    title: str
    url: str
    extractor: str
    file_path: Optional[str] = None
    folder: Optional[str] = None
    downloadable: bool = True


class PlaylistCheckDetail(BaseModel):
    playlist_id: str
    title: str
    url: str
    total_count: int
    found_count: int
    missing_count: int
    downloadable_count: int = 0
    found_items: list[PlaylistItemResponse] = []
    missing_items: list[PlaylistItemResponse] = []
    download_triggered: bool = False
    download_result: Optional[dict] = None


class CheckResponse(BaseModel):
    type: str  # "video", "playlist", "invalid"
    video: Optional[CheckVideoResponse] = None
    playlist: Optional[PlaylistCheckDetail] = None
    message: str


class PlaylistCheckRequest(BaseModel):
    url: str
    auto_download_missing: bool = False
    quality: str = "best"
    max_items: Optional[int] = None


class DownloadRequest(BaseModel):
    url: Optional[str] = None
    urls: Optional[list[str]] = None
    quality: str = "best"
    format_type: str = "any"
    folder: str = ""
    max_items: Optional[int] = None


class DownloadResponse(BaseModel):
    success: bool
    queued_count: int = 1
    skipped_count: int = 0
    message: str
    details: Optional[dict] = None


class ScanResponse(BaseModel):
    status: str
    total_files: int
    added_files: int = 0
    deleted_files: int = 0
    duration_seconds: float
    last_scanned_at: Optional[str]
    has_changes: bool = False


class DiskUsageResponse(BaseModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    total_human: str
    used_human: str
    free_human: str
    percent_used: float


class StatusResponse(BaseModel):
    status: str
    total_media_in_db: int
    last_scanned_at: Optional[str]
    last_scan_duration_sec: float
    downloads_dir: str
    media_dir: str
    archive_file: str
    disks: list[DiskUsageResponse] = []


class HistoryItem(BaseModel):
    id: int
    created_at: str
    source: str
    url: str
    extractor: Optional[str] = None
    video_id: Optional[str] = None
    status: str
    detail: Optional[str] = None


class HistoryResponse(BaseModel):
    total_returned: int
    history: list[HistoryItem]


def get_db(settings: Settings = Depends(get_settings)) -> Database:
    return Database(settings.db_path)


def get_metube(settings: Settings = Depends(get_settings)) -> MeTubeClient:
    return MeTubeClient(settings.metube_url)


async def check_single_video_internal(
    url: str,
    db: Database,
    metube: Optional[MeTubeClient] = None,
    auto_download: bool = False,
    quality: str = "best",
) -> CheckVideoResponse:
    parsed = extract_from_url(url)
    if not parsed:
        db.record_request(
            source="api",
            url=url,
            status="INVALID",
            detail="Could not extract video ID from the provided URL.",
        )
        return CheckVideoResponse(
            exists=False,
            message="Could not extract video ID from the provided URL.",
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
        folder_path = str(p.parent)
        db.record_request(
            source="api",
            url=url,
            extractor=extractor,
            video_id=video_id,
            status="EXISTS",
            detail=file_path,
        )
        return CheckVideoResponse(
            exists=True,
            extractor=extractor,
            video_id=video_id,
            folder=folder_path,
            file_name=p.name,
            file_path=file_path,
            message="Video already exists.",
        )

    # Not found
    status = "MISSING"
    detail = None
    dl_res = None
    if auto_download and metube:
        dl_res = await metube.add_download(url, quality=quality)
        if dl_res.get("success"):
            status = "QUEUED"
        else:
            status = "FAILED"
            detail = dl_res.get("error")

    db.record_request(
        source="api",
        url=url,
        extractor=extractor,
        video_id=video_id,
        status=status,
        detail=detail,
    )

    resp = CheckVideoResponse(
        exists=False,
        extractor=extractor,
        video_id=video_id,
        message="Video not found.",
    )

    if auto_download:
        resp.download_triggered = True
        resp.download_result = dl_res

    return resp


def format_playlist_message(pl_data: PlaylistCheckDetail) -> str:
    unavail_count = pl_data.missing_count - pl_data.downloadable_count
    if pl_data.downloadable_count == 0:
        if unavail_count > 0:
            return f"Playlist: All downloadable videos are already saved!\n({unavail_count} unavailable/private)"
        return f"Playlist: All {pl_data.total_count} video(s) already exist in storage."
    else:
        unavail_str = f", {unavail_count} unavailable" if unavail_count > 0 else ""
        return f"Playlist: {pl_data.downloadable_count} downloadable out of {pl_data.missing_count} missing\n({pl_data.found_count} already saved{unavail_str})"


async def check_playlist_internal(
    url: str,
    db: Database,
    metube: Optional[MeTubeClient] = None,
    auto_download_missing: bool = False,
    quality: str = "best",
    max_items: int = 200,
) -> Optional[PlaylistCheckDetail]:
    analysis = analyze_playlist(url, db, max_items=max_items)
    if not analysis:
        return None

    found_responses = [
        PlaylistItemResponse(
            video_id=it.video_id,
            title=it.title,
            url=it.url,
            extractor=it.extractor,
            file_path=it.file_path,
            folder=it.folder,
            downloadable=it.downloadable,
        )
        for it in analysis.found_items
    ]
    missing_responses = [
        PlaylistItemResponse(
            video_id=it.video_id,
            title=it.title,
            url=it.url,
            extractor=it.extractor,
            downloadable=it.downloadable,
        )
        for it in analysis.missing_items
    ]

    dl_triggered = False
    dl_result = None

    # 1. Record playlist inspection summary (CHECKED)
    db.record_request(
        source="api",
        url=url,
        status="CHECKED",
        detail=f"Playlist: {analysis.found_count} found, {analysis.missing_count} missing ({analysis.downloadable_count} downloadable) out of {analysis.total_count}",
    )

    # 2. If auto-download triggered, record each queued video individually
    downloadable_missing = [it for it in analysis.missing_items if it.downloadable]
    if auto_download_missing and metube and downloadable_missing:
        urls_to_download = [it.url for it in downloadable_missing]
        success_count, results = await metube.add_bulk_downloads(
            urls=urls_to_download,
            quality=quality,
        )
        dl_triggered = True
        dl_result = {
            "queued_count": success_count,
            "failed_count": len(urls_to_download) - success_count,
            "results": results,
        }
        for it, r in zip(downloadable_missing, results):
            detail = f"Playlist: {analysis.playlist_id}" if r.get("success") else str(r.get("error", "Unknown error"))
            db.record_request(
                source="api",
                url=it.url,
                extractor=it.extractor,
                video_id=it.video_id,
                status="QUEUED" if r.get("success") else "FAILED",
                detail=detail,
            )

    return PlaylistCheckDetail(
        playlist_id=analysis.playlist_id,
        title=analysis.title,
        url=analysis.url,
        total_count=analysis.total_count,
        found_count=analysis.found_count,
        missing_count=analysis.missing_count,
        downloadable_count=analysis.downloadable_count,
        found_items=found_responses,
        missing_items=missing_responses,
        download_triggered=dl_triggered,
        download_result=dl_result,
    )


@router.post("/check", response_model=CheckResponse)
async def check_url(
    req: CheckRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    metube: MeTubeClient = Depends(get_metube),
):
    """
    Unified smart check endpoint: automatically detects whether the URL is a single video
    or a playlist, checks DB presence, and returns a structured response.
    """
    clean_url = req.url.strip()
    effective_max = req.max_items or settings.playlist_max_items

    # 1. First check if it's explicitly a playlist URL
    if is_playlist_url(clean_url):
        pl_data = await check_playlist_internal(
            clean_url,
            db=db,
            metube=metube,
            auto_download_missing=req.auto_download,
            quality=req.quality,
            max_items=effective_max,
        )
        if pl_data:
            return CheckResponse(
                type="playlist",
                playlist=pl_data,
                message=format_playlist_message(pl_data),
            )

    # 2. Check as single video
    parsed = extract_from_url(clean_url)
    if parsed:
        v_data = await check_single_video_internal(
            clean_url,
            db=db,
            metube=metube,
            auto_download=req.auto_download,
            quality=req.quality,
        )
        return CheckResponse(
            type="video",
            video=v_data,
            message=v_data.message,
        )

    # 3. Fallback: Check if it can be parsed as playlist via yt-dlp
    pl_data = await check_playlist_internal(
        clean_url,
        db=db,
        metube=metube,
        auto_download_missing=req.auto_download,
        quality=req.quality,
        max_items=effective_max,
    )
    if pl_data:
        return CheckResponse(
            type="playlist",
            playlist=pl_data,
            message=format_playlist_message(pl_data),
        )

    # 4. Invalid
    db.record_request(
        source="api",
        url=clean_url,
        status="INVALID",
        detail="Could not extract video or playlist from the provided URL.",
    )
    return CheckResponse(
        type="invalid",
        message="Could not extract video or playlist from the provided URL.",
    )


@router.post("/check-playlist", response_model=PlaylistCheckDetail)
async def check_playlist(
    req: PlaylistCheckRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    metube: MeTubeClient = Depends(get_metube),
):
    """
    Dedicated playlist check endpoint.
    """
    effective_max = req.max_items or settings.playlist_max_items
    pl_data = await check_playlist_internal(
        req.url.strip(),
        db=db,
        metube=metube,
        auto_download_missing=req.auto_download_missing,
        quality=req.quality,
        max_items=effective_max,
    )
    if not pl_data:
        raise HTTPException(
            status_code=400,
            detail="URL is not a valid playlist or could not be parsed.",
        )
    return pl_data


@router.post("/check-video", response_model=CheckVideoResponse)
async def check_video(
    req: CheckVideoRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    metube: MeTubeClient = Depends(get_metube),
):
    """
    Single video check endpoint (preserves backwards compatibility).
    """
    clean_url = req.url.strip()
    if is_playlist_url(clean_url):
        db.record_request(
            source="api",
            url=clean_url,
            status="INVALID",
            detail="URL is a playlist, not a single video.",
        )
        return CheckVideoResponse(
            exists=False,
            message="This URL is a playlist or feed. Please use /check or /check-playlist.",
        )

    return await check_single_video_internal(
        clean_url,
        db=db,
        metube=metube,
        auto_download=req.auto_download,
        quality=req.quality,
    )


@router.post("/download", response_model=DownloadResponse)
async def download_video(
    req: DownloadRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    metube: MeTubeClient = Depends(get_metube),
):
    """
    Download endpoint supporting:
      1. Single video URL (dispatches 1 download to MeTube)
      2. Playlist URL (analyzes playlist, filters out existing, queues missing videos)
      3. List of URLs (queues all in parallel)
    """
    # Case A: Explicit list of URLs
    if req.urls:
        success_count, results = await metube.add_bulk_downloads(
            urls=req.urls,
            quality=req.quality,
            format_type=req.format_type,
            folder=req.folder,
        )
        for u, r in zip(req.urls, results):
            parsed = extract_from_url(u)
            ext, vid = parsed if parsed else (None, None)
            db.record_request(
                source="api",
                url=u,
                extractor=ext,
                video_id=vid,
                status="QUEUED" if r.get("success") else "FAILED",
                detail=None if r.get("success") else str(r.get("error", "Unknown error")),
            )
        return DownloadResponse(
            success=(success_count > 0),
            queued_count=success_count,
            skipped_count=len(req.urls) - success_count,
            message=f"Queued {success_count} of {len(req.urls)} video(s) to MeTube.",
            details={"results": results},
        )

    # Case B: Single URL provided
    if not req.url:
        raise HTTPException(
            status_code=422,
            detail="Either 'url' or 'urls' must be provided.",
        )

    clean_url = req.url.strip()

    # B-1: Playlist URL -> Filter existing, queue only downloadable missing
    if is_playlist_url(clean_url):
        effective_max = req.max_items or settings.playlist_max_items
        analysis = analyze_playlist(clean_url, db, max_items=effective_max)
        if not analysis:
            return DownloadResponse(
                success=False,
                queued_count=0,
                skipped_count=0,
                message="Failed to parse playlist or playlist is empty.",
            )

        downloadable_missing = [it for it in analysis.missing_items if it.downloadable]
        unavail_count = analysis.missing_count - analysis.downloadable_count

        if not downloadable_missing:
            db.record_request(
                source="api",
                url=clean_url,
                status="EXISTS",
                detail=f"Playlist: {analysis.found_count} saved, {unavail_count} unavailable, 0 downloadable",
            )
            return DownloadResponse(
                success=True,
                queued_count=0,
                skipped_count=analysis.total_count,
                message=f"No downloadable videos to queue ({analysis.found_count} saved, {unavail_count} unavailable/private). Skipped.",
            )

        urls_to_download = [it.url for it in downloadable_missing]
        success_count, results = await metube.add_bulk_downloads(
            urls=urls_to_download,
            quality=req.quality,
            format_type=req.format_type,
            folder=req.folder,
        )
        for it, r in zip(downloadable_missing, results):
            detail = f"Playlist: {analysis.playlist_id}" if r.get("success") else str(r.get("error", "Unknown error"))
            db.record_request(
                source="api",
                url=it.url,
                extractor=it.extractor,
                video_id=it.video_id,
                status="QUEUED" if r.get("success") else "FAILED",
                detail=detail,
            )
        skipped_total = analysis.found_count + unavail_count
        return DownloadResponse(
            success=(success_count > 0),
            queued_count=success_count,
            skipped_count=skipped_total,
            message=f"Successfully queued {success_count} downloadable video(s) to MeTube ({skipped_total} skipped: {analysis.found_count} saved, {unavail_count} unavailable).",
            details={"results": results},
        )

    # B-2: Single video
    parsed = extract_from_url(clean_url)
    extractor, video_id = parsed if parsed else (None, None)

    res = await metube.add_download(
        url=clean_url,
        quality=req.quality,
        format_type=req.format_type,
        folder=req.folder,
    )
    if res.get("success"):
        db.record_request(
            source="api",
            url=clean_url,
            extractor=extractor,
            video_id=video_id,
            status="QUEUED",
        )
        return DownloadResponse(
            success=True,
            queued_count=1,
            skipped_count=0,
            message="Download request successfully sent to MeTube.",
            details=res,
        )

    err_msg = res.get("error", "Failed to request download")
    db.record_request(
        source="api",
        url=clean_url,
        extractor=extractor,
        video_id=video_id,
        status="FAILED",
        detail=err_msg,
    )
    return DownloadResponse(
        success=False,
        queued_count=0,
        skipped_count=0,
        message=err_msg,
        details=res,
    )


@router.get("/history", response_model=HistoryResponse)
def get_request_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: Optional[str] = Query(None, description="Filter by 'api' or 'telegram'"),
    status: Optional[str] = Query(None, description="Filter by 'EXISTS', 'MISSING', 'QUEUED', 'FAILED', 'INVALID'"),
    video_id: Optional[str] = Query(None, description="Filter by video ID"),
    extractor: Optional[str] = Query(None, description="Filter by platform name (e.g. youtube)"),
    url: Optional[str] = Query(None, description="Filter by exact video URL"),
    url_contains: Optional[str] = Query(None, description="Filter by partial URL or playlist ID"),
    db: Database = Depends(get_db),
):
    rows = db.get_history(
        limit=limit,
        offset=offset,
        source=source,
        status=status,
        video_id=video_id,
        extractor=extractor,
        url=url,
        url_contains=url_contains,
    )
    return HistoryResponse(
        total_returned=len(rows),
        history=rows,
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
        added_files=stats.added_files,
        deleted_files=stats.deleted_files,
        duration_seconds=stats.duration_seconds,
        last_scanned_at=stats.last_scanned_at,
        has_changes=stats.has_changes,
    )


@router.get("/status", response_model=StatusResponse)
def get_status(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
):
    stats = get_scan_stats()
    disks_info = get_configured_disks_usage(
        media_dir=settings.media_dir,
        downloads_dir=settings.downloads_dir,
    )
    return StatusResponse(
        status="ok",
        total_media_in_db=db.count(),
        last_scanned_at=stats.last_scanned_at,
        last_scan_duration_sec=stats.duration_seconds,
        downloads_dir=str(settings.downloads_dir),
        media_dir=str(settings.media_dir),
        archive_file=str(settings.archive_file_path),
        disks=[
            DiskUsageResponse(
                path=d.path,
                total_bytes=d.total_bytes,
                used_bytes=d.used_bytes,
                free_bytes=d.free_bytes,
                total_human=d.total_human,
                used_human=d.used_human,
                free_human=d.free_human,
                percent_used=d.percent_used,
            )
            for d in disks_info
        ],
    )


@router.get("/disk", response_model=list[DiskUsageResponse])
def get_disk_status(
    settings: Settings = Depends(get_settings),
):
    disks_info = get_configured_disks_usage(
        media_dir=settings.media_dir,
        downloads_dir=settings.downloads_dir,
    )
    return [
        DiskUsageResponse(
            path=d.path,
            total_bytes=d.total_bytes,
            used_bytes=d.used_bytes,
            free_bytes=d.free_bytes,
            total_human=d.total_human,
            used_human=d.used_human,
            free_human=d.free_human,
            percent_used=d.percent_used,
        )
        for d in disks_info
    ]


@router.get("/health")
def health_check():
    return {"status": "ok"}
