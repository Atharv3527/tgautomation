"""
Media detector utility for identifying completed video responses from Telegram bots.
Inspects Telethon Message objects without downloading the actual media payload.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".ts",
    ".3gp",
}

BOT_ERROR_INDICATORS = [
    "error:",
    "failed to",
    "cannot access",
    "invalid link",
    "link is invalid",
    "message not found",
    "not found in channel",
    "channel is private",
    "make sure bot is admin",
    "file too large",
    "restricted content cannot be accessed",
    "timed out",
    "expired",
]


def is_video_message(message) -> bool:
    """
    Returns True if the Telethon Message contains a video or a video document.
    Does NOT download the video file.
    """
    if not message:
        return False

    # Check 1: Telethon's built-in message.video property (DocumentAttributeVideo)
    try:
        if getattr(message, "video", None):
            return True
    except Exception:
        pass

    # Check 2: Inspect message.document for video mime types, attributes, or extensions
    doc = getattr(message, "document", None)
    if doc:
        mime = getattr(doc, "mime_type", "") or ""
        if mime.lower().startswith("video/"):
            return True

        # Check attributes
        for attr in getattr(doc, "attributes", []):
            if attr.__class__.__name__ == "DocumentAttributeVideo":
                return True

        # Check file extension or name
        file_obj = getattr(message, "file", None)
        if file_obj:
            ext = getattr(file_obj, "ext", "") or ""
            if ext.lower() in VIDEO_EXTENSIONS:
                return True
            name = getattr(file_obj, "name", "") or ""
            _, file_ext = os.path.splitext(name)
            if file_ext.lower() in VIDEO_EXTENSIONS:
                return True

    return False


def get_media_summary(message) -> str:
    """
    Extracts a brief human-readable summary of the video media (size, duration, filename)
    without downloading any file content.
    """
    if not message:
        return "No message"

    parts = []
    file_obj = getattr(message, "file", None)

    if file_obj:
        if file_obj.name:
            parts.append(f"Name: {file_obj.name}")
        if file_obj.size:
            size_mb = file_obj.size / (1024 * 1024)
            parts.append(f"Size: {size_mb:.2f} MB")
        if getattr(file_obj, "duration", None) is not None:
            try:
                total_secs = int(round(float(file_obj.duration)))
                hours = total_secs // 3600
                mins = (total_secs % 3600) // 60
                secs = total_secs % 60
                if hours > 0:
                    parts.append(f"Duration: {hours:02d}:{mins:02d}:{secs:02d}")
                else:
                    parts.append(f"Duration: {mins:02d}:{secs:02d}")
            except Exception:
                pass

    # Fallback to document mime
    doc = getattr(message, "document", None)
    if doc and getattr(doc, "mime_type", None):
        parts.append(f"MIME: {doc.mime_type}")

    return " | ".join(parts) if parts else "Video detected"



def detect_bot_error(message) -> Optional[str]:
    """
    Inspects a non-media message to see if the bot returned an explicit error.
    Returns the error string if found, otherwise None.
    """
    if not message or not getattr(message, "text", None):
        return None

    text = message.text.strip()
    text_lower = text.lower()

    for indicator in BOT_ERROR_INDICATORS:
        if indicator in text_lower:
            return text

    return None


LOGIN_INDICATORS = [
    "login required",
    "already in the middle of logging in",
    "must /login to extract",
    "please reply to the prompt or click /cancel_login"
]

def detect_login_requirement(message) -> bool:
    """
    Returns True if the message indicates the bot is blocked by a login state.
    """
    if not message or not getattr(message, "text", None):
        return False

    text_lower = message.text.strip().lower()
    for indicator in LOGIN_INDICATORS:
        if indicator in text_lower:
            return True

    return False
