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
- Python **3.10+** (uses `match/case` syntax; 3.11 recommended)
- OpenAI API key (GPT-4 access required)
- PokerStars client with a No Limit Hold'em 6-player Cash table open
- macOS (Apple Silicon tested) or Windows 11

## macOS Setup (Apple Silicon — tested and verified)
### 1. Install Miniforge (Python 3.11 environment manager)
```bash
curl -fsSL https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh | bash
```
### 2. Create the project environment
```bash
~/miniforge3/bin/conda create -y -n pokergpt311 python=3.11 pip
~/miniforge3/bin/conda install -y -n pokergpt311 -c conda-forge tesseract opencv
```
### 3. Install Python dependencies
```bash
~/miniforge3/envs/pokergpt311/bin/python -m pip install \
  openai pygetwindow colorama pyobjc pyautogui pygame \
  mss opencv-python-headless numpy pytesseract pillow
```
> Note: use `opencv-python-headless` (not `opencv-python`) to avoid SDL conflicts with pygame.
### 4. Configure your API key
```bash
echo "OPENAI_API_KEY=sk-your-real-key-here" > pokergpt.env
```
Get your key at https://platform.openai.com/api-keys
### 5. Grant macOS permissions
The bot needs **Accessibility** (for mouse clicks) and **Screen Recording** (for pixel capture).
Run once to open the panels automatically:
```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
```
Add your **Terminal / iTerm2** and **Python** to both lists and enable them.
Restart your terminal after granting permissions.
### 6. PokerStars visual setup
- Disable all animations in the table settings
- The bot works best at table size ~960×690 (auto-detected via Quartz on macOS)

![PokerTable2](https://github.com/HarperJonesGPT/PokerGPT/assets/154810617/ba0a7bc5-d2d1-4237-bfd8-015ca2ca14e9)

## Usage
1. Open PokerStars and join a No Limit Hold'em 6-player Cash table
2. From the project directory, run:
```bash
cd /path/to/PokerGPT
set -a && source pokergpt.env && set +a
~/miniforge3/envs/pokergpt311/bin/python main.py
```
3. Enter your player number (1–6, starting from the bottom seat, clockwise)
4. The bot detects the table, reads the game state, and plays automatically

## Windows Setup
1. Install Python 3.11 from https://python.org
2. `pip install -r requirements.txt`
3. Tesseract is bundled in `tesseract/` — no separate install needed
4. Set `OPENAI_API_KEY` in `pokergpt.env` and run `python main.py`

## Project structure
- `main.py` — entry point; permissions check, window detection, orchestration
- `window_manager.py` — cross-platform window detection (Quartz on macOS)
- `read_poker_table.py` — pixel capture, OCR, card/dealer/stack detection threads
- `poker_assistant.py` — GPT-4 game analysis and action decision
- `game_state.py` — game state model (cards, pot, players, betting history)
- `hero_action.py` — mouse automation (click, drag, type bet amounts)
- `gui.py` — real-time Tkinter dashboard
- `audio_player.py` — audio playback via pygame mixer + OpenAI TTS
- `hero_info.py` — hero statistics (VPIP, PFR, 3-bet, aggression)
- `hero_hand_range.py` — pre-flop hand range filter
- `macos_permissions.py` — macOS Accessibility/Screen Recording checker

## Limitations
- Requires PokerStars 6-player No Limit Hold'em Cash table
- OCR accuracy depends on table visual settings (disable animations)
- On macOS the table window cannot be auto-resized; resize manually to ~960×690
- Image reading speed depends on CPU performance

## Contributing

Contributions to PokerGPT are welcome!

## License

This project is licensed under the MIT License - see the `LICENSE.md` file for details.

## Support

I do not provide any further support for this project. If you can't figure it out, it's not for you.
