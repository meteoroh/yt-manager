import asyncio
from typing import Any, Optional
import httpx

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DEFAULT_BULK_CONCURRENCY = 3


class MeTubeClient:
    def __init__(self, base_url: str, max_concurrency: int = DEFAULT_BULK_CONCURRENCY):
        self.base_url = base_url.rstrip("/")
        self.max_concurrency = max(1, max_concurrency)

    async def add_download(
        self,
        url: str,
        quality: str = "best",
        format_type: str = "any",
        folder: str = "",
    ) -> dict[str, Any]:
        """
        Send download request to MeTube's /add endpoint.
        """
        endpoint = f"{self.base_url}/add"
        payload = {
            "url": url,
            "quality": quality,
            "format": format_type,
            "folder": folder,
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()

                # Parse response body
                data = None
                try:
                    data = response.json()
                except Exception:
                    data = response.text

                # Check MeTube's internal status field (e.g. {"status": "error", "msg": "..."})
                if isinstance(data, dict):
                    status = data.get("status")
                    if status and status != "ok":
                        error_msg = data.get("msg") or data.get("error") or f"status: {status}"
                        return {
                            "success": False,
                            "status_code": response.status_code,
                            "error": f"MeTube error: {error_msg}",
                            "data": data,
                        }

                return {
                    "success": True,
                    "status_code": response.status_code,
                    "data": data,
                }
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": "MeTube request timed out waiting for metadata",
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "error": f"MeTube HTTP error {e.response.status_code}: {e.response.text}",
            }
        except httpx.RequestError as e:
            return {
                "success": False,
                "error": f"Failed to connect to MeTube at {self.base_url}: {str(e)}",
            }

    async def add_bulk_downloads(
        self,
        urls: list[str],
        quality: str = "best",
        format_type: str = "any",
        folder: str = "",
    ) -> tuple[int, list[dict[str, Any]]]:
        """
        Send multiple download requests to MeTube with bounded concurrency.
        Returns: (success_count, list_of_results)
        """
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _bounded_add(url: str) -> dict[str, Any]:
            async with semaphore:
                return await self.add_download(
                    url=url,
                    quality=quality,
                    format_type=format_type,
                    folder=folder,
                )

        tasks = [_bounded_add(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        success_count = sum(1 for r in results if r.get("success"))
        return success_count, list(results)
