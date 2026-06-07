import os
import platform
import openai
from colorama import init

# Configura Tesseract prima di qualsiasi import che usa pytesseract
if platform.system() == "Darwin":
    _tess = os.path.join(
        os.path.expanduser("~"),
        "miniforge3", "envs", "pokergpt311", "bin", "tesseract"
    )
    if os.path.exists(_tess):
        os.environ.setdefault("TESSERACT_CMD", _tess)

# Import tkinter BEFORE pygame to prevent SDL/tkinter NSInvalidArgumentException on macOS
import tkinter  # noqa: F401 — side-effect import, must precede pygame

from window_manager import get_poker_window
from macos_permissions import ensure_macos_permissions, request_open_privacy_panels

from game_state             import GameState
from gui                    import GUI
from hero_action            import HeroAction
from poker_assistant        import PokerAssistant
from audio_player           import AudioPlayer
from read_poker_table       import ReadPokerTable
from hero_hand_range        import PokerHandRangeDetector
from hero_info              import HeroInfo

def main():

    # Verifica permessi macOS (Accessibility + Screen Recording)
    if platform.system() == "Darwin":
        has_access, has_screen = ensure_macos_permissions(auto_print=True)
        if not has_access or not has_screen:
            print("\nVuoi aprire i pannelli Privacy ora? (s/n): ", end="", flush=True)
            try:
                resp = input().strip().lower()
                if resp == "s":
                    request_open_privacy_panels()
                    print("Pannelli aperti. Concedi i permessi, poi riavvia il programma.")
                    return
            except EOFError:
                pass

    # Ask the user for the hero player number ( 1- 6 , starting from bottom(1))
    while True:
        try:
            hero_player_number = int(input("Enter hero player number (1-6): "))
            if 1 <= hero_player_number <= 6:
                break
            else:
                print("Invalid number. Please enter a number between 1 and 6.")
        except ValueError:
            print("Invalid input. Please enter a number.")

    api_key                 = os.getenv('OPENAI_API_KEY')
    openai_client           = openai.OpenAI(api_key=api_key)
    poker_window            = get_poker_window()
    init(autoreset=True)

    # macOS-first window size feedback (do not force resize on Mac)
    if poker_window is not None:
        if platform.system() == "Darwin":
            print(f"Finestra Poker rilevata: {poker_window.width}x{poker_window.height}")
            if poker_window.width < 850 or poker_window.height < 550:
                print("Suggerimento: ridimensiona manualmente il tavolo a circa 960x690 per maggiore stabilità.")

    # Initialize all the instances
    
    if poker_window is not None:

        audio_player            = AudioPlayer( openai_client )
        hero_action             = HeroAction( poker_window )
        
        hero_info               = HeroInfo()
        hero_hand_range         = PokerHandRangeDetector()

        game_state              = GameState( hero_action, audio_player )
        poker_assistant         = PokerAssistant( openai_client, hero_info, game_state, hero_action, audio_player )
       
        gui                     = GUI( game_state, poker_assistant )
        read_poker_table        = ReadPokerTable( poker_window, hero_info, hero_hand_range, hero_action, poker_assistant, game_state )

        

        setup_read_poker_table( read_poker_table=read_poker_table )

        # Update hero player number in game state
        game_state.update_player(hero_player_number, hero=True)

        game_state.hero_player_number = hero_player_number

        game_state.extract_blinds_from_title()

        # Start the GUI
        gui.run()

def setup_read_poker_table(read_poker_table):

    # Start continuous detection of the poker table
    read_poker_table.start_continuous_detection()

if __name__ == "__main__":
    main()

# Run script:
# python main.py