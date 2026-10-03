"""
Progress and state manager for Telegram automation.
Saves and loads progress to/from progress.json to allow resuming seamlessly.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config_loader import AppConfig, LoopConfig


@dataclass
class LoopState:
    name: str
    base_link: str
    start_id: int
    end_id: int
    last_completed_id: Optional[int] = None
    completed_ids: List[int] = field(default_factory=list)
    is_finished: bool = False


@dataclass
class AutomationState:
    current_loop_index: int = 0
    current_message_id: Optional[int] = None
    last_action: str = "INITIALIZED"
    completed_loops: List[int] = field(default_factory=list)
    loops: Dict[str, LoopState] = field(default_factory=dict)
    updated_at: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )


class StateManager:
    def __init__(self, state_file: str = "progress.json"):
        self.state_file = Path(state_file)
        self.state = AutomationState()

    def load(self, config: AppConfig) -> AutomationState:
        """Loads progress from progress.json or initializes a new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    loops_dict = {}
                    for k, v in data.get("loops", {}).items():
                        loops_dict[k] = LoopState(**v)

                    self.state = AutomationState(
                        current_loop_index=data.get("current_loop_index", 0),
                        current_message_id=data.get("current_message_id"),
                        last_action=data.get("last_action", "INITIALIZED"),
                        completed_loops=data.get("completed_loops", []),
                        loops=loops_dict,
                        updated_at=data.get("updated_at", datetime.now().isoformat()),
                    )
            except Exception as e:
                print(f"[Warning] Failed to read {self.state_file} ({e}). Starting fresh state.")
                self.state = self._init_from_config(config)
        else:
            self.state = self._init_from_config(config)

        # Ensure all loops from current config exist in state
        for idx, loop in enumerate(config.loops):
            key = str(idx)
            if key not in self.state.loops:
                self.state.loops[key] = LoopState(
                    name=loop.name,
                    base_link=loop.base_link,
                    start_id=loop.start_id,
                    end_id=loop.end_id,
                )
        self.save()
        return self.state

    def _init_from_config(self, config: AppConfig) -> AutomationState:
        loops_dict = {}
        for idx, loop in enumerate(config.loops):
            loops_dict[str(idx)] = LoopState(
                name=loop.name,
                base_link=loop.base_link,
                start_id=loop.start_id,
                end_id=loop.end_id,
            )
        return AutomationState(
            current_loop_index=0,
            current_message_id=None,
            last_action="INITIALIZED",
            completed_loops=[],
            loops=loops_dict,
        )

    def save(self) -> None:
        """Atomically saves state to disk."""
        self.state.updated_at = datetime.now().isoformat()
        tmp_file = self.state_file.with_suffix(".tmp")

        data = {
            "current_loop_index": self.state.current_loop_index,
            "current_message_id": self.state.current_message_id,
            "last_action": self.state.last_action,
            "completed_loops": self.state.completed_loops,
            "updated_at": self.state.updated_at,
            "loops": {
                k: asdict(v) for k, v in self.state.loops.items()
            },
        }

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp_file.replace(self.state_file)

    def record_sent(self, loop_idx: int, message_id: int) -> None:
        """Mark that a message link was sent to the bot."""
        self.state.current_loop_index = loop_idx
        self.state.current_message_id = message_id
        self.state.last_action = "SENT"
        self.save()

    def record_video_received(
        self, loop_idx: int, message_id: int, bot_msg_id: Optional[int] = None
    ) -> None:
        """Mark that the video response for message_id was successfully received."""
        key = str(loop_idx)
        if key in self.state.loops:
            loop_st = self.state.loops[key]
            loop_st.last_completed_id = message_id
            if message_id not in loop_st.completed_ids:
                loop_st.completed_ids.append(message_id)
                loop_st.completed_ids.sort()

            if message_id >= loop_st.end_id:
                loop_st.is_finished = True
                if loop_idx not in self.state.completed_loops:
                    self.state.completed_loops.append(loop_idx)

        self.state.last_action = "VIDEO_RECEIVED"
        self.save()

    def get_next_task(self, config: AppConfig) -> Optional[Tuple[int, int]]:
        """
        Determines the next (loop_index, message_id) to process.
        Returns None if all loops are completely finished.
        """
        for loop_idx, loop_cfg in enumerate(config.loops):
            key = str(loop_idx)
            loop_st = self.state.loops.get(key)
            if not loop_st:
                loop_st = LoopState(
                    name=loop_cfg.name,
                    base_link=loop_cfg.base_link,
                    start_id=loop_cfg.start_id,
                    end_id=loop_cfg.end_id,
                )
                self.state.loops[key] = loop_st

            if loop_st.is_finished or loop_idx in self.state.completed_loops:
                continue

            last_id = loop_st.last_completed_id
            if last_id is None:
                next_id = loop_cfg.start_id
            else:
                next_id = last_id + 1

            if next_id > loop_cfg.end_id:
                loop_st.is_finished = True
                if loop_idx not in self.state.completed_loops:
                    self.state.completed_loops.append(loop_idx)
                self.save()
                continue

            return (loop_idx, next_id)

        return None

    def reset(self) -> None:
        """Resets the state file."""
        if self.state_file.exists():
            self.state_file.unlink()
        self.state = AutomationState()
