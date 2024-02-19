#!/usr/bin/env python3

import os
import pty
import select
import sys
import logging
import subprocess
import termios
import tty
import fcntl
import struct
import shutil
from datetime import datetime

# Configure logging with timestamp in the filename
log_file_path = f'adom_log_{datetime.now().strftime("%Y-%m-%d_%H-%M-%S")}.log'
logging.basicConfig(filename=log_file_path, level=logging.DEBUG)

def set_window_size(fd, rows, cols):
    """Set the window size of the terminal."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

def extract_game_name(file_path):
    """Extract the actual game name from the saved game file."""
    with open(file_path, 'rb') as file:
        file.seek(0x10)  # Go to the offset where the game name is stored
        game_name = file.read(12).split(b'\x00', 1)[0].decode('utf-8')  # Read the name and split by null terminator
    return game_name

def display_menu_and_get_choice(backup_dir_base):
    """Display a menu of saved games and return the user's choice."""
    print("Select a game to load:")
    saved_games = [f for f in os.listdir(backup_dir_base) if os.path.isdir(os.path.join(backup_dir_base, f))]
    for index, game in enumerate(saved_games, start=1):
        print(f"{index}. Load game: {game}")
    print("0. Start a new game")
    
    choice = int(input("Enter your choice: "))
    return choice, saved_games

def prepare_game(backup_dir_base, saved_games_dir, choice, saved_games):
    """Prepare the game for launching, either by copying a saved game or doing nothing for a new game."""
    if choice > 0:
        game_to_load = saved_games[choice - 1]
        game_backup_dir = os.path.join(backup_dir_base, game_to_load)
        # Find the latest backup file for the selected game
        latest_backup = max([os.path.join(game_backup_dir, f) for f in os.listdir(game_backup_dir)], key=os.path.getmtime)
        # Copy the selected game back to the ADOM saved games directory
        shutil.copy2(latest_backup, os.path.join(saved_games_dir, os.path.basename(latest_backup)))
        return extract_game_name(latest_backup)  # Return the actual game name to load
    return ""  # Return an empty string for a new game

def main():
    adom_path = os.getenv('ADOM_PATH')
    home_dir = os.getenv('HOME')
    saved_games_dir = os.path.join(home_dir, '.adom.data/savedg')
    backup_dir_base = os.path.join(home_dir, '.adompy.data')

    # Create the backup directory if it does not exist
    os.makedirs(backup_dir_base, exist_ok=True)
    
    if not adom_path:
        adom_path = 'adom'

    choice, saved_games = display_menu_and_get_choice(backup_dir_base)
    game_name_to_load = prepare_game(backup_dir_base, saved_games_dir, choice, saved_games)

    old_settings = termios.tcgetattr(sys.stdin)
    
    try:
        master_fd, slave_fd = pty.openpty()
        set_window_size(master_fd, 25, 80)

        tty.setraw(sys.stdin.fileno())

        # Launch ADOM with the game name as an argument if loading a game
        adom_args = [adom_path]
        if game_name_to_load:
            adom_args += ["-l", game_name_to_load]  # Correctly include "-l" argument
        adom_proc = subprocess.Popen(adom_args, preexec_fn=os.setsid, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)

        while adom_proc.poll() is None:
            r, w, e = select.select([master_fd, sys.stdin], [], [], 0.1)
            if master_fd in r:
                output = os.read(master_fd, 1024)
                sys.stdout.write(output.decode('utf-8'))
                sys.stdout.flush()
            if sys.stdin in r:
                input = os.read(sys.stdin.fileno(), 1024)
                os.write(master_fd, input)
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        os.close(master_fd)
        os.close(slave_fd)

if __name__ == "__main__":
    main()
