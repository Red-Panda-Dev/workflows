"""Cloudflare R2 storage upload module for CentralDepo workflow.

Handles PDF file uploads to Cloudflare R2 storage (S3-compatible).
Provides public URLs for use with Mistral OCR API.
Uses aioboto3 for async S3 operations.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import aioboto3

logger = logging.getLogger(__name__)

R2_PUBLIC_URL_BASE = "https://pub-e91e2e22fe0049b5b3763b83b6645829.r2.dev"


async def upload_to_r2(file_path: Path, r2_key: str) -> Optional[str]:
    """Upload file to Cloudflare R2 and return public URL.

    Uses aioboto3 for async S3-compatible upload to Cloudflare R2.

    Args:
        file_path: Local path to the file to upload
        r2_key: Object key/path in R2 bucket (e.g., "temps/payouts/hash/file.pdf")

    Returns:
        Public URL string (e.g., "https://pub-...r2.dev/temps/payouts/..."),
        or None on failure
    """
    try:
        bucket = os.environ.get("AWS_S3_BUCKET_NAME", "tokenbel")

        # Create async session and client
        session = aioboto3.Session(
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_S3_REGION", "auto"),
        )

        async with session.client(
            "s3",
            endpoint_url=os.environ.get("AWS_S3_URL"),
        ) as client:
            # Read file async
            file_content = await asyncio.to_thread(file_path.read_bytes)

            # Upload to R2
            await client.put_object(
                Bucket=bucket,
                Key=r2_key,
                Body=file_content,
                ACL="public-read",
            )

        public_url = f"{R2_PUBLIC_URL_BASE}/{r2_key}"
        logger.info("Uploaded %s to R2: %s", file_path, public_url)
        return public_url
    except Exception as e:
        logger.error("Failed to upload %s to R2: %s", file_path, e)
        return None
