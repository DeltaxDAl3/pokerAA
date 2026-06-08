from __future__ import annotations
import contextlib
import io
import os
import time
import warnings
from collections import Counter
from dataclasses import dataclass, field
from window_manager import (
    click_bet,
    click_call,
    click_check,
    click_fold,
    click_raise,
    find_and_activate_poker_windows,
    hero_action_buttons_ready,
    initialize_easyocr,
    read_table_cards_ocr,
    take_table_screenshot,
)

RANK_MAP = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
    "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
_EQUITY_CACHE_MAX_SIZE = 2400
_EQUITY_CACHE: dict[tuple[tuple[str, ...], tuple[str, ...]], float] = {}
FOLD_EQUITY_THRESHOLD = 0.28
AGGRESSIVE_EQUITY_THRESHOLD = 0.48
POSTFLOP_RAISE_THRESHOLD = 0.52
OCR_UNCERTAIN_CALL_MAX_BB = 1.2
BOARD_STAGE_LENGTHS = {0, 3, 4, 5}
BOARD_RESET_CONFIRMATION_CYCLES = 2
HOLE_SWITCH_CONFIRMATION_CYCLES = 2


@dataclass
class Decision:
    action: str
    amount_bb: float
    reason: str
    equity: float = 0.0
    spr: float = 0.0
    pot_odds: float = 0.0
    position: str = ""


@dataclass
class TableConsistencyState:
    stable_hole_cards: list[str] = field(default_factory=list)
    stable_board_cards: list[str] = field(default_factory=list)
    pending_hole_cards: list[str] = field(default_factory=list)
    pending_hole_switch_count: int = 0
    pending_empty_board_count: int = 0


def _card_rank(card: str) -> int:
    if not card:
        return 0
    return RANK_MAP.get(card[0].upper(), 0)


def _card_suit(card: str) -> str:
    if len(card) < 2:
        return ""
    return card[1].lower()


def _is_valid_card_token(card: str) -> bool:
    if not card or len(card) != 2:
        return False
    rank = card[0].upper()
    suit = card[1].lower()
    return rank in {"A", "K", "Q", "J", "T", "9", "8", "7", "6", "5", "4", "3", "2"} and suit in {"s", "h", "d", "c"}


def _sanitize_ocr_cards(hole_cards: list[str], board_cards: list[str]) -> tuple[list[str], list[str], bool]:
    cleaned_hole: list[str] = []
    cleaned_board: list[str] = []
    seen_cards: set[str] = set()
    had_ocr_conflict = False

    for card in hole_cards[:2]:
        token = (card or "").strip()
        if not _is_valid_card_token(token):
            if token:
                had_ocr_conflict = True
            continue
        if token in seen_cards:
            had_ocr_conflict = True
            continue
        seen_cards.add(token)
        cleaned_hole.append(token)

    for card in board_cards[:5]:
        token = (card or "").strip()
        if not _is_valid_card_token(token):
            if token:
                had_ocr_conflict = True
            continue
        if token in seen_cards:
            had_ocr_conflict = True
            continue
        seen_cards.add(token)
        cleaned_board.append(token)

    return cleaned_hole, cleaned_board, had_ocr_conflict


def _filter_board_with_consistency(state: TableConsistencyState, raw_board_cards: list[str]) -> tuple[list[str], bool, bool]:
    board_cards = list(raw_board_cards[:5])
    board_len = len(board_cards)
    board_reset_confirmed = False
    board_conflict = False

    if board_len not in BOARD_STAGE_LENGTHS:
        return list(state.stable_board_cards), False, True

    if board_len == 0:
        if state.stable_board_cards:
            state.pending_empty_board_count += 1
            if state.pending_empty_board_count >= BOARD_RESET_CONFIRMATION_CYCLES:
                state.stable_board_cards = []
                state.pending_empty_board_count = 0
                board_reset_confirmed = True
        else:
            state.pending_empty_board_count = 0
        return list(state.stable_board_cards), board_reset_confirmed, False

    state.pending_empty_board_count = 0
    previous_board = list(state.stable_board_cards)

    if not previous_board:
        # Hardening: una nuova board può iniziare solo da flop (3 carte).
        if board_len == 3:
            state.stable_board_cards = list(board_cards)
        else:
            board_conflict = True
        return list(state.stable_board_cards), False, board_conflict

    prev_len = len(previous_board)
    if board_len == prev_len:
        # Hardening: stessa street -> board immutabile.
        if board_cards == previous_board:
            state.stable_board_cards = list(board_cards)
        else:
            board_conflict = True
    elif board_len > prev_len:
        # Progressione stretta flop->turn->river.
        if board_len == prev_len + 1 and board_len in (4, 5) and board_cards[:prev_len] == previous_board:
            state.stable_board_cards = list(board_cards)
        else:
            board_conflict = True
    else:
        # Non permettiamo rollback di street senza reset confermato a board vuota.
        board_conflict = True

    return list(state.stable_board_cards), board_reset_confirmed, board_conflict


