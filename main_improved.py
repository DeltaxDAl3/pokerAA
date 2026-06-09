#!/usr/bin/env python3
"""
XANDER BOT v2.7 – GTO Range Calibrato
VPIP Target: 18-24% (6-max Cash / Spin & Go)

Range calibrato v2.7:
  UTG ~12.5% | HJ ~18% | CO ~24%
  BTN ~28.5% | SB ~24% | BB ~21%
  Media pesata = ~21.3%
"""
import os
import sys
import time
import random
import signal

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG (da environment variables)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MAX_CYCLES = int(os.getenv("MAX_CYCLES", 50))
SIM_MODE   = int(os.getenv("FALLBACK_SIM_MODE", 1))
MAX_TABLES = int(os.getenv("MAX_TABLES", 1))
DELAY      = 0.8

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CARTE E COSTANTI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RANKS = "23456789TJQKA"
SUITS = "hdcs"
RV    = {r: i + 2 for i, r in enumerate(RANKS)}   # 2..14
RN    = {v: k for k, v in RV.items()}              # reverse
DECK  = [(r, s) for r in RANKS for s in SUITS]
POS   = ["UTG", "HJ", "CO", "BTN", "SB", "BB"]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STATS TRACKING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
stats = {
    "hands": 0, "vpip": 0,
    "raise": 0, "call": 0, "fold": 0,
    "profit_bb": 0.0,
}

shutdown_flag = False


