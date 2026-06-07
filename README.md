# PokerGPT - GPT4 poker bot for Pokerstars

PokerGPT is an advanced online poker-playing bot for Pokerstars(6-player Texas Hold'em, Cash game) that utilizes the OpenAI GPT-4 API for real-time game state analysis and decision-making.
It has built-in GUI to visualize poker data + voice support to playback actions on the table.

![PokerGPT_GUI](https://github.com/HarperJonesGPT/PokerGPT/assets/154810617/8310109b-5086-470b-92ba-81854f132cb2)

## Features

- Real-time detection of game events by reading pixels on the screen.
- Uses Tesseract OCR API to recognize cards, pot sizes, dealer button and all player actions.
- Uses 'gpt-4-1106-preview' to analyze the game data and players in order to take appriopriate action(fold, check, raise, bet etc).
- Advanced GPT-4 prompt engineering for analyzing game states, player exploitation and strategizing.
- Simulates mouse clicks within the Pokerstars client for automated gameplay.

## Prerequisites

- Python 3.11 or higher (Python 3.10+ required because the code uses `match/case` syntax)
- Access to OpenAI API
- Tesseract OCR for text recognition
- PokerStars client

## Installation

1. Clone the repository to your local machine.
2. Create and activate a Python 3.11 virtual environment.
3. Install dependencies:
   - `pip install openai pygetwindow colorama pyobjc pyautogui pygame keyboard mss opencv-python-headless numpy pytesseract pillow`
4. Create/update `pokergpt.env` with your key:
   - `OPENAI_API_KEY=your_api_key_here`
5. Install Tesseract OCR (or provide the executable path via `TESSERACT_CMD`).
   - The app resolves Tesseract in this order: `TESSERACT_CMD` env var, system `PATH`, then bundled `tesseract/` candidates.

## PokerStars client (Visual) setup:
1. Since this bot reads all of the data from the poker client window, you will need to setup the visuals excactly like in this image:
2. Disable all animations for Pokerstars client in the table settings.
![PokerTable2](https://github.com/HarperJonesGPT/PokerGPT/assets/154810617/ba0a7bc5-d2d1-4237-bfd8-015ca2ca14e9)


## Usage

To start the PokerGPT, follow these steps:

1. Open Pokerstars client and ensure it's visible on the screen.
2. Load environment variables and run:
   - `set -a; source pokergpt.env; set +a`
   - `python main.py`
3. Enter your own player number (player numbers start from the bottom of the table and goes clockwise 1(bottom), 2(bottom-left), 3(top-eft), 4(top), 5(top-right), 6(bottom-right))
4. The bot will automatically locate the poker window and start playing based on the GPT-4 strategy analysis.


## Structure

- `audio_player.py`: Handles audio feedback from the bot.
- `game_state.py`: Manages the current state of the game.
- `gui.py`: Provides a graphical user interface for monitoring the bot's actions.
- `hero_action.py`: Contains logic for determining the hero's actions.
- `hero_hand_range.py`: Assesses hand ranges for the hero.
- `hero_info.py`: Collects information about the hero's current state.
- `main.py`: Entry point for running the bot.
- `poker_assistant.py`: Interfaces with OpenAI's API to analyze the game state and decide on actions.
- `read_poker_table.py`: Uses OCR and pixel detection to read the table state.

## Limitations
- Dependant on the Pokerstars client window size (PokerGPT automatically resizes to small window)
- Might not work on all screen resolutions (tested on '1920 x 1080' pixel screen resolution, Windows 11)
- Works only in Pokerstars 6-Player table.
- Image reading(OCR) speed is dependant on your CPU.

## Contributing

Contributions to PokerGPT are welcome!

## License

This project is licensed under the MIT License - see the `LICENSE.md` file for details.

## Support

I do not provide any further support for this project. If you can't figure it out, it's not for you.
