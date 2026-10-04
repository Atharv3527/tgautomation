"""
Configuration loader and validator for Telegram automation.
Loads settings from .env and config.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv


@dataclass
class LoopConfig:
    name: str
    base_link: str
    start_id: int
    end_id: int

    def get_link_for_id(self, message_id: int) -> str:
        """Construct the full message URL for the given message ID."""
        return f"{self.base_link.rstrip('/')}/{message_id}"

    @property
    def total_count(self) -> int:
        return max(0, self.end_id - self.start_id + 1)


@dataclass
class AppConfig:
    api_id: Optional[int]
    api_hash: Optional[str]
    phone: Optional[str]
    session_name: str
    bot_username: str
    timeout_seconds: Optional[float]
    delay_between_links: float
    loops: List[LoopConfig] = field(default_factory=list)


def load_app_config(
    env_file: Optional[str] = ".env",
    config_file: Optional[str] = "config.json",
) -> AppConfig:
    """
    Loads configuration from environment variables and config.json.
    """
    if env_file and os.path.exists(env_file):
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(override=False)

    # Defaults
    bot_username = os.getenv("BOT_USERNAME", "@SaveRestrictedContentfreeBot").strip()
    session_name = os.getenv("TELEGRAM_SESSION", "telegram_automation").strip()
    phone = os.getenv("TELEGRAM_PHONE", "").strip() or None
    timeout_seconds: Optional[float] = 600.0
    delay_between_links: float = 3.0
    raw_loops = []

    # Read config.json if present
    if config_file and os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            cfg_data = json.load(f)
            if "bot_username" in cfg_data and cfg_data["bot_username"]:
                bot_username = cfg_data["bot_username"].strip()
            if "timeout_seconds" in cfg_data:
                timeout_val = cfg_data["timeout_seconds"]
                timeout_seconds = float(timeout_val) if timeout_val is not None and timeout_val > 0 else None
            if "delay_between_links" in cfg_data:
                delay_between_links = max(0.0, float(cfg_data["delay_between_links"]))
            if "loops" in cfg_data and isinstance(cfg_data["loops"], list):
                raw_loops = cfg_data["loops"]

    # Parse API_ID and API_HASH
    api_id_str = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip() or None

    api_id: Optional[int] = None
    if api_id_str and api_id_str.isdigit():
        api_id = int(api_id_str)

    loops: List[LoopConfig] = []
    for idx, item in enumerate(raw_loops):
        name = item.get("name", f"Loop {idx + 1}")
        base_link = item.get("base_link", "").strip()
        start_id = int(item.get("start_id", 0))
        end_id = int(item.get("end_id", 0))

        if not base_link:
            raise ValueError(f"Loop '{name}' (index {idx}) is missing a valid 'base_link'.")
        if start_id <= 0:
            raise ValueError(f"Loop '{name}' has invalid start_id: {start_id}. Must be >= 1.")
        if end_id < start_id:
            raise ValueError(
                f"Loop '{name}' has end_id ({end_id}) smaller than start_id ({start_id})."
            )

        loops.append(
            LoopConfig(
                name=name,
                base_link=base_link,
                start_id=start_id,
                end_id=end_id,
            )
        )

    return AppConfig(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        session_name=session_name,
        bot_username=bot_username,
        timeout_seconds=timeout_seconds,
        delay_between_links=delay_between_links,
        loops=loops,
    )


def validate_credentials(config: AppConfig) -> List[str]:
    """
    Checks if required Telegram credentials are present and valid.
    Returns a list of missing / invalid items (empty list if everything is valid).
    """
    errors = []
    if not config.api_id:
        errors.append(
            "TELEGRAM_API_ID is missing or not a valid number. "
            "Get it from https://my.telegram.org under 'API development tools'."
        )
    if not config.api_hash:
        errors.append(
            "TELEGRAM_API_HASH is missing. "
            "Get it from https://my.telegram.org under 'API development tools'."
        )
    if not config.loops:
        errors.append("No loops configured. Please specify at least one loop.")
    return errors


def prompt_manual_loops() -> List[LoopConfig]:
    """
    Interactively prompts the user in the console to input loops.
    Supports either pasting all 3 values in one line:
       https://t.me/c/3548255677/15/ 25 45
    or entering them step-by-step.
    """
    print("\n" + "=" * 60)
    print("MANUAL LOOP CONFIGURATION")
    print("=" * 60)
    print("Tip: You can paste all 3 values in ONE LINE:")
    print("     https://t.me/c/3548255677/15/ 25 45\n")
    loops: List[LoopConfig] = []
    loop_idx = 1

    while True:
        print(f"\n--- [ Loop {loop_idx} ] ---")
        line = input("Enter base link (or paste 'link start end' in 1 line): ").strip()
        parts = line.split()

        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            base_link = parts[0]
            start_id = int(parts[1])
            end_id = int(parts[2])
            if end_id < start_id:
                print(f"[!] End ID ({end_id}) cannot be smaller than Start ID ({start_id}). Try again.")
                continue
        else:
            base_link = parts[0] if parts else ""
            while not (base_link.startswith("http://") or base_link.startswith("https://")):
                base_link = input("Enter valid base link (https://...): ").strip()

            start_id = None
            while start_id is None:
                start_str = input("Enter start message ID (e.g. 25): ").strip()
                if start_str.isdigit() and int(start_str) > 0:
                    start_id = int(start_str)
                else:
                    print("[!] Start ID must be a positive integer!")

            end_id = None
            while end_id is None:
                end_str = input(f"Enter end message ID (>= {start_id}): ").strip()
                if end_str.isdigit() and int(end_str) >= start_id:
                    end_id = int(end_str)
                else:
                    print(f"[!] End ID must be an integer greater than or equal to {start_id}!")

        loops.append(
            LoopConfig(
                name=f"Loop {loop_idx}",
                base_link=base_link,
                start_id=start_id,
                end_id=end_id,
            )
        )
        print(f"Added Loop {loop_idx}: {base_link.rstrip('/')}/[{start_id}..{end_id}] (Total: {end_id - start_id + 1} links)")

        more = input("\nDo you want to add another loop? (y/N): ").strip().lower()
        if more not in ("y", "yes"):
            break
        loop_idx += 1

    return loops



def save_loops_to_config(loops: List[LoopConfig], config_file: str = "config.json") -> None:
    """Saves configured loops back to config.json."""
    data = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data["loops"] = [
        {
            "name": l.name,
            "base_link": l.base_link,
            "start_id": l.start_id,
            "end_id": l.end_id,
        }
        for l in loops
    ]

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[Config] Saved {len(loops)} loop(s) to '{config_file}'.")

