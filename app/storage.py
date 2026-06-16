import os

import boto3
from botocore.config import Config

BUCKET = os.environ.get("CLOUDFLARE_R2_BUCKET_NAME", "")
ENDPOINT = os.environ.get("CLOUDFLARE_R2_ENDPOINT_URL", "")
ACCESS_KEY = os.environ.get("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
SECRET_KEY = os.environ.get("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")

# Maps internal folder keys to R2 folder names
FOLDERS = {
    "profile_pictures": "Profile Pictures",
    "resumes": "Resumes",
    "onboarding": "Onboarding",
    "certificates": "Certificates",
    "ids": "IDs",
}


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def r2_key(folder_key: str, filename: str) -> str:
    return f"{FOLDERS[folder_key]}/{filename}"


def upload_file(data: bytes, folder_key: str, filename: str) -> None:
    """Upload bytes to the R2 bucket under the appropriate folder."""
    _client().put_object(Bucket=BUCKET, Key=r2_key(folder_key, filename), Body=data)


def get_presigned_url(folder_key: str, filename: str, expiry: int = 3600) -> str:
    """Return a temporary presigned GET URL for a private R2 object."""
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": r2_key(folder_key, filename)},
        ExpiresIn=expiry,
    )


def delete_file(folder_key: str, filename: str) -> None:
    """Delete an object from R2. Silently succeeds if the object does not exist."""
    try:
        _client().delete_object(Bucket=BUCKET, Key=r2_key(folder_key, filename))
    except Exception:
        pass