def _filter_hole_with_persistence(
    state: TableConsistencyState,
    raw_hole_cards: list[str],
    board_cards: list[str],
    board_reset_confirmed: bool,
) -> tuple[list[str], bool]:
    hole_cards = list(raw_hole_cards[:2])
    board_len = len(board_cards)
    hole_conflict = False

    if board_reset_confirmed:
        if len(hole_cards) == 2:
            state.stable_hole_cards = list(hole_cards)
        else:
            state.stable_hole_cards = []
        state.pending_hole_cards = []
        state.pending_hole_switch_count = 0
        return list(state.stable_hole_cards), False

    # Durante una mano post-flop blocchiamo le hole cards (persistenza hard).
    if board_len in (3, 4, 5):
        state.pending_hole_cards = []
        state.pending_hole_switch_count = 0
        if not state.stable_hole_cards and len(hole_cards) == 2:
            state.stable_hole_cards = list(hole_cards)
        elif len(hole_cards) == 2 and hole_cards != state.stable_hole_cards:
            hole_conflict = True
        return list(state.stable_hole_cards), hole_conflict

    if not state.stable_hole_cards:
        if len(hole_cards) == 2:
            state.stable_hole_cards = list(hole_cards)
        return list(state.stable_hole_cards), False

    if len(hole_cards) == 2:
        if hole_cards == state.stable_hole_cards:
            state.pending_hole_cards = []
            state.pending_hole_switch_count = 0
            return list(state.stable_hole_cards), False

        # Preflop: switch hole solo con conferma multi-ciclo.
        if hole_cards == state.pending_hole_cards:
            state.pending_hole_switch_count += 1
        else:
            state.pending_hole_cards = list(hole_cards)
            state.pending_hole_switch_count = 1

        if state.pending_hole_switch_count >= HOLE_SWITCH_CONFIRMATION_CYCLES:
            state.stable_hole_cards = list(hole_cards)
            state.pending_hole_cards = []
            state.pending_hole_switch_count = 0
            return list(state.stable_hole_cards), False

        return list(state.stable_hole_cards), True

    if hole_cards:
        hole_conflict = True
    return list(state.stable_hole_cards), hole_conflict


def _apply_consistency_filters(
    state: TableConsistencyState,
    hole_cards: list[str],
    board_cards: list[str],
) -> tuple[list[str], list[str], bool]:
    clean_hole, clean_board, had_ocr_conflict = _sanitize_ocr_cards(hole_cards, board_cards)
    stable_board, board_reset_confirmed, board_conflict = _filter_board_with_consistency(state, clean_board)
    stable_hole, hole_conflict = _filter_hole_with_persistence(state, clean_hole, stable_board, board_reset_confirmed)
    residual_conflict = had_ocr_conflict or board_conflict or hole_conflict or (len(clean_board) not in BOARD_STAGE_LENGTHS)
    return stable_hole, stable_board, residual_conflict


def _has_straight(values: list[int]) -> bool:
    uniq = sorted(set(v for v in values if v > 0))
    if not uniq:
        return False
    if 14 in uniq:
        uniq = [1] + uniq
    run = 1
    for i in range(1, len(uniq)):
        if uniq[i] == uniq[i - 1] + 1:
            run += 1
            if run >= 5:
                return True
        else:
            run = 1
    return False


def _straight_draw(values: list[int]) -> bool:
    uniq = sorted(set(v for v in values if v > 0))
    if 14 in uniq:
        uniq = [1] + uniq
    for i in range(len(uniq) - 3):
        window = uniq[i:i + 4]
        if window[-1] - window[0] <= 4:
            return True
    return False