def handle_shutdown(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    print("\n🛑 Shutdown ricevuto – chiusura pulita...")


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DEAL & HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def deal():
    """Pesca 2 carte random dal mazzo."""
    cards = random.sample(DECK, 2)
    v1, v2 = RV[cards[0][0]], RV[cards[1][0]]
    hi, lo = max(v1, v2), min(v1, v2)
    pair   = (v1 == v2)
    suited = (cards[0][1] == cards[1][1])
    gap    = hi - lo
    return cards, hi, lo, pair, suited, gap


def fmt_cards(c):
    return f"{c[0][0]}{c[0][1]} {c[1][0]}{c[1][1]}"


def hand_name(hi, lo, pair, suited):
    if pair:
        return f"{RN[hi]}{RN[lo]}"
    return f"{RN[hi]}{RN[lo]}{'s' if suited else 'o'}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GTO POSITIONAL PREFLOP RANGE v2.7 – calibrato 21.3%
#   UTG ~12.5% | HJ ~18% | CO ~24%
#   BTN ~28.5% | SB ~24% | BB ~21%
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def in_range(pos, hi, lo, pair, suited, gap):
    """Ritorna True se la mano è nel range GTO per la posizione."""
    conn = (gap == 1)

    # ── BTN ~28.5% ──
    # 22+, A2s+, T6s+, 45s+, A9o+, KTo+, QTo+, T9o+
    if pos == "BTN":
        return (
            pair or
            (suited and hi >= 14) or
            (suited and hi >= 10 and lo >= 6) or
            (suited and conn and hi >= 5) or
            (hi >= 14 and lo >= 9) or
            (hi >= 13 and lo >= 10) or
            (hi >= 12 and lo >= 10) or
            (conn and hi >= 10)
        )

    # ── CO ~24% ──
    # 33+, A2s+, T7s+, 56s+, ATo+, KTo+, QTo+
    if pos == "CO":
        return (
            (pair and hi >= 3) or
            (suited and hi >= 14) or
            (suited and hi >= 10 and lo >= 7) or
            (suited and conn and hi >= 6) or
            (hi >= 14 and lo >= 10) or
            (hi >= 13 and lo >= 10) or
            (hi >= 12 and lo >= 10)
        )

    # ── HJ ~18% ──
    # 44+, A2s+, T8s+, 78s+, ATo+, KQo
    if pos == "HJ":
        return (
            (pair and hi >= 4) or
            (suited and hi >= 14) or
            (suited and hi >= 10 and lo >= 8) or
            (suited and conn and hi >= 8) or
            (hi >= 14 and lo >= 10) or
            (hi == 13 and lo >= 12)
        )

    # ── UTG ~12.5% ──
    # 66+, A2s+, QTs+, T9s+, AQo+, KQo
    if pos == "UTG":
        return (
            (pair and hi >= 6) or
            (suited and hi >= 14) or
            (suited and hi >= 12 and lo >= 10) or
            (suited and conn and hi >= 10) or
            (hi >= 14 and lo >= 12) or
            (hi == 13 and lo >= 12)
        )

    # ── SB ~24% ──
    # 33+, A2s+, T7s+, 56s+, ATo+, KTo+, QTo+
    if pos == "SB":
        return (
            (pair and hi >= 3) or
            (suited and hi >= 14) or
            (suited and hi >= 10 and lo >= 7) or
            (suited and conn and hi >= 6) or
            (hi >= 14 and lo >= 10) or
            (hi >= 13 and lo >= 10) or
            (hi >= 12 and lo >= 10)
        )

    # ── BB difesa ~21% ──
    # 22+, A2s+, T7s+, 56s+, ATo+, KQo
    if pos == "BB":
        return (
            pair or
            (suited and hi >= 14) or
            (suited and hi >= 10 and lo >= 7) or
            (suited and conn and hi >= 6) or
            (hi >= 14 and lo >= 10) or
            (hi == 13 and lo >= 12)
        )

    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EQUITY ESTIMATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def estimate_equity(hi, lo, pair, suited, gap):
    """Stima equity preflop vs mano random (semplificata)."""
    if pair:
        base = 50.0 + (hi - 2) * 2.9     # 22≈50%, AA≈85%
    else:
        base = 20.0 + hi * 1.8 + lo * 1.0
        if suited:  base += 3.0
        if gap <= 1: base += 2.0
        if gap <= 2: base += 1.0
        if hi == 14: base += 4.0
    base += random.uniform(-4.0, 4.0)
    return round(min(92.0, max(25.0, base)), 1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GTO DECISION ENGINE (super-aggressivo)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def gto_decision(equity, pos, pot_odds):
    """
    Regole GTO aggressive:
    - Raise:  equity > 55%, oppure > 48% in posizione (BTN/CO)
    - Call:   equity > pot_odds e > 32%
    - Fold:   solo equity < 30% senza draw
    """
    if equity > 55:
        return "raise"
    if equity > 48 and pos in ("BTN", "CO"):
        return "raise"
    if equity > 42:
        return "call"
    if equity >= pot_odds and equity > 32:
        return "call"
    return "fold"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PROFIT / LOSS SIMULATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def calc_profit(action, equity):
    """Simula P/L in BB in base ad azione e equity."""
    win = random.random() * 100 < equity
    if action == "raise":
        return 3.5 if win else -3.0
    if action == "call":
        return 2.0 if win else -2.0
    return 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CALIBRAZIONE RANGE (verifica teorica)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def calibrate(n=50_000):
    """Monte Carlo per verificare VPIP teorico."""
    vpip_count = 0
    for _ in range(n):
        _, hi, lo, pair, suited, gap = deal()
        pos = random.choice(POS)
        if in_range(pos, hi, lo, pair, suited, gap):
            vpip_count += 1
    return vpip_count / n * 100


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN LOOP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    theo_vpip = calibrate(50_000)

    print(f"🚀 XANDER BOT v2.5 – GTO RANGE ENGINE")
    print(f"   Cicli: {MAX_CYCLES} | SimMode: {SIM_MODE} | Tavoli: {MAX_TABLES}")
    print(f"   VPIP teorico: {theo_vpip:.1f}% | Target: 18-24% | Delay: {DELAY}s")
    print("━" * 72)

    for cycle in range(1, MAX_CYCLES + 1):
        if shutdown_flag:
            break

        cards, hi, lo, pair, suited, gap = deal()
        pos = random.choice(POS)
        name = hand_name(hi, lo, pair, suited)
        equity = estimate_equity(hi, lo, pair, suited, gap)
        stats["hands"] += 1

        playable = in_range(pos, hi, lo, pair, suited, gap)

        if playable:
            stats["vpip"] += 1
            pot_odds = round(random.uniform(18, 35), 1)
            action = gto_decision(equity, pos, pot_odds)
        else:
            action = "fold"
            pot_odds = 0.0

        stats[action] += 1
        pl = calc_profit(action, equity)
        stats["profit_bb"] += pl

        vpip_pct = stats["vpip"] / stats["hands"] * 100

        if playable:
            print(
                f"[{cycle:3d}] {pos:3s} | {fmt_cards(cards)} ({name:4s}) | "
                f"Eq:{equity:5.1f}% | PO:{pot_odds:4.1f}% | "
                f"▶ {action.upper():5s} | P/L:{pl:+5.1f}BB | VPIP:{vpip_pct:5.1f}%"
            )
        else:
            print(
                f"[{cycle:3d}] {pos:3s} | {fmt_cards(cards)} ({name:4s}) | "
                f"Eq:{equity:5.1f}% |   ---   | "
                f"▶ FOLD  |  0.0BB | VPIP:{vpip_pct:5.1f}%"
            )

        time.sleep(DELAY)

    # ━━ REPORT FINALE ━━
    t = stats["hands"]
    vpip_f = stats["vpip"] / t * 100 if t else 0
    ok = "✅" if 18 <= vpip_f <= 24 else "⚠️"
    total_actions = stats["raise"] + stats["call"] + stats["fold"]

    print()
    print("━" * 72)
    print("📊 REPORT FINALE")
    print("━" * 72)
    print(f"  Mani giocate:    {t}")
    print(f"  VPIP finale:     {vpip_f:.1f}% {ok}  (teorico: {theo_vpip:.1f}%)")
    print(f"  Profit/Loss:     {stats['profit_bb']:+.1f} BB")
    print(f"  Azioni totali:   {total_actions}")
    print(f"    Raise: {stats['raise']:3d}  |  Call: {stats['call']:3d}  |  Fold: {stats['fold']:3d}")
    print(f"  Mani VPIP:       {stats['vpip']} / {t}")
    print("━" * 72)


if __name__ == "__main__":
    main()
