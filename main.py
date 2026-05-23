import os
import sys

os.chdir(os.path.join(os.path.dirname(__file__), "discord-bot"))
sys.path.insert(0, os.getcwd())

from bot import main

if __name__ == "__main__":
    main()