def _hole_preflop_score(hole_cards: list[str]) -> float:
    if len(hole_cards) != 2:
        return 0.0
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    s1, s2 = _card_suit(hole_cards[0]), _card_suit(hole_cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    pair = r1 == r2
    suited = s1 == s2
    connected = abs(r1 - r2) <= 1

    score = (hi + lo) / 28.0
    if pair:
        score += 0.40 + (hi / 20.0)
    if suited:
        score += 0.08
    if connected:
        score += 0.06
    if hi >= 13 and lo >= 10:
        score += 0.08
    return min(1.0, round(score, 3))


def _estimate_equity(hole_cards: list[str], board_cards: list[str], hand_info: dict | None = None) -> float:
    """
    Equity euristica veloce (no Monte Carlo per ciclo).
    """
    if len(hole_cards) != 2:
        return 0.0
    cache_key = (tuple(hole_cards[:2]), tuple(board_cards[:5]))
    cached = _EQUITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if len(board_cards) == 0:
        preflop_score = _hole_preflop_score(hole_cards)
        equity = round(min(0.86, max(0.30, 0.28 + preflop_score * 0.58)), 3)
        if len(_EQUITY_CACHE) >= _EQUITY_CACHE_MAX_SIZE:
            _EQUITY_CACHE.pop(next(iter(_EQUITY_CACHE)))
        _EQUITY_CACHE[cache_key] = equity
        return equity

    info = hand_info or evaluate_hand(hole_cards, board_cards)
    tier = info["tier"]
    board_len = len(board_cards)
    tier_equity = {
        9: 0.98,  # straight flush
        8: 0.96,  # quads
        7: 0.91,  # full house
        6: 0.83,  # flush
        5: 0.79,  # straight
        4: 0.72,  # set
        3: 0.63,  # two pair
        2: 0.49,  # pair
        1: 0.33,  # high card
    }
    equity = tier_equity.get(tier, 0.35)

    if tier == 2 and info["top_pair"]:
        equity += 0.08
    if info["flush_draw"]:
        equity += 0.10 if board_len <= 4 else 0.02
    if info["straight_draw"]:
        equity += 0.08 if board_len <= 4 else 0.02
    if info["draw_outs"] >= 12:
        equity += 0.06
    if board_len >= 4 and tier <= 2:
        equity -= 0.04
    equity = round(min(0.98, max(0.12, equity)), 3)
    if len(_EQUITY_CACHE) >= _EQUITY_CACHE_MAX_SIZE:
        _EQUITY_CACHE.pop(next(iter(_EQUITY_CACHE)))
    _EQUITY_CACHE[cache_key] = equity
    return equity


def evaluate_hand(hole_cards: list[str], board_cards: list[str]) -> dict:
    all_cards = hole_cards + board_cards
    ranks = [_card_rank(c) for c in all_cards]
    suits = [_card_suit(c) for c in all_cards if _card_suit(c)]

    rank_counts = Counter(ranks)
    suit_counts = Counter(suits)
    counts = sorted(rank_counts.values(), reverse=True)
    pairs = sum(1 for v in rank_counts.values() if v == 2)
    has_three = 3 in counts
    has_four = 4 in counts
    has_flush = any(v >= 5 for v in suit_counts.values())
    flush_draw = any(v == 4 for v in suit_counts.values())
    flush_draw_suit = next((s for s, n in suit_counts.items() if n == 4), "")
    has_straight = _has_straight(ranks)
    straight_draw = _straight_draw(ranks) and not has_straight

    if has_straight and has_flush:
        hand_type = "straight flush"
        tier = 9
    elif has_four:
        hand_type = "quads"
        tier = 8
    elif has_three and pairs >= 1:
        hand_type = "full house"
        tier = 7
    elif has_flush:
        hand_type = "flush"
        tier = 6
    elif has_straight:
        hand_type = "straight"
        tier = 5
    elif has_three:
        hand_type = "set"
        tier = 4
    elif pairs >= 2:
        hand_type = "two pair"
        tier = 3
    elif pairs == 1:
        hand_type = "pair"
        tier = 2
    else:
        hand_type = "high card"
        tier = 1

    board_ranks = [_card_rank(c) for c in board_cards]
    hole_ranks = [_card_rank(c) for c in hole_cards]
    top_pair = False
    if hand_type == "pair" and board_ranks and hole_ranks:
        top_board_rank = max(board_ranks)
        top_pair = top_board_rank in hole_ranks and rank_counts[top_board_rank] >= 2
    draw_outs = 0
    if flush_draw:
        draw_outs += 9
    if straight_draw:
        draw_outs += 8
    draw_outs = min(draw_outs, 15)

    return {
        "type": hand_type,
        "tier": tier,
        "flush_draw": flush_draw,
        "flush_draw_suit": flush_draw_suit,
        "straight_draw": straight_draw,
        "top_pair": top_pair,
        "draw_outs": draw_outs,
    }

def _compute_pot_odds(pot_bb: float, to_call_bb: float) -> float:
    if to_call_bb <= 0:
        return 0.0
    total = max(0.1, pot_bb + to_call_bb)
    return round(to_call_bb / total, 3)


def _is_premium_pair(hole_cards: list[str]) -> bool:
    if len(hole_cards) != 2:
        return False
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    return r1 == r2 and r1 >= 12  # QQ+


def _is_green_hand(hole_cards: list[str]) -> bool:
    """
    Mani verdi da giocare in modo aggressivo:
    AK, AQ, AJ, KQ, broadway, coppie 55+, suited connectors.
    """
    if len(hole_cards) != 2:
        return False
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    s1, s2 = _card_suit(hole_cards[0]), _card_suit(hole_cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = s1 == s2

    named_green_combo = (hi, lo) in ((14, 13), (14, 12), (14, 11), (13, 12))  # AK, AQ, AJ, KQ
    pair_55_plus = r1 == r2 and r1 >= 5
    suited_connector = suited and abs(r1 - r2) == 1 and hi >= 6
    broadway = hi >= 10 and lo >= 10
    return named_green_combo or pair_55_plus or suited_connector or broadway

def _is_aggressive_preflop_combo(hole_cards: list[str]) -> bool:
    return _is_green_hand(hole_cards)


def _good_kicker(hole_cards: list[str]) -> bool:
    return max((_card_rank(c) for c in hole_cards), default=0) >= 11

def _is_big_ace_combo(hole_cards: list[str]) -> bool:
    if len(hole_cards) != 2:
        return False
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    return (hi, lo) in ((14, 13), (14, 12))  # AK or AQ

def _draw_hit_probability(draw_outs: int, cards_to_come: int) -> float:
    outs = max(0, min(int(draw_outs), 15))
    if outs == 0:
        return 0.0
    unseen = 47.0
    if cards_to_come >= 2:
        miss_turn = (unseen - outs) / unseen
        miss_river = (unseen - 1.0 - outs) / (unseen - 1.0)
        return round(max(0.0, 1.0 - (miss_turn * miss_river)), 3)
    return round(max(0.0, min(1.0, outs / unseen)), 3)


def _should_open_wide_preflop(hole_cards: list[str], position: str) -> bool:
    """
    Range ampio preflop con focus BTN/CO >=40%.
    """
    if len(hole_cards) != 2:
        return False
    score = _hole_preflop_score(hole_cards)
    thresholds = {
        "BTN": 0.40,
        "CO": 0.42,
        "HJ": 0.47,
        "UTG": 0.52,
        "SB": 0.45,
        "BB": 0.50,
    }
    return score >= thresholds.get(position, 0.47)


def _solver_raise_size(
    pot_bb: float,
    effective_stack_bb: float,
    spr: float,
    position: str,
    profile: str,
    to_call_bb: float = 0.0,
    preflop: bool = False,
) -> float:
    late_bonus = 0.08 if position in ("BTN", "CO") else 0.0
    if preflop:
        open_multiplier = 2.5 if position in ("BTN", "CO", "HJ") else 3.0
        if effective_stack_bb <= 16.0 and (to_call_bb >= 1.2 or pot_bb >= 8.0):
            return round(min(effective_stack_bb, max(4.0, pot_bb * 1.05)), 1)
        if to_call_bb > 0:
            # Reraise standard ~3x da OOP e 2.5x in late position
            re_raise_multiplier = 2.5 if position in ("BTN", "CO") else 3.0
            target = max(4.0, to_call_bb * re_raise_multiplier)
            if pot_bb >= 10.0:
                target = max(target, pot_bb * 0.85)
        else:
            target = max(2.5, open_multiplier)
    else:
        factors = {
            "nuts": 1.00,   # pot bet
            "value": 0.74,
            "thin": 0.52,
            "merged": 0.86,
            "semi": 0.92,
            "probe": 0.62,
        }
        base_factor = factors.get(profile, 0.90)
        if spr <= 1.45 and profile in ("nuts", "value"):
            return round(min(effective_stack_bb, max(3.0, effective_stack_bb)), 1)
        if spr <= 2.0 and profile in ("semi", "merged"):
            base_factor += 0.10
        target = pot_bb * (base_factor + late_bonus)
        target = max(3.0, target)
    return round(min(effective_stack_bb, target), 1)


def _in_solver_gto_range(hole_cards: list[str], position: str) -> bool:
    if len(hole_cards) != 2:
        return False
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    s1, s2 = _card_suit(hole_cards[0]), _card_suit(hole_cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    pair = r1 == r2
    suited = s1 == s2
    pair_77_plus = pair and hi >= 7
    pair_66_plus = pair and hi >= 6
    pair_55_plus = pair and hi >= 5
    broadway = hi >= 10 and lo >= 10
    suited_connector = suited and abs(r1 - r2) == 1 and hi >= 6
    suited_one_gap = suited and abs(r1 - r2) == 2 and hi >= 9
    named_core = (hi, lo) in ((14, 13), (14, 12), (14, 11), (13, 12))  # AK/AQ/AJ/KQ
    suited_ace_wheel = suited and hi == 14 and lo <= 5
    suited_ace_strong = suited and hi == 14 and lo >= 9
    suited_broadway = suited and broadway

    if position == "BTN":
        return pair_55_plus or broadway or suited_connector or suited_one_gap or suited_ace_wheel or suited_ace_strong
    if position == "CO":
        return pair_55_plus or broadway or suited_connector or suited_ace_wheel or suited_ace_strong
    if position in ("HJ", "SB"):
        return pair_66_plus or named_core or suited_broadway or (suited_connector and hi >= 8) or suited_ace_wheel or suited_ace_strong
    if position == "BB":
        return pair_66_plus or named_core or broadway or (suited_connector and hi >= 7) or suited_ace_wheel
    # UTG
    return pair_77_plus or named_core or (suited_broadway and hi >= 12) or (suited_connector and hi >= 10) or suited_ace_wheel


def _is_garbage_hand(hole_cards: list[str], board_cards: list[str], info: dict) -> bool:
    if len(hole_cards) != 2:
        return True
    if board_cards and info["tier"] >= 2:
        return False
    r1, r2 = _card_rank(hole_cards[0]), _card_rank(hole_cards[1])
    hi, lo = max(r1, r2), min(r1, r2)
    suited = _card_suit(hole_cards[0]) == _card_suit(hole_cards[1])
    connected = abs(r1 - r2) <= 1
    broadway = hi >= 10 and lo >= 10
    small_low = hi <= 9 and lo <= 7
    very_gapped = abs(r1 - r2) >= 4
    return (
        not suited
        and not connected
        and not broadway
        and small_low
        and very_gapped
        and r1 != r2
    )


def _position_aggression_bonus(position: str) -> float:
    bonuses = {
        "BTN": 0.07,
        "CO": 0.05,
        "HJ": 0.03,
        "UTG": 0.00,
        "SB": 0.04,
        "BB": 0.02,
    }
    return bonuses.get(position, 0.0)


def decide_action(
    hole_cards: list[str],
    board_cards: list[str],
    pot_bb: float,
    stack_bb: float,
    to_call_bb: float,
    opponent_stacks_bb: list[float],
    position: str,
) -> Decision:
    hole_cards, board_cards, had_ocr_conflict = _sanitize_ocr_cards(hole_cards, board_cards)
    spr = round(stack_bb / max(pot_bb, 0.1), 2)
    pot_odds = _compute_pot_odds(pot_bb, to_call_bb)
    villain_avg_stack = round(
        (sum(opponent_stacks_bb) / max(1, len(opponent_stacks_bb))) if opponent_stacks_bb else stack_bb,
        1,
    )
    effective_stack_bb = max(5.0, min(stack_bb, villain_avg_stack))
    effective_spr = round(effective_stack_bb / max(pot_bb, 0.1), 2)
    late_position = position in ("BTN", "CO")
    blind_position = position in ("SB", "BB")
    has_complete_hole_cards = len(hole_cards) == 2

    if not has_complete_hole_cards:
        if to_call_bb > 0:
            if to_call_bb <= OCR_UNCERTAIN_CALL_MAX_BB and pot_odds <= 0.24:
                return Decision("call", to_call_bb, "ocr_uncertain_small_pot_defend", 0.20, spr, pot_odds, position)
            if blind_position and to_call_bb <= 0.5 and pot_odds <= 0.14:
                return Decision("call", to_call_bb, "incomplete_cards_blind_price_defend", 0.18, spr, pot_odds, position)
            return Decision("fold", 0.0, "ocr_uncertain_safe_fold", 0.16, spr, pot_odds, position)
        return Decision("check", 0.0, "incomplete_hole_cards_safe_check", 0.16, spr, pot_odds, position)

    info = evaluate_hand(hole_cards, board_cards)
    tier = info["tier"]
    premium_pair = _is_premium_pair(hole_cards)
    green_hand = _is_green_hand(hole_cards)
    wide_open_hand = _should_open_wide_preflop(hole_cards, position)
    good_kicker = _good_kicker(hole_cards)
    gto_open_range = _in_solver_gto_range(hole_cards, position)
    has_flush_draw = info["flush_draw"]
    has_straight_draw = info["straight_draw"]
    has_any_draw = has_flush_draw or has_straight_draw
    draw_hit_prob = _draw_hit_probability(info["draw_outs"], 2 if len(board_cards) <= 3 else 1)
    strong_draw = info["draw_outs"] >= 8 or draw_hit_prob >= 0.24
    top_pair_any = info["top_pair"]
    top_pair_good_kicker = info["top_pair"] and good_kicker
    strong_value_hand = top_pair_good_kicker or tier >= 3 or premium_pair
    short_stack_mode = effective_stack_bb <= 14.0

    equity = _estimate_equity(hole_cards, board_cards, info)
    equity += _position_aggression_bonus(position)
    equity += draw_hit_prob * 0.12
    if top_pair_good_kicker:
        equity = min(0.99, equity + 0.07)
    if has_flush_draw:
        equity = min(0.99, equity + 0.06)
    if premium_pair and not board_cards:
        equity = max(equity, 0.66)
    if late_position and gto_open_range and not board_cards:
        equity = max(equity, 0.56)
    if late_position and wide_open_hand and not board_cards:
        equity = max(equity, 0.50)
    if effective_spr <= 3.0 and (strong_value_hand or has_any_draw):
        equity = min(0.99, equity + 0.03)
    if spr >= 8.0 and tier <= 1 and not has_any_draw:
        equity = max(0.24, equity - 0.02)
    if had_ocr_conflict:
        # Con input OCR conflittuale abbassiamo l'equity stimata per limitare over-aggression.
        equity = max(0.12, equity - 0.06)

    # Preflop GTO: range ampio e più apertura in BTN/CO
    if not board_cards:
        if short_stack_mode and (premium_pair or _is_big_ace_combo(hole_cards) or green_hand or equity >= 0.56):
            jam_size = round(min(stack_bb, effective_stack_bb), 1)
            return Decision("raise", jam_size, "short_stack_gto_push", equity, spr, pot_odds, position)
        if to_call_bb > 0 and (green_hand or equity >= AGGRESSIVE_EQUITY_THRESHOLD):
            reraise_size = _solver_raise_size(
                pot_bb,
                effective_stack_bb,
                effective_spr,
                position,
                "value" if (premium_pair or _is_big_ace_combo(hole_cards)) else "merged",
                to_call_bb=to_call_bb,
                preflop=True,
            )
            return Decision("raise", reraise_size, "aggressive_preflop_reraise", equity, spr, pot_odds, position)
        if gto_open_range or (late_position and wide_open_hand):
            preflop_size = _solver_raise_size(
                pot_bb,
                effective_stack_bb,
                effective_spr,
                position,
                "value" if (premium_pair or _is_big_ace_combo(hole_cards)) else "merged",
                to_call_bb=to_call_bb,
                preflop=True,
            )
            open_reason = "wide_btn_co_open_40pct" if (late_position and wide_open_hand and not gto_open_range) else "gto_preflop_open"
            return Decision("raise", preflop_size, open_reason, equity, spr, pot_odds, position)
        if equity > 0.55:
            preflop_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, "merged", to_call_bb=to_call_bb, preflop=True)
            return Decision("raise", preflop_size, "equity_over_55_preflop_raise", equity, spr, pot_odds, position)
        if to_call_bb > 0:
            if equity < FOLD_EQUITY_THRESHOLD and not green_hand:
                return Decision("fold", 0.0, "fold_preflop_under_28", equity, spr, pot_odds, position)
            defend_threshold = max(
                FOLD_EQUITY_THRESHOLD,
                pot_odds
                - (0.03 if late_position else 0.0)
                - (0.02 if blind_position else 0.0),
            )
            if equity >= defend_threshold:
                if blind_position and to_call_bb <= 1.5:
                    return Decision("call", to_call_bb, "preflop_blind_cheap_defend", equity, spr, pot_odds, position)
                return Decision("call", to_call_bb, "preflop_pot_odds_call", equity, spr, pot_odds, position)
            # hard rule: niente fold sopra 28% senza draw preflop -> flat-call difensivo
            return Decision("call", to_call_bb, "preflop_floor_call_over_28", equity, spr, pot_odds, position)
        if late_position and equity >= 0.34:
            open_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, "probe", preflop=True)
            return Decision("raise", open_size, "late_position_gto_open", equity, spr, pot_odds, position)
        return Decision("check", 0.0, "check_preflop", equity, spr, pot_odds, position)

    postflop_aggressive_action = "raise" if to_call_bb > 0 else "bet"

    # Postflop value line
    if tier >= 3 or top_pair_good_kicker:
        profile = "nuts" if tier >= 5 else "value"
        value_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, profile, preflop=False)
        reason = "value_raise_two_pair_plus" if tier >= 3 else "thin_value_top_pair_good_kicker"
        return Decision(postflop_aggressive_action, value_size, reason, equity, spr, pot_odds, position)
    if top_pair_any:
        profile = "thin" if not top_pair_good_kicker else "merged"
        top_pair_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, profile, preflop=False)
        return Decision(postflop_aggressive_action, top_pair_size, "top_pair_pressure", equity, spr, pot_odds, position)

    # Semi-bluff flush/straight draw
    if has_any_draw:
        if to_call_bb > 0 and not strong_draw and equity < 0.42:
            return Decision("call", to_call_bb, "draw_call_controlled_line", equity, spr, pot_odds, position)
        semi_profile = "merged" if strong_draw else "semi"
        semi_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, semi_profile, preflop=False)
        return Decision(postflop_aggressive_action, semi_size, "aggressive_draw_semi_bluff", equity, spr, pot_odds, position)
    # Postflop: pressione con equity forte
    if equity > 0.55:
        profile = "value" if equity > 0.60 else "merged"
        pressure_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, profile, preflop=False)
        return Decision(postflop_aggressive_action, pressure_size, "equity_over_55_postflop_raise", equity, spr, pot_odds, position)

    garbage_hand = _is_garbage_hand(hole_cards, board_cards, info)
    if to_call_bb > 0 and equity < FOLD_EQUITY_THRESHOLD and garbage_hand and not has_any_draw:
        return Decision("fold", 0.0, "fold_under_28_no_draw", equity, spr, pot_odds, position)

    if to_call_bb > 0:
        raise_threshold = max(POSTFLOP_RAISE_THRESHOLD, pot_odds + 0.10 - (0.03 if late_position else 0.0))
        if equity >= raise_threshold:
            pressure_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, "merged", preflop=False)
            return Decision("raise", pressure_size, "postflop_equity_pressure_raise", equity, spr, pot_odds, position)
        if blind_position and to_call_bb <= 1.2 and equity >= max(0.33, pot_odds - 0.01):
            return Decision("call", to_call_bb, "postflop_blind_cheap_defend", equity, spr, pot_odds, position)
        # hard rule: call over floor, fold only under 28% without draw
        return Decision("call", to_call_bb, "postflop_pot_odds_call", equity, spr, pot_odds, position)

    if late_position and equity >= 0.40:
        steal_size = _solver_raise_size(pot_bb, effective_stack_bb, effective_spr, position, "probe", preflop=False)
        return Decision("bet", steal_size, "late_position_pressure", equity, spr, pot_odds, position)

    return Decision("check", 0.0, "check_back", equity, spr, pot_odds, position)


