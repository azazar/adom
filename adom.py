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
import re
from datetime import datetime
from time import time
import traceback
import curses

# Configure logging with timestamp in the filename
log_file_path = 'adom_log_{}.log'.format(datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
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

def list_saved_games(backup_dir_base, saved_games_dir):
    """List the saved games and their corresponding modification time."""
    mtime = dict()

    saved_games = [f for f in os.listdir(saved_games_dir) if os.path.isfile(os.path.join(saved_games_dir, f)) and f.endswith('.svg')]

    for game in saved_games:
        mtime[game] = os.path.getmtime(os.path.join(saved_games_dir, game))

    if os.path.isdir(backup_dir_base):
        for game in os.listdir(backup_dir_base):
            if game not in saved_games:
                saved_games.append(game)
                mtime[game] = os.path.getmtime(os.path.join(backup_dir_base, game))

    saved_games.sort(key=lambda x: mtime[x], reverse=True)

    return saved_games

def curses_menu(win, backup_dir_base, saved_games_dir):
    curses.curs_set(0)  # Hide the cursor
    saved_games = list_saved_games(backup_dir_base, saved_games_dir)
    current_selection = 0
    while True:
        win.clear()
        win.addstr("Select a game to load:\n\n")
        for index, game in enumerate(saved_games):
            if index == current_selection:
                win.addstr("{} Load game: {}\n".format(index + 1, game), curses.A_REVERSE)
            else:
                win.addstr("{} Load game: {}\n".format(index + 1, game))
        if current_selection == len(saved_games):
            win.addstr("0. Start a new game\n", curses.A_REVERSE)
        else:
            win.addstr("0. Start a new game\n")
        win.refresh()

        key = win.getch()
        if key == curses.KEY_UP and current_selection > 0:
            current_selection -= 1
        elif key == curses.KEY_DOWN and current_selection < len(saved_games):
            current_selection += 1
        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r'), ord('>')):
            break
        elif key == curses.KEY_EXIT or key == 27 or key == ord('q'):
            return False, False
        elif key == ord('0'):
            return None, None

    if current_selection >= len(saved_games):
        return None, None

    filename = saved_games[current_selection]
    filepath = os.path.join(saved_games_dir, filename)

    if not os.path.isfile(filepath):
        filepath = os.path.join(backup_dir_base, filename)

    return extract_game_name(filepath), filename

def display_menu_and_get_choice(backup_dir_base, saved_games_dir):
    """Wrap curses application to display a menu of saved games and return the user's choice."""
    def curses_wrapper(stdscr):
        return curses_menu(stdscr, backup_dir_base, saved_games_dir)

    return curses.wrapper(curses_wrapper)

TIMEOUT = 0.05  # Define a constant for the user input timeout
SELECT_TIMEOUT = 0.1  # Define a constant for the select timeout

def main():
    adom_path = os.getenv('ADOM_PATH')
    home_dir = os.getenv('ADOM_HOME', os.getenv('HOME'))
    output_buffer = ""  # Create a buffer for the game output
    last_callback_time = time()  # Initialize the last callback time
    saved_games_dir = os.path.join(home_dir, '.adom.data/savedg')
    backup_dir_base = os.path.join(home_dir, '.adompy.data')

    # Create the backup directory if it does not exist
    os.makedirs(backup_dir_base, exist_ok=True)
    
    if not adom_path:
        adom_path = 'adom'

    game_name_to_load, game_filename = display_menu_and_get_choice(backup_dir_base, saved_games_dir)

    if game_name_to_load is False:
        return

    old_settings = termios.tcgetattr(sys.stdin)
    
    state = {
        'save_sequence': False,
        'quit_sequence': False,
        'drinking_sequence': False,
        'restart': True,
        'error': False,
        'drinking_infinite': False,
    }

    while state['restart']:
        state['restart'] = False
        state['start_sequence'] = True

        # Restore game file from backup if it's not in the saved games directory
        if game_name_to_load and game_filename not in os.listdir(saved_games_dir):
            shutil.copyfile(os.path.join(backup_dir_base, game_filename), os.path.join(saved_games_dir, game_filename))

        try:
            master_fd, slave_fd = pty.openpty()

            set_window_size(master_fd, 25, 80)

            tty.setraw(sys.stdin.fileno())

            # Launch ADOM with the game name as an argument if loading a game
            adom_args = [adom_path if adom_path else 'adom']
            if game_name_to_load:
                adom_args += ["-l", game_name_to_load]  # Correctly include "-l" argument

                # Backup the game file before loading it
                shutil.copyfile(os.path.join(saved_games_dir, game_filename), os.path.join(backup_dir_base, game_filename))

            adom_proc = subprocess.Popen(adom_args, preexec_fn=os.setsid, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)

            def callback(output, state):
                """Callback function to be called when the timeout happens."""
                # Strip ANSI sequences and "\x1b(B" sequences from the output using a more concise regular expression
                ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]|\x1b\(B')
                stripped_output = ansi_escape.sub('', output)
                # Trim the entire string
                trimmed_output = stripped_output.strip()
                logging.info("Callback called with output: {}".format(ascii(trimmed_output)))

                if state['start_sequence']:
                    # Send "P" keys when the string ends with "--- Play the Game --- Credits ---"
                    if trimmed_output.endswith("--- Play the Game --- Credits ---"):
                        logging.info("Sending 'P' key to start the game")
                        os.write(master_fd, b'P')
                        return

                    # Close the game ad on start
                    exit_key_match = re.search(r'-+ \[\+\-\] Page up/down -- \[\*\_\] Line up/down -- \[(\w)\] Exit -+', trimmed_output)
                    if exit_key_match:
                        logging.info("Sending '{}' key to close the ad on start".format(exit_key_match.group(1)))
                        exit_key_code = exit_key_match.group(1)
                        os.write(master_fd, exit_key_code.encode())
                        state['start_sequence'] = False
                        return

                # Start save game process
                save_game_match = re.search(r'-+Really save the game\? \[y\/N\]', trimmed_output)
                if save_game_match:
                    logging.info("Sending 'y' key to save the game")
                    os.write(master_fd, b'y')
                    state['save_sequence'] = True
                    return
                
                if state['save_sequence']:
                    logging.info("Checking for save game messages")
                    
                    press_space_match = re.search(r'\[Press SPACE to continue\]', trimmed_output)
                    if press_space_match:
                        logging.info("Sending ' ' key to continue")
                        os.write(master_fd, b' ')
                        return

                    quit_game_match = re.search(r'\[c\] read the credits or\[q\] quit the game\?Your choice:', trimmed_output)
                    if quit_game_match:
                        logging.info("Sending 'q' key to quit the game")
                        os.write(master_fd, b'q')
                        state['save_sequence'] = False
                        return

                    return

                # Message: "Really quit the game? [y/N]"
                quit_game_match = re.search(r'Really quit the game\? \[y\/N\]', trimmed_output)
                if quit_game_match:
                    logging.info("Sending 'y' key to quit the game")
                    os.write(master_fd, b'y')
                    state['quit_sequence'] = True
                    return
            
                if state['quit_sequence']:
                    logging.info("Checking for quit game messages")

                    # Message: "-- [Zz ] Exit ############\r(more)"
                    exit_game_match = re.search(r'-- \[Zz \] Exit #+', trimmed_output)
                    if exit_game_match:
                        logging.info("Sending 'Z' key to close the screen")
                        os.write(master_fd, b'Z')
                        return
                
                    # Message: "[e] exit to the main menu or  [q] quit the game?  Your choice:'"
                    exit_game_match = re.search(r'\[e\] exit to the main menu or  \[q\] quit the game\?  Your choice:', trimmed_output)
                    if exit_game_match:
                        logging.info("Sending 'q' key to quit the game")
                        os.write(master_fd, b'q')
                        state['quit_sequence'] = False
                        return

                if state['quit_sequence']:
                    # Some blocking message with "more" "You sense a certain tension.(more)"
                    more_match = re.search(r'\(more\)', trimmed_output)
                    if more_match:
                        logging.info("Sending ' ' key to continue")
                        os.write(master_fd, b' ')
                        return
                
                # Message: "You see a red pool."
                pool_match = re.search(r'You see a \S+ pool\.', trimmed_output)
                if pool_match:
                    if state['drinking_infinite']:
                        logging.info("Sending 'D' key to drink from the pool")
                        os.write(master_fd, b'D')
                        return
                    else:
                        # Write at the top of the screen
                        sys.stdout.write("\033[0;0HYou see a pool. \"D\" for drinking, F12 for infinite drinking. \r\n")
                        sys.stdout.flush()
                
                # Message: "-Do you want to drink from the pool? [Y/n]"
                drink_pool_match = re.search(r'-+Do you want to drink from the pool\? \[Y\/n\]', trimmed_output)
                if drink_pool_match and game_name_to_load:
                    logging.info("Sending 'Y' key to drink from the pool")
                    os.write(master_fd, b'Y')
                    state['drinking_sequence'] = True
                    return
                
                if state['drinking_sequence']:
                    logging.info("Checking for drinking messages")

                    good_messages = [
                        "You swallow hard", "You feel hot-headed",
                        "You feel bold at the thought of danger", "You feel very lucky",
                        "You feel cool", "You feel lucky", "You are moved by the sheer pleasure of this sip of fluid",
                        "You feel flexible", "Your digestion calms down", "Your eyes tingle for a second",
                        "You hear a voice calling you 'Iceberg'", "You feel totally awake",
                        "You feel very controlled", "Your looks improve", "You feel very self-confident",
                        "Your movements are getting swifter", "You feel studious",
                        "You feel more in touch with the world", "Your senses sharpen",
                        "Your muscles feel stronger", "Your health increases", "Your will seems inflexible",
                        "You feel great about your", "You feel much better",
                        "You feel younger!", "You suddenly remember your early youth", 
                        "You feel slightly strengthened", "Your wounds no longer bleed", 
                        "Your blood seems to cool down", "You suddenly are visible again",
                        "No effect, as far as you notice", "You feel steady", "You feel relieved", 

                    ]

                    bad_messages = [
                        "The pool suddenly dries up.", "You taste bitter bile in your mouth", "You shiver",
                        "You feel like an endangered species", "You become depressive",
                        "You continue the trip on the road to nowhere", "You feel elated", "You sweat",
                        "Your outfit suddenly looks much cleaner", "You suddenly can see yourself",
                        "You feel dizzy for some seconds", "Suddenly you are gone", "You feel translucent",
                        "You feel on air", "You suddenly are visible again", "You feel cheated", 
                        "You feel inflexible", "You feel gnarly", "Your stomach stings painfully",
                        "Your eyes sting for a second", "You feel shocked", "You feel tired", 
                        "You feel shaken", "You suddenly hate the thought of jumping around", 
                        "You are growing a wart", "You feel reserved", "You are getting shaky", 
                        "Thinking seems to get tougher", "You are getting out of touch with everything", 
                        "You seem to get less perceptive", "Your muscles soften", "It seems that you are getting a cold", 
                        "You feel soft-hearted", "You feel bad about your", 
                        "Bah! This liquid is extremely filthy!", "Urgh! Poison!", "You age!", "You feel exhausted", 
                        "You feel corrupted!", "A gush of water hits you!", "Lots of vipers burst out of the pool",
                        "You are hit by lots of water.", "You slip and fall in!", "Suddenly a water elemental rises from the pool!",
                        "Suddenly your ears start to bleed!", "The water is suddenly writhing with snakes!",
                        "You start a trip on the road to nowhere.", "You feel very very bad.",
                        "You hear hissing sounds", "You suddenly hear many hissing sounds!", 
                        "You suddenly hear roaring waves!", "You feel very bad off.",
                        "You suddenly feel like jumping around", "You feel jumpy",  "You sense trouble",
                        "Lots of vipers burst out of the pool."
                    ]

                    neutral_messages = [
                        "Nothing happens.", "The pool bubbles", "Great! Pure dwarven ale!", "Wow! Pure beer!",
                        "The liquid tastes bitter.", "While you drink small waves seem to ripple the otherwise calm surface of the pool.",
                        "Your outfit suddenly looks much cleaner", 
                    ]

                    wish_message = "What do you wish for?"

                    # Check if trimmed_output contains any of the bad messages
                    for message in bad_messages:
                        if message in trimmed_output:
                            os.write(master_fd, b'\n\n\n\n\n\n\n\n\n\n\n\n\nQ')
                            state['drinking_sequence'] = False
                            state['quit_sequence'] = True
                            state['restart'] = True
                            return
                    
                    # Check if trimmed_output contains any of the good messages
                    for message in good_messages:
                        if message in trimmed_output:
                            os.write(master_fd, b'\nS')
                            state['drinking_sequence'] = False
                            state['save_sequence'] = True
                            state['restart'] = True
                            return
                        
                    # Check if trimmed_output contains any of the neutral messages
                    for message in neutral_messages:
                        if message in trimmed_output:
                            state['drinking_sequence'] = False
                            if state['drinking_infinite']:
                                os.write(master_fd, b'D')
                            return
                    
                    if 'A small frog pops up. (more)' in trimmed_output or 'A small frog pops up.(more)' in trimmed_output:
                        os.write(master_fd, b'    ')
                        if state['drinking_infinite']:
                            os.write(master_fd, b'D')
                        return

                    if wish_message in trimmed_output:
                        state['drinking_sequence'] = False
                        return

                    state['drinking_sequence'] = False

            while adom_proc.poll() is None:
                r, w, e = select.select([master_fd, sys.stdin], [], [], SELECT_TIMEOUT)
                if master_fd in r:
                    output = os.read(master_fd, 1024).decode('utf-8')
                    output_buffer += output  # Buffer the output
                    sys.stdout.write(output)
                    sys.stdout.flush()
                if sys.stdin in r:
                    input = os.read(sys.stdin.fileno(), 1024)
                    logging.info("Input: {}".format(ascii(input)))
                    if input == b'\x1b[24~':
                        state['drinking_infinite'] = not state['drinking_infinite']
                        if state['drinking_infinite']:
                            sys.stdout.write("\033[0;0HInfinite drinking: {}\n".format(state['drinking_infinite']))
                            sys.stdout.flush()
                            os.write(master_fd, b'D')
                    else:
                        os.write(master_fd, input)

                # If the timeout has happened and there is output, call the callback function and flush the buffer
                if time() - last_callback_time > TIMEOUT and output_buffer:
                    callback(output_buffer, state)
                    output_buffer = ""
                    last_callback_time = time()

            # Backup the game file after quitting
            if game_name_to_load:
                filepath = os.path.join(saved_games_dir, game_filename)
                if os.path.isfile(filepath):
                    shutil.copyfile(filepath, os.path.join(backup_dir_base, game_filename))

        except Exception as e:
            logging.error("An error occurred: {}".format(e))

            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            traceback.print_exc()

            state['error'] = True
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            os.close(master_fd)
            os.close(slave_fd)

    if not state['error']:
        # Delete log file
        os.remove(log_file_path)

if __name__ == "__main__":
    main()
