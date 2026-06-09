from __future__ import annotations
from dataclasses import dataclass
from typing import List

@dataclass
class GameState:
    """Stato base del tavolo poker per PokerGPT"""
    my_stack: float = 0.0
    blinds: tuple[float, float] = (0.5, 1.0)
    position: str = ""
    hole_cards: List[str] = None
    community_cards: List[str] = None
    pot: float = 0.0
    table_name: str = ""

    def __post_init__(self):
        if self.hole_cards is None:
            self.hole_cards = []
        if self.community_cards is None:
            self.community_cards = []

    def update_from_window(self, window_info: dict):
        """Aggiorna lo stato con le info della finestra trovata"""
        self.table_name = window_info.get("title", "PokerStars")
        print(f"[GameState] Aggiornato da finestra: {self.table_name} | Stack: {self.my_stack}")