def _estimate_position(cycle: int, table_index: int) -> str:
    positions = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
    return positions[(cycle + table_index - 1) % len(positions)]


def estimate_context(cycle: int, board_cards: list[str], table_index: int) -> tuple[float, float, float, list[float], str]:
    street = len(board_cards)
    pot_bb = round(2.0 + street * 2.5 + ((cycle + table_index) % 3) * 0.8, 1)
    stack_bb = round(max(25.0, 100.0 - cycle * 3.2 - street * 2.5 - table_index * 1.5), 1)
    to_call_bb = round(min(4.0, pot_bb * 0.34), 1) if (cycle + table_index) % 2 else 0.0
    villain_base = max(20.0, 92.0 - cycle * 2.1 - street * 2.1 - table_index * 1.2)
    opponent_stacks_bb = [
        round(max(12.0, villain_base + offset), 1)
        for offset in (-11.0, -5.0, 0.0, 6.0, 13.0)
    ]
    position = _estimate_position(cycle, table_index)
    return pot_bb, stack_bb, to_call_bb, opponent_stacks_bb, position

def _format_signed_bb(value_bb: float) -> str:
    return f"{value_bb:+.1f}"
_FALLBACK_HOLE_SCENARIOS = [
    ["Ah", "Kh"],
    ["9c", "9d"],
    ["Qs", "Js"],
    ["7h", "6h"],
    ["Ad", "Tc"],
    ["5s", "5c"],
    ["Kc", "Qc"],
    ["8d", "7d"],
]

_FALLBACK_BOARD_SCENARIOS = [
    [],
    ["As", "7d", "2h"],
    ["Qc", "Jh", "3s", "9h"],
    ["Th", "9h", "2c", "Kd", "4h"],
    ["8s", "6s", "2d"],
    ["Kh", "Td", "5c", "3c"],
]


def _fallback_table_state(cycle: int, table_index: int) -> tuple[list[str], list[str]]:
    hole_idx = (cycle + (table_index * 3)) % len(_FALLBACK_HOLE_SCENARIOS)
    board_idx = (cycle + table_index) % len(_FALLBACK_BOARD_SCENARIOS)
    return list(_FALLBACK_HOLE_SCENARIOS[hole_idx]), list(_FALLBACK_BOARD_SCENARIOS[board_idx])


def _decision_to_text(decision: Decision) -> str:
    action = decision.action.lower()
    if action in ("raise", "bet", "call"):
        return f"{action} {decision.amount_bb:.1f}BB ({decision.reason})"
    return f"{action} ({decision.reason})"


def _build_table_output(
    table_index: int,
    active_mark: str,
    hole_cards: list[str],
    board_cards: list[str],
    pot_bb: float,
    to_call_bb: float,
    decision: Decision,
) -> str:
    hole_text = " ".join(hole_cards) if hole_cards else "--"
    board_text = " ".join(board_cards) if board_cards else "--"
    equity_pct = int(round(decision.equity * 100))
    odds_pct = int(round(decision.pot_odds * 100))
    decision_text = _decision_to_text(decision)
    return (
        f"T{table_index}-{active_mark} Hole:{hole_text} Board:{board_text} "
        f"Pot:{pot_bb:.1f}BB ToCall:{to_call_bb:.1f}BB Eq:{equity_pct}% "
        f"Odds:{odds_pct}% SPR:{decision.spr:.1f} -> {decision_text}"
    )

def _execute_single_cycle_action(window_info: dict, decision: Decision) -> bool:
    if not hero_action_buttons_ready(window_info):
        return False
    action = (decision.action or "").strip().lower()
    if action == "check":
        return click_check(window_info)
    if action == "call":
        return click_call(window_info)
    if action == "raise":
        return click_raise(window_info, decision.amount_bb)
    if action == "bet":
        return click_bet(window_info, decision.amount_bb)
    if action == "fold":
        return click_fold(window_info)
    return False

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    warnings.filterwarnings("ignore", message=".*pin_memory.*MPS.*")
    max_cycles_raw = os.getenv("MAX_CYCLES", "").strip()
    max_cycles = int(max_cycles_raw) if max_cycles_raw.isdigit() else 0
    loop_delay_raw = os.getenv("LOOP_DELAY_SECONDS", "0.8").strip()
    try:
        parsed_delay = float(loop_delay_raw)
        # Ultra-fast loop hard-clamped a 0.8-1.0 secondi.
        loop_delay = min(1.0, max(0.8, parsed_delay))
    except ValueError:
        loop_delay = 0.8
    fallback_sim_mode = os.getenv("FALLBACK_SIM_MODE", "0").strip().lower() in ("1", "true", "yes", "on")

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        initialize_easyocr()
    cycle = 0
    initial_stack_bb: float | None = None
    final_stack_bb: float | None = None
    interrupted = False
    table_consistency_states: dict[int, TableConsistencyState] = {}
    try:
        while True:
            cycle += 1
            try:

                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    windows = find_and_activate_poker_windows(max_windows=2)

                if not windows:
                    if fallback_sim_mode:
                        sim_outputs = []
                        tracked_cycle_stack_bb: float | None = None
                        for table_index in range(1, 3):
                            hole_cards, board_cards = _fallback_table_state(cycle, table_index)
                            pot_bb, stack_bb, to_call_bb, opponent_stacks_bb, position = estimate_context(
                                cycle,
                                board_cards,
                                table_index,
                            )
                            if tracked_cycle_stack_bb is None:
                                tracked_cycle_stack_bb = stack_bb
                            decision = decide_action(
                                hole_cards,
                                board_cards,
                                pot_bb,
                                stack_bb,
                                to_call_bb,
                                opponent_stacks_bb,
                                position,
                            )
                            sim_outputs.append(
                                _build_table_output(
                                    table_index=table_index,
                                    active_mark="SIM",
                                    hole_cards=hole_cards,
                                    board_cards=board_cards,
                                    pot_bb=pot_bb,
                                    to_call_bb=to_call_bb,
                                    decision=decision,
                                )
                            )
                        if tracked_cycle_stack_bb is not None:
                            if initial_stack_bb is None:
                                initial_stack_bb = tracked_cycle_stack_bb
                            final_stack_bb = tracked_cycle_stack_bb
                        print(f"Ciclo {cycle} - Nessun tavolo Zoom rilevato -> fallback sim mode || " + " || ".join(sim_outputs))
                    else:
                        print(f"Ciclo {cycle} - Nessun tavolo Zoom rilevato")
                    if cycle % 50 == 0:
                        stack_for_summary = final_stack_bb if final_stack_bb is not None else 0.0
                        profit_for_summary = (
                            stack_for_summary - initial_stack_bb
                            if initial_stack_bb is not None
                            else 0.0
                        )
                        print(
                            f"Ciclo {cycle} - Stack: {stack_for_summary:.1f} BB - "
                            f"Profitto: {_format_signed_bb(profit_for_summary)} BB"
                        )
                    if max_cycles and cycle >= max_cycles:
                        break
                    time.sleep(loop_delay)
                    continue

                table_outputs = []
                tracked_cycle_stack_bb: float | None = None
                action_attempted_this_cycle = False
                for table_index, window_info in enumerate(windows, start=1):
                    table_state = table_consistency_states.setdefault(table_index, TableConsistencyState())
                    hole_cards: list[str] = []
                    board_cards: list[str] = []
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        screenshot_path = take_table_screenshot(
                            window_info,
                            save_path=os.path.join(base_dir, f"table_live_t{table_index}.png"),
                        )
                        if screenshot_path:
                            hole_cards, board_cards = read_table_cards_ocr(window_info, screenshot_path)
                    hole_cards, board_cards, _ = _apply_consistency_filters(table_state, hole_cards, board_cards)

                    pot_bb, stack_bb, to_call_bb, opponent_stacks_bb, position = estimate_context(
                        cycle,
                        board_cards,
                        table_index,
                    )
                    if tracked_cycle_stack_bb is None:
                        tracked_cycle_stack_bb = stack_bb
                    decision = decide_action(
                        hole_cards,
                        board_cards,
                        pot_bb,
                        stack_bb,
                        to_call_bb,
                        opponent_stacks_bb,
                        position,
                    )

                    is_active = bool(window_info.get("is_active", False))
                    if is_active:
                        tracked_cycle_stack_bb = stack_bb
                    if is_active and not action_attempted_this_cycle:
                        action_attempted_this_cycle = _execute_single_cycle_action(window_info, decision)

                    active_mark = "ACTIVE" if is_active else "PASSIVE"
                    table_outputs.append(
                        _build_table_output(
                            table_index=table_index,
                            active_mark=active_mark,
                            hole_cards=hole_cards,
                            board_cards=board_cards,
                            pot_bb=pot_bb,
                            to_call_bb=to_call_bb,
                            decision=decision,
                        )
                    )
                if tracked_cycle_stack_bb is not None:
                    if initial_stack_bb is None:
                        initial_stack_bb = tracked_cycle_stack_bb
                    final_stack_bb = tracked_cycle_stack_bb
                profit_bb = (
                    (final_stack_bb - initial_stack_bb)
                    if (initial_stack_bb is not None and final_stack_bb is not None)
                    else 0.0
                )

                print(f"Ciclo {cycle} - " + " || ".join(table_outputs))
                if cycle % 50 == 0:
                    stack_for_summary = final_stack_bb if final_stack_bb is not None else 0.0
                    print(
                        f"Ciclo {cycle} - Stack: {stack_for_summary:.1f} BB - "
                        f"Profitto: {_format_signed_bb(profit_bb)} BB"
                    )
                if max_cycles and cycle >= max_cycles:
                    break
            except Exception:
                print(f"Ciclo {cycle} - Hole: -- Board: -- Pot: -- - Decisione: check (errore)")
                if cycle % 50 == 0:
                    stack_for_summary = final_stack_bb if final_stack_bb is not None else 0.0
                    profit_for_summary = (
                        stack_for_summary - initial_stack_bb
                        if initial_stack_bb is not None
                        else 0.0
                    )
                    print(
                        f"Ciclo {cycle} - Stack: {stack_for_summary:.1f} BB - "
                        f"Profitto: {_format_signed_bb(profit_for_summary)} BB"
                    )
                if max_cycles and cycle >= max_cycles:
                    break
            time.sleep(loop_delay)
    except KeyboardInterrupt:
        interrupted = True
        print("Bot fermato dall'utente")
    finally:
        if initial_stack_bb is None:
            initial_stack_bb = 0.0
        if final_stack_bb is None:
            final_stack_bb = initial_stack_bb
        net_profit_bb = final_stack_bb - initial_stack_bb
        hands_played = max(cycle, 1)
        winrate_bb_100 = (net_profit_bb / hands_played) * 100.0
        if max_cycles and cycle >= max_cycles and not interrupted:
            print(
                f"Test {max_cycles} cicli completato - Profitto netto: "
                f"{_format_signed_bb(net_profit_bb)} BB - "
                f"Winrate: {winrate_bb_100:.1f} BB/100 mani"
            )
        elif cycle > 0:
            print(
                f"Test interrotto dopo {cycle} cicli - Profitto netto: "
                f"{_format_signed_bb(net_profit_bb)} BB - "
                f"Winrate: {winrate_bb_100:.1f} BB/100 mani"
            )


if __name__ == "__main__":
    main()
